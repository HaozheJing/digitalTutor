"""Stage-one business core. Model proposals never write records directly."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

from .domain import (
    SHANGHAI, BusinessError, require, timestamp, DailyCommit, ProgressCommit,
    Report, Inbound, Interpret, ControlCommand, PendingInput,
)
from .storage import Store, encode, digest, new_id
from .planning import PlanningMixin


from .runtime_config import Clock, validate_mode


class Application(PlanningMixin):
    def __init__(self, path, clock=None, service_start=8, service_end=21, *, mode='full', strict_runtime=False, runtime_clock=None):
        require(0 <= service_start < service_end <= 24, 'INVALID_CONFIG', '服务窗口配置无效')
        self.store = Store(path)
        self.clock = clock or Clock()
        self.mode = validate_mode(mode)
        self.strict_runtime = strict_runtime
        self.runtime_clock = runtime_clock or (Clock() if strict_runtime else None)
        self.service_start, self.service_end = service_start, service_end

    def close(self):
        if hasattr(self, 'channel'):
            self.channel.close()
        self.store.close()

    def create_family(self, family_id, uid, recipient, mock=True):
        """Trusted bootstrap, deliberately not exposed in model tool registry."""
        require(all(isinstance(x, str) and x for x in (family_id, uid, recipient)), 'INVALID_SCHEMA', '身份必须是非空字符串')
        require(mock is True, 'REAL_CHANNEL_DISABLED', '第一阶段只允许模拟渠道家庭')
        with self.store.transaction() as db:
            old = db.execute('SELECT * FROM families WHERE id=?', (family_id,)).fetchone()
            if old:
                require(old['uid'] == uid and old['recipient'] == recipient, 'IDEMPOTENCY_CONFLICT', '家庭绑定不一致')
            else:
                db.execute('INSERT INTO families VALUES(?,?,?,?,?)', (family_id, uid, recipient, 1, 1))
        return self.store.family(family_id)

    def open_session(self, family_id, role, report_id=None, revision=None):
        """Trusted host creates capabilities; callers cannot pass roles to tools."""
        require(role in {'service', 'planner_daily', 'planner_progress', 'channel', 'admin'}, 'ROLE_FORBIDDEN', '未知角色')
        with self.store.transaction() as db:
            binding = {'mode': self.mode, 'family_version': self.store.family(family_id)['version'],
                       'event_versions': {e['id']: e['version'] for e in self.store.list('event', family_id)}}
            if role == 'planner_progress':
                report = self.store.get('report', self.report_key(family_id, report_id, revision), family_id)
                require(report['resolution'] is None, 'REPORT_CONSUMED', '报告版本已处理或被替代')
                binding.update(report_id=report_id, revision=revision, event_ids=report['event_ids'])
            token = new_id('cap')
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,1)', (hashlib.sha256(token.encode()).hexdigest(), family_id, role, encode(binding)))
        return token

    def revoke_session(self, token):
        with self.store.transaction() as db:
            db.execute('UPDATE sessions SET active=0 WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def auth(self, token, roles, allow_inactive=False):
        row = self.store.connection.execute('SELECT * FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        require(row is not None, 'ROLE_FORBIDDEN', '未授权会话')
        require(row['role'] in roles, 'ROLE_FORBIDDEN', '此角色不能使用该工具')
        require(allow_inactive or row['active'], 'RUN_REVOKED', '运行权限已撤销')
        binding = json.loads(row['binding'])
        if not allow_inactive and binding.get('lease'):
            from .jobs import validate_lease
            validate_lease(self, row['family_id'], binding['lease'])
        return row['family_id'], binding

    def write(self, token, roles, command, payload, callback):
        """Replay before freshness checks; receipt and all effects commit together."""
        with self.store.transaction() as db:
            family, binding = self.auth(token, roles, allow_inactive=True)
            key = payload['request_id']
            hashed = digest({'command': command, 'payload': payload})
            old = db.execute('SELECT * FROM receipts WHERE family_id=? AND request_id=?', (family, key)).fetchone()
            if old:
                require(old['digest'] == hashed, 'IDEMPOTENCY_CONFLICT', '相同请求身份不能携带不同内容')
                result = json.loads(old['result'])
                return {**result, 'outcome': 'duplicate'}
            self.auth(token, roles)
            data = callback(family, binding)
            version = self.store.bump(family)
            result = {'request_id': key, 'outcome': 'applied', 'data': data,
                      'record_versions': {'family_input_version': version}, 'error': None}
            db.execute('INSERT INTO receipts VALUES(?,?,?,?)', (family, key, hashed, encode(result)))
            return result

    def check_version(self, family, expected):
        require(self.store.family(family)['version'] == expected, 'STALE_INPUT', '读取后家庭相关输入已变化')

    def check_planner_snapshot(self, binding, expected):
        require(binding['family_version'] == expected, 'STALE_INPUT', '提议必须使用此运行绑定的快照')

    @staticmethod
    def report_key(family, report_id, revision):
        return encode([family, report_id, revision])

    def event(self, family, event_id):
        event = self.store.get('event', event_id, family)
        count = self.store.connection.execute('SELECT count(*) FROM report_links WHERE family_id=? AND event_id=? AND resolved=0', (family, event_id)).fetchone()[0]
        event['update_pending'] = bool(count)
        return event

    def state(self, token):
        with self.store.transaction():
            family, binding = self.auth(token, {'service', 'planner_daily', 'planner_progress', 'admin'})
            events = [self.event(family, e['id']) for e in self.store.list('event', family)]
            return {'family_version': self.store.family(family)['version'], 'events': events,
                    'controls': self.store.list('control', family), 'plans': self.store.list('plan', family),
                    'reports': self.store.list('report', family), 'pending_inputs': self.store.list('pending', family),
                    'messages': self.store.list('message', family), 'operations': self.store.list('operation', family),
                    'now': self.clock.iso()}

    def validate_refs(self, family, refs):
        result = []
        for ref in refs:
            if hasattr(ref, 'model_dump'):
                ref = ref.model_dump()
            rec = self.store.get(ref['kind'], ref['id'], family)
            require(rec['version'] == ref['version'], 'EVIDENCE_SUPERSEDED', '证据版本已变化')
            result.append((ref['kind'], rec))
        return result

    def ingest_message(self, token, args):
        cmd = Inbound.model_validate(args)
        payload = {'request_id': 'inbound:' + cmd.external_id, **cmd.model_dump()}

        def action(family, _):
            msg_id = new_id('msg')
            row = self.store.put('message', msg_id, family, {
                **cmd.model_dump(), 'direction': 'inbound', 'received_at': self.clock.iso(),
                'interpreted': False, 'intents': [], 'source_kind': 'mock_wechat'})
            self.store.connection.execute('INSERT INTO message_index(message_id,family_id,external_id,direction) VALUES(?,?,?,?)', (msg_id, family, cmd.external_id, 'inbound'))
            self.store.put('job', 'message:' + msg_id, family, {'kind': 'service', 'message_id': msg_id, 'status': 'ready'})
            from .jobs import yield_to_inbound
            yield_to_inbound(self, family, msg_id)
            return row
        return self.write(token, {'channel', 'admin'}, 'ingest_message', payload, action)

    def interpret(self, token, args):
        cmd = Interpret.model_validate(args)

        def action(family, _):
            msg = self.store.get('message', cmd.message_id, family)
            require(msg['direction'] == 'inbound', 'INVALID_SCHEMA', '仅解释入站消息')
            require(msg['version'] == cmd.expected_version, 'STALE_INPUT', '消息版本已变化')
            require(not msg['interpreted'], 'ALREADY_INTERPRETED', '已有意图不能被覆盖或吞掉')
            ids = [i.intent_id for i in cmd.intents]
            require(len(ids) == len(set(ids)), 'INVALID_SCHEMA', '意图ID重复')
            msg['intents'] = [{**i.model_dump(), 'reply_operation_refs': []} for i in cmd.intents]
            msg['interpreted'] = True
            return self.store.put('message', cmd.message_id, family, msg, msg['version'])
        return self.write(token, {'service'}, 'interpret', cmd.model_dump(), action)

    def apply_control(self, token, args):
        cmd = ControlCommand.model_validate(args)

        def action(family, _):
            msg = self.store.get('message', cmd.source_message_id, family)
            require(msg['direction'] == 'inbound' and msg['version'] == cmd.source_version, 'EVIDENCE_SUPERSEDED', '控制来源必须是当前入站消息')
            if cmd.event_id:
                self.event(family, cmd.event_id)
            if cmd.release_control_id:
                control = self.store.get('control', cmd.release_control_id, family)
                require(control['status'] == 'active', 'INVALID_CONTROL', '暂停已解除')
                require(control['source_message_id'] != cmd.source_message_id, 'EVIDENCE_MISSING', '解除需要新消息依据')
                require(control['event_id'] == cmd.event_id and set(control['action_kinds']) == set(cmd.action_kinds), 'INVALID_CONTROL', '解除范围不一致')
                control.update(status='released', released_at=self.clock.iso(), release_source_message_id=cmd.source_message_id)
                return self.store.put('control', control['id'], family, control, control['version'])
            return self.store.put('control', new_id('control'), family, {
                **cmd.model_dump(exclude={'request_id', 'release_control_id'}), 'status': 'active', 'effective_at': self.clock.iso()})
        return self.write(token, {'service'}, 'apply_control', cmd.model_dump(), action)

    def record_pending(self, token, args):
        cmd = PendingInput.model_validate(args)

        def action(family, _):
            msg = self.store.get('message', cmd.source_message_id, family)
            require(msg['version'] == cmd.source_version and msg['direction'] == 'inbound', 'EVIDENCE_SUPERSEDED', '来源消息无效')
            return self.store.put('pending', new_id('pending'), family, {
                **cmd.model_dump(exclude={'request_id'}), 'raised_at': msg['occurred_at'],
                'disposition': 'pending', 'event_id': None})
        return self.write(token, {'service'}, 'record_pending', cmd.model_dump(), action)

    def submit_report(self, token, args):
        cmd = Report.model_validate(args)

        def action(family, binding):
            require(len(set(cmd.event_ids)) == len(cmd.event_ids), 'INVALID_SCHEMA', '关联事项重复')
            for eid in cmd.event_ids:
                self.event(family, eid)
            self.validate_refs(family, cmd.evidence_refs)
            key = self.report_key(family, cmd.report_id, cmd.revision)
            existing = self.store.connection.execute("SELECT data FROM records WHERE kind='report' AND id=?", (key,)).fetchone()
            canonical = cmd.model_dump(exclude={'request_id'})
            if existing:
                saved = json.loads(existing['data'])
                require(saved['payload'] == canonical, 'IDEMPOTENCY_CONFLICT', '报告身份的载荷不可变')
                return self.store.get('report', key, family)
            if cmd.revision > 1:
                require(cmd.supersedes_revision == cmd.revision - 1, 'INVALID_REVISION', '报告修订必须连续且声明替代')
                oldkey = self.report_key(family, cmd.report_id, cmd.supersedes_revision)
                old = self.store.get('report', oldkey, family)
                require(set(old['event_ids']) == set(cmd.event_ids), 'PROGRESS_SCOPE_VIOLATION', '同报告修订不能改变关联事项范围')
                require(set(old['payload']['facts']).issubset(cmd.facts), 'REPORT_FACT_LOSS', '修订必须保留旧事实')
                if old['resolution'] is None:
                    old['resolution'] = 'superseded'
                    self.store.put('report', oldkey, family, old, old['version'])
                    self.store.connection.execute('UPDATE report_links SET resolved=1 WHERE report_key=?', (oldkey,))
            else:
                require(cmd.supersedes_revision is None, 'INVALID_REVISION', '首版不能声明旧修订')
            saved = self.store.put('report', key, family, {'payload': canonical, 'event_ids': cmd.event_ids,
                'report_id': cmd.report_id, 'revision': cmd.revision, 'resolution': None,
                'created_at': self.clock.iso(), 'origin_run_id': binding.get('run_id')})
            for eid in cmd.event_ids:
                self.store.connection.execute('INSERT INTO report_links VALUES(?,?,?,0)', (key, eid, family))
            self.store.put('job', 'progress:' + key, family, {'kind': 'progress', 'report_key': key, 'status': 'ready'})
            return saved
        return self.write(token, {'service'}, 'submit_report', cmd.model_dump(), action)

    def add_evidence(self, token, evidence_id, data):
        """Trusted fixture/adapter entry; not exposed to service/planning models."""
        with self.store.transaction():
            family, _ = self.auth(token, {'admin'})
            require(data.get('kind') in {'lesson_completed', 'cancel', 'accept_change', 'correction'}, 'INVALID_SCHEMA', '不支持证据类型')
            refs = data.get('source_refs', [])
            self.validate_refs(family, refs)
            saved = self.store.put('evidence', evidence_id, family, {**data, 'trusted': True})
            self.store.bump(family)
            return saved

