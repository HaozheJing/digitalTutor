"""Family-scoped evidence-backed memory, distinct from authoritative controls.

Corrections retain internal revision history. Deletion redacts memory bodies in
both records and history; original chat/evidence and database backups are outside
this operation. Write receipts contain metadata only, so replay cannot restore text.
"""
from typing import Literal
from datetime import datetime, timedelta

from pydantic import Field, field_validator

from .domain import ID, Positive, Strict, require, timestamp
from .storage import encode, new_id, digest
from .service_context import message_source_digest

READ_ROLES = {'service', 'planner_daily', 'planner_progress', 'memory_daily', 'admin'}


class MemoryRead(Strict):
    query: str | None = Field(default=None,max_length=200)
    subject: Literal['parent','child','family','unknown'] | None = None
    cursor: str | None = Field(default=None,max_length=2048)
    memory_id: ID | None = None
    kind: Literal['stated_preference','reported_fact','service_interpretation'] | None = None
    event_id: ID | None = None
    limit: int = Field(default=20, ge=1, le=50)


class MemoryWrite(Strict):
    subject: Literal['parent','child','family','unknown'] = 'unknown'
    topic: str | None = Field(default=None,min_length=1,max_length=80)
    request_id: ID
    expected_family_version: Positive
    memory_id: ID | None = None
    expected_version: Positive | None = None
    source_message_id: ID
    source_version: Positive = Field(description='来源消息现在的version，解释或发送后须使用工具回执中的最新message版本；不是解释时的旧版本或家庭版本。')
    source_excerpt: str = Field(min_length=1, max_length=2000)
    body: str = Field(min_length=1, max_length=2000)
    kind: Literal['stated_preference', 'reported_fact', 'service_interpretation']
    event_id: ID | None = None
    valid_until: str | None = Field(default=None, description='source_day通常省略，由宿主按来源自然日截止；until必须填写带时区ISO日期时间，如2026-10-04T21:00:00+08:00。不能仅填2026-10-04。')
    temporal_scope: Literal['source_day','until','persistent'] = Field(default='source_day', description='默认只在来源自然日有效。今天/生病/本次等临时情况用source_day或until并指定截止；持续偏好须有原话依据才显式persistent。不是提醒暂停或解除权限。')

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


def select_memories(app, family, event_id=None, limit=20, *, cursor=None, memory_id=None, kind=None, query=None, subject=None):
    """Caller holds transaction. Latest corrections, never historical versions."""
    from .daily_memory import valid_memory,coverage
    from .recall import matches
    rows = [m for m in app.store.list('memory', family)
            if valid_memory(app,family,m) and m.get('event_id') in (None, event_id)
            and (subject is None or m.get('subject','unknown')==subject)
            and matches(query,m.get('body','')+' '+m.get('topic',''))]
    rows.sort(key=lambda m: (m['updated_at'], m['id']), reverse=True)
    from .memory_paging import page
    return {**page(rows,family,event_id,limit,cursor,memory_id,kind),
            'coverage':coverage(app,family) if getattr(app,'context_mode',None)=='daily_native' else None,
            'temporal_contract':'occurred_at是来源时间，不是当前状态。有效期也不证明情况持续至今；结合更新来信。一次请假不是持续偏好，新答案不能被旧的稍后回复请求覆盖。历史无temporal_scope的记忆不得据此断言当前尚未听课。'}


def read_memory(app, token, args):
    cmd = MemoryRead.model_validate(args)
    with app.store.transaction():
        family, binding = app.auth(token, READ_ROLES)
        if cmd.event_id:
            app.event(family, cmd.event_id)
        result={**select_memories(app, family, cmd.event_id, cmd.limit,cursor=cmd.cursor,memory_id=cmd.memory_id,kind=cmd.kind,query=cmd.query,subject=cmd.subject),
                'deletion_receipts':deletion_receipts(app,family,memory_id=cmd.memory_id),
                'deletion_contract':'删除回执只证明对应来源请求已执行。当前无有效记忆不等于从未保存；不得重新推断或恢复已删正文。'}
        from .workflow import enabled
        if enabled(app) and binding.get('role')=='memory_daily':
            from .daily_memory import bound
            _,_,batch=bound(app,token,binding['memory_batch_id'])
            seen=batch.get('reviewed_memories',{}) if batch.get('memory_review_run_id')==binding.get('run_id') else {}
            seen.update({m['id']:m['version'] for m in result['memories']})
            batch.update(reviewed_memories=seen,memory_review_run_id=binding.get('run_id'))
            app.store.put('memory_batch',batch['id'],family,batch,batch['version'])
        return result


def deletion_receipts(app,family,*,today=False,memory_id=None):
    from .business_time import business_day
    rows=[m for m in app.store.list('memory',family) if m.get('status')=='deleted'
          and (memory_id is None or m['id']==memory_id)
          and (not today or business_day(m['deleted_at'])==business_day(app.clock.now()))]
    rows.sort(key=lambda m:(m['deleted_at'],m['id']),reverse=True)
    return [{k:m[k] for k in ('id','status','deleted_at','deletion_source_message_id') if k in m} for m in rows[:20]]


def save_memory(app, token, args):
    cmd = MemoryWrite.model_validate(args)

    def action(family, binding):
        app.check_version(family, cmd.expected_family_version)
        source = _source(app, family, cmd)
        require(cmd.source_excerpt in source['body'], 'EVIDENCE_MISSING', '摘录必须出自来源消息原文')
        require(cmd.temporal_scope!='until' or cmd.valid_until is not None,
                'MEMORY_TIME_REQUIRED','until记忆必须指定有效截止')
        from .business_time import expiry
        valid_until=expiry(cmd.temporal_scope, cmd.valid_until, source['occurred_at'])
        if cmd.event_id:
            app.event(family, cmd.event_id)
        require((cmd.memory_id is None) == (cmd.expected_version is None),
                'INVALID_SCHEMA', '更正必须同时提供记忆ID和版本')
        mid = cmd.memory_id or new_id('memory')
        old = None
        if cmd.memory_id:
            old = app.store.get('memory', mid, family)
            require(old['status'] == 'active', 'MEMORY_DELETED', '删除的记忆不能被更正恢复')
            require(old['source_message_id'] != cmd.source_message_id,
                    'EVIDENCE_MISSING', '更正需要新的消息依据')
            require(timestamp(source['occurred_at']) >= timestamp(old['occurred_at']),
                    'MEMORY_SOURCE_OUTDATED', '迟到旧消息不能覆盖较新记忆；需核实后用新消息更正')
        row = app.store.put('memory', mid, family, {
            'subject':cmd.subject if cmd.subject!='unknown' or not old else old.get('subject','unknown'),
            'topic':cmd.topic or (old or {}).get('topic',''),
            'topic_key':digest([cmd.subject if cmd.subject!='unknown' or not old else old.get('subject','unknown'),cmd.topic.strip().casefold()]) if cmd.topic else (old or {}).get('topic_key'),
            'source_refs':[{'id':source['id'],'digest':message_source_digest(source)}],
            'body': cmd.body, 'kind': cmd.kind, 'event_id': cmd.event_id,
            'source_message_id': source['id'], 'source_version': source['version'],
            'source_excerpt': cmd.source_excerpt, 'occurred_at': source['occurred_at'],
            'updated_at': app.clock.iso(), 'valid_until': valid_until, 'temporal_scope':cmd.temporal_scope,
            'status': 'active', 'certainty': 'interpretation' if cmd.kind == 'service_interpretation' else 'user_reported',
        }, cmd.expected_version)
        from .daily_memory import invalidate
        invalidate(app,family,old)
        from .preference_review import request_review
        review=request_review(app,family,source,row,binding)
        affected=[{'id':e['id'],'version':e['version']} for e in app.store.list('event',family)
                  if e.get('status') not in ('done','cancelled') and e.get('next_action')]
        return {'memory_id': mid, 'version': row['version'], 'status': row['status'],
                'temporal_scope':row['temporal_scope'],'valid_until':row['valid_until'],
                'active_event_refs':affected,
                'contact_preference_review':review,
                'planning_followup':'若本条明确更正联系时间，检查这些原事项；用来源消息提交progress.submit调整现有next_action.execute_at，不只更新记忆，不新建目标，不向家庭播报内部交接。'}

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
        from .daily_memory import invalidate
        invalidate(app,family,old,deleting=True)
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
