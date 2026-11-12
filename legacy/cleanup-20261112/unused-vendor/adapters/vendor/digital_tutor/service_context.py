"""Host-derived service scopes and run-local evidence of actual message reads.

These projections do not decide what a parent meant or whether teaching worked.
Only read_messages may record a read; assembly/state reads never grant one.
Call mutation helpers inside the caller's existing transaction.
"""
from copy import deepcopy

from .domain import require
from .learning import learning_source_contract
from .storage import digest
from .source_period_scope import reporting_period


MESSAGE_SOURCE_FIELDS = ('id', 'external_id', 'direction', 'body', 'occurred_at',
                         'received_at', 'source_kind')


def message_source_digest(message):
    """Interpretation and reply receipts are derived state, not a changed input."""
    value = {key: message.get(key) for key in MESSAGE_SOURCE_FIELDS}
    if message.get('source_metadata') is not None:
        value['source_metadata'] = message['source_metadata']
    return digest(value)


def _read_record(app, family, run_id):
    if not run_id:
        return None
    record_id = 'service-read:' + digest([family, run_id])
    row = app.store.connection.execute(
        "SELECT 1 FROM records WHERE kind='runtime_message_read' AND id=? AND family_id=?",
        (record_id, family)).fetchone()
    return app.store.get('runtime_message_read', record_id, family) if row else None


def record_message_read(app, family, binding, result):
    """Record only rows returned by a successful, authenticated service read.

    No family bump, message update, job scheduling or new receipt is performed.
    Repeated reads of identical source rows do not create history revisions.
    This helper is not a model tool and does not accept model-asserted evidence.
    """
    if not (app.strict_runtime and binding.get('run_id') and binding.get('lease')
            and binding.get('job_kind') == 'service'):
        return
    old = _read_record(app, family, binding['run_id'])
    observed = deepcopy(old.get('messages', {})) if old else {}
    for returned in result['messages']:
        if returned.get('direction') != 'inbound':
            continue
        current = app.store.get('message', returned['id'], family)
        require(message_source_digest(returned) == message_source_digest(current),
                'MESSAGE_READ_CHANGED', '读取的原消息内容已变更，需要重新读取')
        observed[returned['id']] = {
            'source_digest': message_source_digest(returned),
            'seq': returned['seq'],
        }
    if not observed or (old and observed == old.get('messages')):
        return
    app.store.put('runtime_message_read', 'service-read:' + digest([family, binding['run_id']]),
                  family, {'run_id': binding['run_id'], 'job_id': binding.get('job_id'),
                           'source': 'successful_service_message_read', 'messages': observed,
                           'read_marks_answered': False}, old['version'] if old else None)


def require_reply_messages_read(app, family, cmd, capability):
    """Before a new strict send/dispatch, check this run read every replied input.

    Existing receipt replay and already-dispatched operation reconciliation stay
    outside this guard. Queued operations that have not dispatched must satisfy
    it when rechecked; old evidence from a different run cannot authorize them.
    """
    if not app.strict_runtime or not capability.get('run_id') or not cmd.reply_to:
        return
    observed = (_read_record(app, family, capability['run_id']) or {}).get('messages', {})
    for ref in cmd.reply_to:
        current = app.store.get('message', ref.message_id, family)
        read = observed.get(ref.message_id)
        require(current.get('direction') == 'inbound' and read is not None
                and read.get('source_digest') == message_source_digest(current),
                'MESSAGE_READ_REQUIRED', '本轮尚未核对所答入站原文，不能发起该回复；上下文展示不等于消息工具读取')


def message_read_state(app, family, binding):
    """Read coverage plus usable cursors, with old unresolved input made visible."""
    db = app.store.connection
    rows = db.execute('SELECT seq,message_id,direction FROM message_index WHERE family_id=? ORDER BY seq',
                      (family,)).fetchall()
    positions = {row['message_id']: row['seq'] for row in rows}
    scope = binding.get('reply_scope') or {}
    observed = (_read_record(app, family, binding.get('run_id')) or {}).get('messages', {})
    required = []
    for mid in scope:
        message = app.store.get('message', mid, family)
        required.append({'message_id': mid, 'seq': positions.get(mid),
                         'source_digest': message_source_digest(message),
                         'read_this_run': observed.get(mid, {}).get('source_digest') == message_source_digest(message)})
    cursors = []
    for cursor in app.store.list('runtime_cursor', family):
        if cursor.get('source') != 'successful_service_message_read' or cursor.get('direction') not in {'all', 'inbound'}:
            continue
        after = cursor['after_seq']
        cursors.append({'cursor_id': cursor['id'], 'version': cursor['version'],
                        'direction': cursor['direction'], 'after_seq': after,
                        'new_matching_count': sum(row['seq'] > after and
                            (cursor['direction'] == 'all' or row['direction'] == 'inbound') for row in rows),
                        'required_before_or_at_cursor': [item['message_id'] for item in required
                            if item['seq'] is not None and item['seq'] <= after and not item['read_this_run']]})
    return {'run_id': binding.get('run_id'), 'source': 'host_message_index_and_actual_read_receipts',
            'required_reply_inputs': required, 'trusted_contiguous_cursors': cursors,
            'snapshot_watermark': rows[-1]['seq'] if rows else 0,
            'assembly_is_tool_read': False, 'read_marks_answered': False,
            'selection_contract': 'wechat.read_message_stream按after_seq读取新增页，has_more表示尚有后续页。游标只代表此前完整读取的连续前缀；游标前仍待答的原文也须本轮核对，不能跳过。wechat.read_all_messages返回当前已到达全量快照。两种读取都不表示已答。'}


def control_scope_snapshot(app, family):
    """Exact action scope, independent of model prose and event topic similarity."""
    from .control_scope import control_event_id, legacy_invalid_scope, control_active
    event_ids = sorted(event['id'] for event in app.store.list('event', family))
    controls = []
    for control in app.store.list('control', family):
        target = control_event_id(control)
        invalid=legacy_invalid_scope(control)
        controls.append({'control_id': control['id'], 'version': control['version'],
                         'status': control['status'],
                         'scope_kind': 'invalid_legacy' if invalid else 'family' if target is None else 'event',
                         'effective_scope_kind': 'family' if target is None else 'event',
                         'event_id': target, 'stored_event_id': control.get('event_id'),
                         'legacy_invalid_scope': invalid,
                         'scope_interpretation': '历史无效范围，仅保守拦截所列动作；不得据此认定原家长授权了全家庭范围，须原来源复核或新授权解除。' if invalid else '明确保存的动作范围，以当前记录为准。',
                         'action_kinds': list(control['action_kinds']),
                         'source_message_id': control.get('source_message_id'),
                         'applies_to_current_event_ids': event_ids if target is None else [target],
                         'applies_to_future_events': target is None,
                         'valid_until': control.get('valid_until'),
                         'effective': control_active(app,control)})
    return {'authority': 'current_control_records', 'controls': controls,
            'meaning': '仅active控制生效。event_id为空的控制覆盖本家庭所有事项及以后事项中的action_kinds；有event_id仅覆盖该事项的这些动作。未列出的动作不因该控制暂停，仍须经过其他权限和发送检查。旧空白字符串范围按全家庭保守解释并标明legacy_invalid_scope，不修改原记录。自由文本不能缩小、扩大或解除控制。'}


def continuation_context(app, family, outcomes):
    """Preserve original debt; treat old free-text explanations as claims only."""
    scopes = control_scope_snapshot(app, family)
    return [{**deepcopy(outcome), 'current_control_scopes': deepcopy(scopes),
             'resume_condition_authority': 'model_input_recovery_note_not_control_or_send_permission'}
            for outcome in outcomes]


def request_source_registration(app, family, binding):
    """Persisted links for current reply inputs; absence is not a semantic verdict."""
    scopes = binding.get('reply_scope') or {}
    pending = app.store.list('pending', family)
    return [{
        'message_id': message_id,
        'intent_ids': list(intent_ids),
        'current_source': {name: deepcopy(app.store.get('message', message_id, family).get(name))
                           for name in ('body', 'version', 'occurred_at')},
        'registration_contract': '先核本条原话是否重申或补充原goal的补读行动/时间，再决定回复是否可结清。若是，使用pending.propose_makeup的existing_event_id保留本条完整来源；同义、确认语气、与原安排兼容均不替代来源登记。保留原话时间精度，仅日级行动时间为schedule/date，明确时刻为schedule/datetime；有完成上界另存completion_deadline，无时间增量可空。本条已登记则读取现有处置，不重复登记。登记只保留待审来源，不代表接受或更改原安排。普通答谢或无关聊天不因此登记。语义及原goal关联仍由模型结合完整原文判断。',
        'registered_sources': [{name: deepcopy(row.get(name)) for name in
            ('id', 'version', 'source_intent_id', 'candidate_source_id',
             'requested_event_id', 'event_id', 'requested_time', 'supplemental_requested_times',
             'await_decision', 'decision_state')}
            for row in pending if row.get('source_message_id') == message_id
            and ('*' in intent_ids or row.get('source_intent_id') in intent_ids)]
    } for message_id, intent_ids in scopes.items()]


def service_evidence_contract(app, family, binding, events=None):
    """Read-only separation of input, snapshot, responsibility and control facts."""
    events = events if events is not None else [app.event(family, event['id'])
                                               for event in app.store.list('event', family)]
    responsibilities = []
    for event in events:
        if event.get('status') in {'done', 'cancelled'}:
            continue
        responsibilities.append({
            'event_id': event['id'], 'version': event['version'], 'status': event['status'],
            'goal_text': event.get('goal_text'), 'completion': deepcopy(event['completion']),
            'remaining_work': deepcopy(event.get('remaining_work', [])),
            'requirement_revision': event.get('requirement_revision'), 'due_at': event.get('due_at'),
            'completion_condition': event.get('completion_condition'),
            'completion_evidence_scope': '核对原completion及有值的原completion_condition；null不新增学习发生日期或覆盖完整期限要求。完成、是否按时、快照覆盖分别判断；有效完成证据不必等到截止之后才可核查。原来源明确限定发生日期时仍保留该条件。',
            'source_kind': 'business_responsibility',
            'status_is_learning_snapshot': False,
            'reporting_period': reporting_period(event),
        })
    bound_events = list(binding.get('event_ids') or [])
    if binding.get('job_id'):
        job = app.store.get('job', binding['job_id'], family)
        if job.get('event_id'):
            bound_events = [job['event_id']]
    return {
        'source': 'host_records_projection',
        'current_request': {'message_ids': list(binding.get('reply_scope') or {}),
                            'event_ids': bound_events,
                            'activity_completion_is_event_completion': False},
        'request_source_registration': request_source_registration(app, family, binding),
        'source_comparison_contract': '原事项索引保留已登记的目标和时间含义；due_at是完成期限，requested_time中的schedule是行动时间，不能仅因日期相同视为同一约束。本次输入的registered_sources只展示已持久登记，不代表语义重复或已接纳；是否补充、重复或需澄清仍由Service结合原文判断。读取不改变原事项或授予执行权。',
        'responsibility_contracts': responsibilities,
        'learning_snapshot_contract': learning_source_contract(app, family),
        'completion_contract': '本次陪读或聊天可以停止；这只界定当前活动。原目标覆盖completion中的全部课节，原交付覆盖全部required_part_ids及required_stage；remaining_work仍按原合同核验。局部表达、卡片或一次互动不代替原目标的可靠完成依据。',
        'control_scopes': control_scope_snapshot(app, family),
        'message_reads': message_read_state(app, family, binding),
    }
