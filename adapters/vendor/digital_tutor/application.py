"""Stage-one business core. Model proposals never write records directly."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os

from .domain import (
    SHANGHAI, BusinessError, require, timestamp, DailyCommit, ProgressCommit,
    Report, Inbound, Interpret, ControlCommand, PendingInput,
)
from .storage import Store, encode, digest, new_id
from .planning import PlanningMixin
from .control_scope import control_event_id


from .runtime_config import Clock, validate_mode


class Application(PlanningMixin):
    def __init__(self, path, clock=None, service_start=8, service_end=21, *, mode='full', strict_runtime=False, runtime_clock=None):
        require(0 <= service_start < service_end <= 24, 'INVALID_CONFIG', '服务窗口配置无效')
        self.store = Store(path)
        self.clock = clock or Clock()
        self.mode = validate_mode(mode)
        self.context_mode = os.environ.get('DIGITAL_TUTOR_CONTEXT_MODE','rebuild_compact')
        self.steering_enabled = os.environ.get('DIGITAL_TUTOR_STEERING') == '1'
        self.workflow_enabled = os.environ.get('DIGITAL_TUTOR_WORKFLOW') == '1'
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

    def open_session(self, family_id, role, report_id=None, revision=None, planning_reason='baseline', pending_id=None):
        """Trusted host creates capabilities; callers cannot pass roles to tools."""
        require(role in {'service', 'planner_daily', 'planner_progress', 'memory_daily', 'channel', 'admin'}, 'ROLE_FORBIDDEN', '未知角色')
        with self.store.transaction() as db:
            candidate_binding = {}
            candidates = []
            if role == 'planner_daily' and self.strict_runtime:
                from .candidates import refresh_candidates, snapshot_sources
                candidates = refresh_candidates(self, family_id)
                candidate_binding = {'candidate_versions': {c['id']:c['version'] for c in candidates if c['status']=='ready'},
                                     'candidate_source_states': snapshot_sources(self, family_id)}
            binding = {'mode': self.mode, 'role': role, 'family_version': self.store.family(family_id)['version'],
                       'event_versions': {e['id']: e['version'] for e in self.store.list('event', family_id)}}
            binding.update(candidate_binding)
            if role == 'planner_daily':
                require(planning_reason in {'baseline', 'admission','source_refresh'}, 'PLANNING_SCOPE', '未知规划触发')
                binding['planning_reason'] = planning_reason
                if planning_reason=='source_refresh':
                    from .lab_sources import pending_candidates
                    binding['candidate_versions']={c['id']:c['version'] for c in pending_candidates(self,family_id)}
                if planning_reason == 'admission':
                    pending = self.store.get('pending', pending_id, family_id)
                    require(pending.get('planning_requested') and pending['disposition'] == 'pending',
                            'ADMISSION_NOT_PENDING', '无当前待接纳需求')
                    binding.update(pending_id=pending_id, pending_version=pending['version'],
                                   decision_revision=pending.get('decision_revision', 1))
                    scoped = {c['id']: c['version'] for c in candidates
                              if c['status'] == 'ready' and c.get('pending_id') == pending_id}
                    binding['candidate_versions'] = scoped
                plans = [p for p in self.store.list('plan', family_id)
                         if p['business_date'] == self.clock.now().date().isoformat()]
                binding['plan_revision'] = plans[0].get('revision', 1) if plans else 0
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
            from .steering import require_current
            require_current(self,family,binding)
            before = self.store.record_versions(family)
            data = callback(family, binding)
            version = self.store.bump(family)
            result = {'request_id': key, 'outcome': 'applied', 'data': data,
                      'record_versions': {'family_input_version': version,
                          'changed_records': self.store.changed_records(family, before)}, 'error': None}
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
            result = {'family_version': self.store.family(family)['version'], 'events': events,
                    'controls': self.store.list('control', family), 'plans': self.store.list('plan', family),
                    'reports': self.store.list('report', family), 'pending_inputs': self.store.list('pending', family),
                    'messages': self.store.list('message', family), 'operations': self.store.list('operation', family),
                    'now': self.clock.iso()}
            if self.strict_runtime and binding.get('role') == 'service':
                from .execution_projection import project_service_state
                return project_service_state(self, family, result, binding)
            return result

    def validate_refs(self, family, refs):
        result = []
        for ref in refs:
            if hasattr(ref, 'model_dump'):
                ref = ref.model_dump()
            rec = self.store.get(ref['kind'], ref['id'], family)
            require(rec['version'] == ref['version'], 'EVIDENCE_SUPERSEDED', '证据版本已变化')
            if ref['kind']=='evidence':
                from .learning_evidence import validate_adapter_evidence
                validate_adapter_evidence(self,family,rec)
                from .reschedule_response import validate_change_evidence
                validate_change_evidence(self,family,rec)
                from .commitment_review import validate_expiration_evidence
                validate_expiration_evidence(self,family,rec)
            result.append((ref['kind'], rec))
        return result

    def ingest_message(self, token, args):
        cmd = Inbound.model_validate(args)
        source = cmd.model_dump(exclude_none=True)
        payload = {'request_id': 'inbound:' + cmd.external_id, **source}

        def action(family, _):
            from .replay_runtime import configuration
            if configuration(self, family) is not None:
                require(timestamp(cmd.occurred_at) <= self.clock.now(), 'REPLAY_FUTURE_INPUT',
                        '未来消息必须留在控制器，不能提前进入业务库')
                require(cmd.source_metadata is not None and cmd.source_metadata.source_id==cmd.external_id,
                        'REPLAY_SOURCE_REQUIRED','回放来源须完整绑定原输入身份')
                require(cmd.source_metadata.speaker_role!='teacher','REPLAY_TEACHER_INPUT',
                        '窗口内老师参考不能进入智能体来信流')
            msg_id = new_id('msg')
            row = self.store.put('message', msg_id, family, {
                **source, 'direction': 'inbound', 'received_at': self.clock.iso(),
                'interpreted': False, 'intents': [], 'source_kind': 'mock_wechat'})
            self.store.connection.execute('INSERT INTO message_index(message_id,family_id,external_id,direction) VALUES(?,?,?,?)', (msg_id, family, cmd.external_id, 'inbound'))
            from .steering import enabled, enqueue
            if enabled(self):enqueue(self,family,msg_id)
            else:self.store.put('job', 'message:' + msg_id, family, {'kind': 'service', 'message_id': msg_id, 'status': 'ready'})
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
            if msg['interpreted']:
                require(cmd.append_new_intents, 'ALREADY_INTERPRETED', '已有意图不能覆盖；遗漏的新意图仅可在来源尚无业务效果时append_new_intents=true追加')
                from .interpretation_extension import validate_extension
                validate_extension(self,family,msg,cmd.intents)
            else:
                require(not cmd.append_new_intents,'ALREADY_INTERPRETED','首次解释不使用追加模式')
            ids = [i.intent_id for i in cmd.intents]
            require(len(ids) == len(set(ids)), 'INVALID_SCHEMA', '意图ID重复')
            for intent in cmd.intents:
                if intent.service_kind == 'future_delivery':
                    require(intent.requires_reply, 'FUTURE_DELIVERY_REPLY_REQUIRED',
                            '未来交付仍欠确认或履行，requires_reply须为true；这不表示立即发送')
                    require(intent.service_authorization and intent.service_authorization.strip()
                            and intent.service_authorization in msg['body'],
                            'SERVICE_AUTHORIZATION_REQUIRED', '未来文字交付须保留原消息内明确授权原文')
                else:
                    require(intent.service_authorization is None, 'SERVICE_AUTHORIZATION_SCOPE',
                            '普通答疑不附未来交付授权；缺资料本身不产生承接请求')
            for intent in cmd.intents:
                require(not (self.strict_runtime and len(cmd.intents)>1) or intent.source_excerpt is not None,
                        'INTENT_SOURCE_REQUIRED', '同一消息的多个意图各须引用本条原文；跨消息分别调用')
                require(intent.source_excerpt is None or (intent.source_excerpt.strip()
                        and intent.source_excerpt in msg['body']),
                        'INTENT_SOURCE_MISMATCH', '意图来源摘录必须来自指定message_id的正文')
            additions = [{**i.model_dump(), 'source_message_id':msg['id'],
                'source_excerpt':i.source_excerpt or msg['body'],
                'reply_operation_refs': []} for i in cmd.intents]
            from .responsibility_review import guard_interpret
            guard_interpret(self,msg,cmd.intents)
            msg['intents'] = msg['intents']+additions if cmd.append_new_intents else additions
            msg['interpreted'] = True
            return self.store.put('message', cmd.message_id, family, msg, msg['version'])
        payload=cmd.model_dump()
        if not cmd.append_new_intents:payload.pop('append_new_intents')
        for intent in payload['intents']:
            if intent['source_excerpt'] is None:intent.pop('source_excerpt')
            if intent['service_kind']=='reply' and intent['service_authorization'] is None:
                intent.pop('service_kind');intent.pop('service_authorization')
        return self.write(token, {'service'}, 'interpret', payload, action)

    def apply_control(self, token, args):
        cmd = ControlCommand.model_validate(args)

        def action(family, _):
            msg = self.store.get('message', cmd.source_message_id, family)
            require(msg['direction'] == 'inbound' and msg['version'] == cmd.source_version, 'EVIDENCE_SUPERSEDED', '控制来源必须是当前入站消息')
            if cmd.event_id is not None:
                self.event(family, cmd.event_id)
            if cmd.release_control_id:
                control = self.store.get('control', cmd.release_control_id, family)
                require(control['status'] == 'active', 'INVALID_CONTROL', '暂停已解除')
                require(control['source_message_id'] != cmd.source_message_id, 'EVIDENCE_MISSING', '解除需要新消息依据')
                require(control_event_id(control) == cmd.event_id and set(control['action_kinds']) == set(cmd.action_kinds), 'INVALID_CONTROL', '解除范围不一致')
                control.update(status='released', released_at=self.clock.iso(), release_source_message_id=cmd.source_message_id)
                return self.store.put('control', control['id'], family, control, control['version'])
            from .business_time import expiry
            valid_until = expiry(cmd.temporal_scope, cmd.valid_until, msg['occurred_at'])
            return self.store.put('control', new_id('control'), family, {
                **cmd.model_dump(exclude={'request_id', 'release_control_id'}), 'valid_until': valid_until,
                'status': 'active', 'effective_at': self.clock.iso()})
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
            from .learning_review import validate_report_revision
            validate_report_revision(self,family,binding,cmd)
            from .commitment_review import validate_expiration_report
            validate_expiration_report(self,family,binding,cmd)
            require(len(set(cmd.event_ids)) == len(cmd.event_ids), 'INVALID_SCHEMA', '关联事项重复')
            for eid in cmd.event_ids:
                self.event(family, eid)
            self.validate_refs(family, cmd.evidence_refs)
            from .learning_evidence import validate_review_report
            validate_review_report(self,family,binding,cmd)
            key = self.report_key(family, cmd.report_id, cmd.revision)
            existing = self.store.connection.execute("SELECT data FROM records WHERE kind='report' AND id=?", (key,)).fetchone()
            from .report_contract import snapshots, validate_assessments, report_payload
            validate_assessments(self, family, cmd)
            saved_old = json.loads(existing['data']) if existing else None
            canonical = report_payload(cmd, legacy=bool(saved_old and not saved_old.get('schema_version')))
            canonical.pop('request_id')
            if existing:
                saved = json.loads(existing['data'])
                require(saved['payload'] == canonical, 'IDEMPOTENCY_CONFLICT', '报告身份的载荷不可变')
                return self.store.get('report', key, family)
            from .acceptance_handoff import validate_report
            validate_report(self, family, binding, cmd)
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
            from .commitment_review import report_observation
            saved = self.store.put('report', key, family, {'payload': canonical, 'event_ids': cmd.event_ids,
                'report_id': cmd.report_id, 'revision': cmd.revision, 'resolution': None,
                'created_at': self.clock.iso(), 'origin_run_id': binding.get('run_id'),
                'schema_version': 2, 'source_snapshots': snapshots(self, family, cmd.evidence_refs),
                **({'commitment_observation':report_observation(self,family,binding)} if binding.get('commitment_review_id') else {})})
            for eid in cmd.event_ids:
                self.store.connection.execute('INSERT INTO report_links VALUES(?,?,?,0)', (key, eid, family))
            self.store.put('job', 'progress:' + key, family, {'kind': 'progress', 'report_key': key, 'status': 'ready'})
            return saved
        from .report_contract import report_payload
        family, _ = self.auth(token, {'service'}, allow_inactive=True)
        old = next((r for r in self.store.list('report', family)
                    if r['report_id'] == cmd.report_id and r['revision'] == cmd.revision), None)
        return self.write(token, {'service'}, 'submit_report', report_payload(cmd, legacy=bool(old and not old.get('schema_version'))), action)

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
