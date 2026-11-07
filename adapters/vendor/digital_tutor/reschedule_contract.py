"""Source-bound rescheduling, immutable proposal identity and accepted contact authority."""
from copy import deepcopy
from datetime import date
import json
from .domain import require, timestamp, SHANGHAI
from .storage import digest, new_id
from .report_contract import message_source_digest


def require_no_uncertain_proposal(app, family, event_id, exclude_operation=None):
    require(not any(op['id'] != exclude_operation and
        (op.get('event_id') == event_id or (op.get('proposal_snapshot') or {}).get('event_id') == event_id)
        and op.get('stage') in {'queued', 'dispatching', 'accepted', 'unknown'}
        for op in app.store.list('operation', family)),
        'OPERATION_UNCERTAIN', '原事项或其改约提议仍有待核操作，不能用另一来信或提议号重发')


def validate_proposed_step(app, event, requested, step):
    scope = event['completion']['lesson_refs'] or event['completion']['required_part_ids']
    actions = ({'remind_makeup', 'verify_learning'} if event['completion']['kind'] == 'goal'
               else {'deliver_feedback', 'verify_operation'})
    require(step.get('action_kind') in actions and set(step['target_refs']).issubset(scope),
            'PROGRESS_SCOPE_VIOLATION', '改约步骤仍属于原对象与服务类型')
    require(step.get('execute_at'), 'EXECUTION_TIME_REQUIRED', '提议必须给出实际步骤时刻')
    execute = timestamp(step['execute_at']).astimezone(SHANGHAI)
    require(execute >= app.clock.now() and app.service_start <= execute.hour < app.service_end,
            'INVALID_CHANGE_TIME', '不能提议过去或服务窗口外的执行时刻')
    if requested['precision'] == 'date':
        value = date.fromisoformat(requested['value'])
        require(requested['meaning'] == 'schedule', 'TIME_PRECISION_REQUIRES_CLARIFICATION',
                '日期级完成上界需进一步澄清，不能造出日末截止')
        require(execute.date() == value, 'SCHEDULE_SOURCE_MISMATCH', '执行步骤须保留接受的行动日期')
        due = event.get('due_at')
    else:
        value = timestamp(requested['value'])
        if requested['meaning'] == 'schedule':
            require(execute == value, 'SCHEDULE_SOURCE_MISMATCH', '步骤须对应所提精确时刻')
            due = event.get('due_at')
        else:
            require(execute <= value, 'DEADLINE_PASSED', '提议步骤不能晚于新的完成上界')
            due = requested['value']
    require(due is None or execute <= timestamp(due), 'CHANGE_CONSTRAINT_CONFLICT',
            '新行动日期与仍有效完成期限冲突，须澄清或另外有据提议期限变更')
    return due


def make_proposal(app, family, event, proposal, records):
    require(event['status'] not in {'done', 'cancelled'}, 'CHANGE_NOT_APPLICABLE', '已结束事项不能改约')
    require_no_uncertain_proposal(app, family, event['id'])
    message = next((record for kind, record in records
                    if kind == 'message' and record['id'] == proposal['source_message_id']), None)
    require(message and message.get('direction') == 'inbound', 'EVIDENCE_MISSING', '提议须依据本报告原入站来源')
    requested = proposal['requested_time']
    require(requested['source_text'] and requested['source_text'] in message['body'],
            'TIME_SOURCE_MISMATCH', '提议时间保留原话，不编造缺省期限')
    step = deepcopy(proposal['next_action'])
    due = validate_proposed_step(app, event, requested, step)
    result = {'change_id': new_id('change'), 'status': 'proposed', 'proposed_at': app.clock.iso(),
            'old_due_at': event.get('due_at'), 'proposed_due_at': due,
            'old_next_action': deepcopy(event.get('next_action')),
            'proposed_next_action': step, 'requested_time': deepcopy(requested),
            'requirement_revision': event['requirement_revision'],
            'source_message_ref': {'id': message['id'], 'version': message['version']},
            'source_message_digest': message_source_digest(message),
            'contract_kind': 'source_bound_reschedule_v1'}

    current = event.get('proposed_change') or {}
    prior = [current] + [h.get('applied_change', {}) for h in event.get('schedule_history', [])]
    prior += [(op.get('proposal_snapshot') or {}).get('proposal', {})
              for op in app.store.list('operation', family)
              if (op.get('proposal_snapshot') or {}).get('event_id') == event['id']]
    for old in prior:
        if not (old.get('source_message_ref', {}).get('id') == message['id']
                and old.get('source_message_digest') == result['source_message_digest']):
            continue
        if old is current and old.get('status') == 'proposed' and proposal_identity(old) == proposal_identity(result):
            return deepcopy(old)
        delivered = any((op.get('proposal_snapshot') or {}).get('change_id') == old.get('change_id')
            and op.get('stage') in {'sent', 'delivered'} for op in app.store.list('operation', family))
        require(not delivered and old.get('status') != 'accepted',
                'CHANGE_REPROPOSAL_REQUIRES_NEW_SOURCE', '同一未变来源已实际提出或接受，不能换编号或措辞取得新提议发送权')
    return result


def apply_accepted_change(app, family, event, change, evidence, request_id):
    from .reschedule_response import validate_change_evidence
    validate_change_evidence(app, family, evidence, for_consumption=True)
    require(change.get('contract_kind') == 'source_bound_reschedule_v1',
            'INVALID_CHANGE', '实际来源接受须对应新的完整提议合同')
    require(request_id, 'INVALID_CHANGE', '改约必须属于可核的实际progress提交')
    require(event.get('due_at') == change['old_due_at']
            and event.get('next_action') == change['old_next_action']
            and event['requirement_revision'] == change['requirement_revision'],
            'CHANGE_SUPERSEDED', '提议后原合同或步骤已变化，先核对当前安排')
    source = app.store.get('message', change['source_message_ref']['id'], family)
    require(message_source_digest(source) == change['source_message_digest'],
            'EVIDENCE_SUPERSEDED', '提议原始来源已经更正')
    require(not any(((op.get('event_id') == event['id'] and op.get('stage') in {'dispatching', 'accepted', 'unknown'})
                    or ((op.get('proposal_snapshot') or {}).get('event_id') == event['id']
                        and op.get('stage') in {'queued', 'dispatching', 'accepted', 'unknown'}))
                    for op in app.store.list('operation', family)),
            'OPERATION_UNCERTAIN', '接受事实已保留；先核实原操作，不借改约重发')
    step = deepcopy(change['proposed_next_action'])
    due = validate_proposed_step(app, event, change['requested_time'], step)
    from .send_policy import action_paused
    require(not action_paused(app, family, step['action_kind'], event['id']),
            'CONTROL_ACTIVE', '接受改约不解除触达暂停')
    event.setdefault('schedule_history', []).append({
        'next_action': deepcopy(event.get('next_action')), 'due_at': event.get('due_at'),
        'accepted_commitment': deepcopy(event.get('accepted_commitment')),
        'replaced_at': app.clock.iso(), 'change_id': change['change_id'],
        'applied_change': deepcopy(change), 'commit_request_id': request_id,
        'acceptance_evidence_ref': {'kind': 'evidence', 'id': evidence['id'], 'version': evidence['version']}})
    event['next_action'] = step
    event['due_at'] = due
    event['effective_requested_time'] = deepcopy(change['requested_time'])
    event['status'] = 'in_progress'
    event['waiting'] = None
    from .scheduling import step_key
    grant = event.get('accepted_commitment') or {
        'id': 'commitment:' + digest([family, event['id'], change['change_id']]),
        'kind': 'accepted_change', 'commit_request_id': request_id,
        'source_message_ref': deepcopy(evidence['source_message_ref']),
        'source_message_digest': evidence['source_message_digest'],
        'requirement_revision': event['requirement_revision'], 'schedule_revision': 0,
        'authorized_target_refs': deepcopy(event['completion']['lesson_refs'] or event['completion']['required_part_ids']),
        'authorized_action_kinds': (['remind_makeup', 'verify_learning'] if event['completion']['kind'] == 'goal'
                                    else ['deliver_feedback', 'verify_operation'])}
    start = timestamp(step['execute_at']).astimezone(SHANGHAI)
    end = start.replace(hour=app.service_end, minute=0, second=0, microsecond=0)
    if due:end = min(end, timestamp(due))
    # Existing admission identity is retained. A baseline Event receives a new
    # source-bound grant from this actual accepted change, not an invented admission.
    event['accepted_commitment'] = {**grant, 'status': 'active',
        'valid_from': start.isoformat(), 'valid_until': end.isoformat(),
        'authorized_step_key': step_key(event), 'schedule_revision': grant['schedule_revision'] + 1,
        'schedule_change_id': change['change_id'], 'schedule_change_request_id': request_id,
        'acceptance_evidence_ref': {'kind': 'evidence', 'id': evidence['id'], 'version': evidence['version']}}
    event['schedule_history'][-1]['applied_commitment'] = deepcopy(event['accepted_commitment'])
    from .contact_capacity import committed_contacts, committed_reminders
    from .send_policy import ContactPolicy, daily_contact_available
    policy = getattr(app, 'contact_policy', ContactPolicy())
    if step['action_kind'] in {'remind_makeup', 'deliver_feedback'}:
        require(daily_contact_available(app,family,committed_contacts(app, family, start.date().isoformat(), exclude_event=event['id'])),
                'CONTACT_LIMIT', '新安排仍受当日实际与已承接额度限制')
    if step['action_kind'] == 'remind_makeup':
        require(all(count < policy.reminders_per_lesson_week for count in committed_reminders(
            app, family, start.date().isoformat(), event['completion']['lesson_refs'], event['id']).values()),
            'CONTACT_LIMIT', '新安排仍保留原课节已发提醒计数')
    return event


def validate_schedule_receipt(app, family, event, grant):
    if not grant.get('schedule_change_request_id'):
        return
    row = app.store.connection.execute('SELECT result FROM receipts WHERE family_id=? AND request_id=?',
        (family, grant['schedule_change_request_id'])).fetchone()
    require(row is not None, 'COMMITMENT_INVALID', '改约缺少实际progress提交回执')
    committed = next((e for e in json.loads(row['result']).get('data', {}).get('events', [])
                      if e.get('id') == event['id']), None)
    require(committed and committed.get('accepted_commitment') == grant,
            'COMMITMENT_INVALID', '当前排期授权须与原改约提交回执精确一致')


def validate_change_authority(app, family, event, grant):
    """Live authority requires the sealed progress receipt and unchanged sources."""
    validate_schedule_receipt(app, family, event, grant)
    protect_accepted_contact(app, family, event, event.get('next_action'))
    history = next((h for h in event.get('schedule_history', [])
                    if h.get('applied_commitment') == grant), None)
    require(history is not None, 'COMMITMENT_INVALID', '缺少原生效改约合同历史')
    evidence = app.store.get('evidence', grant['acceptance_evidence_ref']['id'], family)
    from .reschedule_response import validate_change_evidence
    validate_change_evidence(app, family, evidence)
    require(evidence['version'] == grant['acceptance_evidence_ref']['version'],
            'COMMITMENT_SOURCE_CHANGED', '原接受凭证版本已变化')
    for ref, expected in [(evidence['source_message_ref'], evidence['source_message_digest']),
                          (history['applied_change']['source_message_ref'], history['applied_change']['source_message_digest'])]:
        require(message_source_digest(app.store.get('message', ref['id'], family)) == expected,
                'COMMITMENT_SOURCE_CHANGED', '改约原来源已更正，不能沿旧授权发起')


def historical_grant_receipt(app, family, event, grant):
    """Check receipt identity for past sends; this never grants current execution."""
    if grant.get('schedule_change_request_id'):
        validate_schedule_receipt(app, family, event, grant)
    else:
        row = app.store.connection.execute('SELECT result FROM receipts WHERE family_id=? AND request_id=?',
            (family, grant.get('commit_request_id'))).fetchone()
        require(row and grant.get('id') in json.loads(row['result']).get('data', {}).get('accepted_commitment_ids', []),
                'COMMITMENT_INVALID', '历史发送缺少原接纳回执')


def contact_step_identity(step):
    """Presentation wording alone does not create a new accepted contact."""
    step = step or {}
    return {'action_kind': step.get('action_kind'),
            'target_refs': sorted(step.get('target_refs', [])),
            'execute_at': timestamp(step['execute_at']).isoformat() if step.get('execute_at') else None,
            'completion_condition': step.get('completion_condition')}


def proposal_identity(change):
    requested = change.get('requested_time') or {}
    return {'source_id': change.get('source_message_ref', {}).get('id'),
            'source_digest': change.get('source_message_digest'),
            'requirement_revision': change.get('requirement_revision'),
            'old_due_at': change.get('old_due_at'),
            'old_next_action': contact_step_identity(change.get('old_next_action')),
            'proposed_due_at': change.get('proposed_due_at'),
            'proposed_next_action': contact_step_identity(change.get('proposed_next_action')),
            'requested_time': {k: requested.get(k) for k in ('meaning', 'precision', 'value')}}


def canonical_accepted_contact(event, next_action):
    grant = event.get('accepted_commitment') or {}
    if not grant.get('schedule_change_request_id') or not next_action or next_action['action_kind'] not in {'remind_makeup', 'deliver_feedback'}:
        return next_action
    history = next((h for h in event.get('schedule_history', []) if h.get('applied_commitment') == grant), None)
    if history:
        agreed = history['applied_change']['proposed_next_action']
        if contact_step_identity(next_action) == contact_step_identity(agreed):
            return deepcopy(agreed)
    return next_action


def protect_accepted_contact(app, family, event, next_action):
    """Guard the unperformed accepted contact without freezing internal checks."""
    grant = event.get('accepted_commitment') or {}
    if not grant.get('schedule_change_request_id') or not next_action or next_action['action_kind'] not in {'remind_makeup', 'deliver_feedback'}:
        return
    history = next((h for h in event.get('schedule_history', []) if h.get('applied_commitment') == grant), None)
    require(history is not None, 'COMMITMENT_INVALID', '缺少当前改约原合同')
    agreed = history['applied_change']['proposed_next_action']
    if contact_step_identity(next_action) == contact_step_identity(agreed):
        from .scheduling import step_key
        require(step_key({'id': event['id'], 'next_action': next_action}) == grant['authorized_step_key'],
                'ACCEPTED_SCHEDULE_CHANGED', '同一已接受触达保留原规范步骤身份，不借文案产生新步骤')
        return
    from .scheduling import operation_step_id, step_key
    # A wording-only rewrite may change step_key. Match immutable Event history
    # and the captured grant, rather than treating the wording as new authority.
    agreed_keys = set()
    for row in app.store.connection.execute('SELECT data FROM history WHERE family_id=? AND kind=? AND id=?',
                                           (family, 'event', event['id'])):
        past = {'id': event['id'], **json.loads(row['data'])}
        if past.get('accepted_commitment') == grant and contact_step_identity(past.get('next_action')) == contact_step_identity(agreed):
            agreed_keys.add(step_key(past))
    performed = any(op.get('event_id') == event['id']
        and operation_step_id(op) in agreed_keys
        and op.get('send_capability', {}).get('commitment_id') == grant['id']
        and op.get('send_capability', {}).get('schedule_revision') == grant['schedule_revision']
        and op.get('dispatch_count') == 1 and op.get('admitted_at')
        and op.get('stage') in {'sent', 'delivered'} for op in app.store.list('operation', family))
    require(performed and next_action['action_kind'] != 'remind_makeup', 'ACCEPTED_SCHEDULE_CHANGED', '尚未履行的已接受触达不得由普通进展改时或换目标；内部核账仍可继续')
