"""Family-scoped evidence-backed memory, distinct from authoritative controls.

Corrections retain internal revision history. Deletion redacts memory bodies in
both records and history; original chat/evidence and database backups are outside
this operation. Write receipts contain metadata only, so replay cannot restore text.
"""
from typing import Literal

from pydantic import Field, field_validator

from .domain import ID, Positive, Strict, require, timestamp
from .storage import encode, new_id

READ_ROLES = {'service', 'planner_daily', 'planner_progress', 'admin'}


class MemoryRead(Strict):
    event_id: ID | None = None
    limit: int = Field(default=20, ge=1, le=50)


class MemoryWrite(Strict):
    request_id: ID
    expected_family_version: Positive
    memory_id: ID | None = None
    expected_version: Positive | None = None
    source_message_id: ID
    source_version: Positive
    source_excerpt: str = Field(min_length=1, max_length=2000)
    body: str = Field(min_length=1, max_length=2000)
    kind: Literal['stated_preference', 'reported_fact', 'service_interpretation']
    event_id: ID | None = None
    valid_until: str | None = None

    @field_validator('valid_until')
    @classmethod
    def validate_time(cls, value):
        if value is not None:
            timestamp(value)
        return value


class MemoryDelete(Strict):
    request_id: ID
    expected_family_version: Positive
    memory_id: ID
    expected_version: Positive
    source_message_id: ID
    source_version: Positive


def _source(app, family, cmd):
    message = app.store.get('message', cmd.source_message_id, family)
    require(message['direction'] == 'inbound' and message['version'] == cmd.source_version,
            'EVIDENCE_SUPERSEDED', '记忆依据必须是当前家庭的有效入站消息')
    return message


def select_memories(app, family, event_id=None, limit=20):
    """Caller holds transaction. Latest corrections, never historical versions."""
    rows = [m for m in app.store.list('memory', family)
            if m['status'] == 'active' and m.get('event_id') in (None, event_id)
            and (not m.get('valid_until') or timestamp(m['valid_until']) > app.clock.now())]
    rows.sort(key=lambda m: (m['updated_at'], m['id']), reverse=True)
    return {'memories': rows[:limit], 'has_more': len(rows) > limit,
            'matching_count': len(rows), 'authoritative_for_controls': False}


def read_memory(app, token, args):
    cmd = MemoryRead.model_validate(args)
    with app.store.transaction():
        family, _ = app.auth(token, READ_ROLES)
        if cmd.event_id:
            app.event(family, cmd.event_id)
        return select_memories(app, family, cmd.event_id, cmd.limit)


def save_memory(app, token, args):
    cmd = MemoryWrite.model_validate(args)

    def action(family, _):
        app.check_version(family, cmd.expected_family_version)
        source = _source(app, family, cmd)
        require(cmd.source_excerpt in source['body'], 'EVIDENCE_MISSING', '摘录必须出自来源消息原文')
        if cmd.event_id:
            app.event(family, cmd.event_id)
        require((cmd.memory_id is None) == (cmd.expected_version is None),
                'INVALID_SCHEMA', '更正必须同时提供记忆ID和版本')
        mid = cmd.memory_id or new_id('memory')
        if cmd.memory_id:
            old = app.store.get('memory', mid, family)
            require(old['status'] == 'active', 'MEMORY_DELETED', '删除的记忆不能被更正恢复')
            require(old['source_message_id'] != cmd.source_message_id,
                    'EVIDENCE_MISSING', '更正需要新的消息依据')
            require(timestamp(source['occurred_at']) >= timestamp(old['occurred_at']),
                    'MEMORY_SOURCE_OUTDATED', '迟到旧消息不能覆盖较新记忆；需核实后用新消息更正')
        row = app.store.put('memory', mid, family, {
            'body': cmd.body, 'kind': cmd.kind, 'event_id': cmd.event_id,
            'source_message_id': source['id'], 'source_version': source['version'],
            'source_excerpt': cmd.source_excerpt, 'occurred_at': source['occurred_at'],
            'updated_at': app.clock.iso(), 'valid_until': cmd.valid_until,
            'status': 'active', 'certainty': 'interpretation' if cmd.kind == 'service_interpretation' else 'user_reported',
        }, cmd.expected_version)
        return {'memory_id': mid, 'version': row['version'], 'status': row['status']}

    return app.write(token, {'service'}, 'memory.save', cmd.model_dump(), action)


def delete_memory(app, token, args):
    cmd = MemoryDelete.model_validate(args)

    def action(family, _):
        app.check_version(family, cmd.expected_family_version)
        source = _source(app, family, cmd)
        old = app.store.get('memory', cmd.memory_id, family)
        require(old['status'] == 'active', 'MEMORY_DELETED', '记忆已经删除')
        tombstone = {'status': 'deleted', 'deleted_at': app.clock.iso(),
                     'deletion_source_message_id': source['id'],
                     'deletion_source_version': source['version']}
        row = app.store.put('memory', old['id'], family, tombstone, cmd.expected_version)
        app.store.connection.execute(
            "UPDATE history SET data=? WHERE kind='memory' AND id=? AND family_id=?",
            (encode(tombstone), old['id'], family))
        return {'memory_id': old['id'], 'version': row['version'], 'status': 'deleted',
                'redacted': ['current_memory', 'memory_revision_history'],
                'retained': ['original_messages', 'source_evidence', 'database_backups']}

    return app.write(token, {'service'}, 'memory.delete', cmd.model_dump(), action)


SPECS = {
    'memory.read': (READ_ROLES, MemoryRead, '读取当前家庭有效个性化记忆；不会替代暂停或事项状态'),
    'memory.save': ({'service'}, MemoryWrite, '依据当前入站消息保存或更正记忆；更正需要新消息'),
    'memory.delete': ({'service'}, MemoryDelete, '删除记忆正文及记忆历史正文；不删除原始聊天或备份'),
}
HANDLERS = {'memory.read': read_memory, 'memory.save': save_memory, 'memory.delete': delete_memory}
