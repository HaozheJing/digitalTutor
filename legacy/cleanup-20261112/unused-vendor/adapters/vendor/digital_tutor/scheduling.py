"""Read-only execution authority and business-time projection for the one job queue."""
from copy import deepcopy
import json

from .domain import BusinessError, SHANGHAI, require, timestamp
from .storage import digest


def baseline_committed(app, family, business_date):
    return any(p.get('business_date') == business_date and p.get('baseline_committed', True)
               for p in app.store.list('plan', family))


def step_key(event):
    step = deepcopy(event.get('next_action'))
    if step:
        # New optional defaults must not invalidate historical operation/job keys.
        for name in ('execute_at', 'completion_condition'):
            if step.get(name) is None:
                step.pop(name, None)
    return digest({'event_id': event['id'], 'next_action': step})


def _versioned_record(app, family, kind, ref):
    require(isinstance(ref, dict) and ref.get('id') and type(ref.get('version')) is int,
            'COMMITMENT_INVALID', '承接来源引用不完整')
    row = app.store.connection.execute(
        'SELECT data FROM history WHERE family_id=? AND kind=? AND id=? AND version=?',
        (family, kind, ref['id'], ref['version'])).fetchone()
    require(row is not None, 'COMMITMENT_INVALID', '承接来源历史版本不属于当前家庭')
    return {'id': ref['id'], 'version': ref['version'], **json.loads(row['data'])}


def commitment_authority(app, family, event):
    """A durable grant is source/receipt-bound, never a durable run capability."""
    grant = event.get('accepted_commitment')
    require(isinstance(grant, dict) and grant.get('status') == 'active',
            'NOT_IN_DAILY_PLAN', '既无今日主动资格，也无有效正式承接')
    require(grant.get('id') and type(grant.get('schedule_revision')) is int
            and grant['schedule_revision'] >= 1 and grant.get('authorized_step_key'),
            'COMMITMENT_INVALID', '承接身份或排期版本不完整')
    require(grant.get('requirement_revision') == event.get('requirement_revision'),
            'COMMITMENT_STALE', '原承接不能覆盖新版完成要求')
    start, end = timestamp(grant.get('valid_from')), timestamp(grant.get('valid_until'))
    require(start <= end and start.astimezone(SHANGHAI).date() == end.astimezone(SHANGHAI).date(),
            'COMMITMENT_INVALID', '承接动作窗口须有界且在同一业务日')
    require(start <= app.clock.now() <= end, 'COMMITMENT_WINDOW', '未到或已超过原承接动作窗口')
    step = event['next_action']
    require(step.get('execute_at') is not None and start <= timestamp(step['execute_at']) <= end,
            'COMMITMENT_SCOPE', '当前步骤排期不在原承接窗口')
    require(step['action_kind'] in grant.get('authorized_action_kinds', [])
            and set(step['target_refs']).issubset(grant.get('authorized_target_refs', [])),
            'COMMITMENT_SCOPE', '当前步骤超出原承接动作或目标范围')
    scope = set(event['completion'].get('lesson_refs', []) + event['completion'].get('required_part_ids', []))
    require(set(grant.get('authorized_target_refs', [])).issubset(scope),
            'COMMITMENT_SCOPE', '承接目标超出原责任')
    if grant.get('kind') == 'accepted_change':
        from .reschedule_contract import validate_change_authority
        validate_change_authority(app, family, event, grant)
        return {'kind': 'accepted_commitment', 'commitment_id': grant['id'], 'schedule_revision': grant['schedule_revision']}
    pending = app.store.get('pending', grant.get('source_pending_id'), family)
    require(pending.get('event_id') == event['id'] and not pending.get('await_decision', False),
            'COMMITMENT_INVALID', '原待承接输入尚未决定或未绑定此事项')
    original = _versioned_record(app, family, 'message', grant.get('source_message_ref'))
    current = app.store.get('message', original['id'], family)
    from .report_contract import message_source_digest
    require(pending.get('source_message_id') == original['id']
            and message_source_digest(original) == message_source_digest(current)
            and grant.get('source_message_digest') == message_source_digest(original),
            'COMMITMENT_SOURCE_CHANGED', '原承接消息事实已变化，须重新核实')
    candidate = _versioned_record(app, family, 'candidate', grant.get('candidate_ref'))
    require(candidate.get('pending_id') == pending['id'], 'COMMITMENT_INVALID', '候选不是本次承接输入')
    source = app.store.get('candidate_source', candidate.get('source_id'), family)
    from .candidates import derive
    current_source = derive(app, family, source)
    require(current_source.get('status')=='ready'
            and (current_source.get('source_fingerprint')==candidate.get('source_fingerprint')
                 or unchanged_text_request(app, family, source, candidate)),
            'COMMITMENT_SOURCE_CHANGED','原承接可信来源已更正或不再成立，须受限复核')
    row = app.store.connection.execute('SELECT result FROM receipts WHERE family_id=? AND request_id=?',
                                       (family, grant.get('commit_request_id'))).fetchone()
    require(row is not None and grant['id'] in json.loads(row['result']).get('data', {}).get('accepted_commitment_ids', []),
            'COMMITMENT_INVALID', '缺少此承接的真实提交回执')
    if grant.get('schedule_change_request_id'):
        from .reschedule_contract import validate_change_authority
        validate_change_authority(app, family, event, grant)
    return {'kind': 'accepted_commitment', 'commitment_id': grant['id'],
            'schedule_revision': grant['schedule_revision']}


def unchanged_text_request(app, family, source, candidate):
    """Legacy text fingerprints included every binding, unrelated to text authority.

    A new learning batch must not revoke an unchanged accepted text request.
    Compare its actual immutable source and original message, while retaining all
    existing grant, receipt, identity, schedule and ready-status checks. Makeup
    requests still require their original binding-sensitive fingerprint.
    """
    if source.get('source_kind') != 'family_service_request':
        return False
    original_source = _versioned_record(app, family, 'candidate_source', {
        'id': candidate.get('source_id'), 'version': candidate.get('source_version')})
    if source != original_source:
        return False
    original_message = _versioned_record(app, family, 'message', {
        'id': source.get('source_message_id'), 'version': source.get('source_message_version')})
    current_message = app.store.get('message', original_message['id'], family)
    from .family_candidates import message_content
    return message_content(original_message) == message_content(current_message)


def require_step_authority(app, family, event, cap=None):
    require(event['status'] in {'pending', 'in_progress'} and not event.get('update_pending'),
            'UPDATE_PENDING', '事项已结束、等待或有报告待处理')
    step = event.get('next_action')
    require(step is not None, 'STEP_MISSING', '事项没有已提交下一步')
    if step.get('execute_at'):
        require(timestamp(step['execute_at']) <= app.clock.now(), 'STEP_NOT_DUE', '尚未到步骤业务执行时间')
    now = app.clock.now()
    require(app.service_start <= now.hour < app.service_end, 'WINDOW_CLOSED', '当前不在业务执行窗口')
    from .send_policy import action_paused
    require(not action_paused(app, family, step['action_kind'], event['id']),
            'CONTROL_ACTIVE', '步骤动作处于暂停范围')
    if cap is not None and app.strict_runtime:
        require(cap.get('job_kind') == 'step' and cap.get('event_id') == event['id']
                and cap.get('current_step_key') == step_key(event),
                'STEP_CAPABILITY_REQUIRED', '主动执行必须绑定本轮当前事项步骤')
    plans = [p for p in app.store.list('plan', family)
             if p.get('business_date') == now.date().isoformat()
             and any(i['event_id'] == event['id'] and i.get('authority','baseline') in {'baseline','source_refresh'}
                     for i in p.get('items', []))]
    # A run claiming a grant cannot silently switch authority if that grant changes.
    if (event.get('accepted_commitment') or {}).get('status')=='active' or (cap and cap.get('commitment_id')):
        authority = commitment_authority(app, family, event)
        if cap is not None and app.strict_runtime:
            require(authority['commitment_id'] == cap.get('commitment_id')
                    and authority['schedule_revision'] == cap.get('schedule_revision'),
                    'COMMITMENT_STALE', '承接排期已改变或本轮缺承接授权')
        return authority
    if plans:
        return {'kind': 'daily_plan', 'plan_id': plans[0]['id'],
                'plan_revision': plans[0].get('revision', 1)}
    return commitment_authority(app, family, event)


def pending_delivery_parts(app, family, event):
    """Only unsent parts already authorized by this exact committed action."""
    step=event.get('next_action') or {}
    if not event.get('service_action_id') or step.get('action_kind')!='deliver_feedback':return []
    parts=set(event['completion'].get('required_part_ids',[]))
    targets=set(step.get('target_refs',[]))
    parts=parts if targets.intersection(event['completion'].get('lesson_refs',[])) else parts.intersection(targets)
    sent={o.get('part_id') for o in app.store.list('operation',family) if o.get('event_id')==event['id'] and o.get('requirement_revision')==event['requirement_revision'] and o.get('stage') in {'sent','delivered','read'}}
    return sorted(parts-sent)


def step_ready(app, family, event, cap=None):
    from .runtime_config import allowed_kind
    if not allowed_kind(app, 'step'):
        return False
    try:
        require_step_authority(app, family, event, cap)
        if event['next_action']['action_kind']=='verify_operation':
            # A committed accounting step must be able to read uncertain receipts.
            # Its session is forced no_contact; existing job/wait identities bound scans.
            return not any(op['stage']=='queued' and (op.get('event_id')==event['id'] or
                (op.get('proposal_snapshot') or {}).get('event_id')==event['id'])
                for op in app.store.list('operation',family))
        # An uncertain old side effect never becomes repeatable just because a
        # schedule/date changed. Reconcile its original operation instead.
        return not any(((op.get('event_id') == event['id'] or
                         (op.get('proposal_snapshot') or {}).get('event_id') == event['id'])
                        and op['stage'] in {'queued', 'dispatching', 'accepted', 'unknown'}) or
                       (op.get('event_id') == event['id'] and op['stage'] in {'sent','delivered'}
                        and operation_step_id(op)==step_key(event) and not pending_delivery_parts(app,family,event))
                       for op in app.store.list('operation', family))
    except BusinessError:
        return False


def operation_step_id(operation):
    """Current sends persist step_id in request; legacy top-level only if absent."""
    request = operation.get('request')
    if isinstance(request, dict) and 'step_id' in request:
        return request['step_id']
    return operation.get('step_id')


def event_order(app, family, event):
    step = event.get('next_action') or {}
    earliest = timestamp(step['execute_at']).timestamp() if step.get('execute_at') else float('-inf')
    deadline = timestamp(event['due_at']).timestamp() if event.get('due_at') else float('inf')
    ranks = [i.get('rank', 999999) for p in app.store.list('plan', family)
             if p.get('business_date') == app.clock.now().date().isoformat()
             for i in p.get('items', []) if i['event_id'] == event['id']]
    return earliest, deadline, min(ranks, default=999999), event['id']


def planning_input_observation(app, family, job):
    if job.get('kind') != 'service' or not job.get('message_id'):
        return []
    return sorted([{'id': p['id'], 'version': p['version'],
                    'decision_revision': p.get('decision_revision', 1),
                    'source_intent_id': p.get('source_intent_id'),
                    'await_decision': bool(p.get('await_decision'))}
                   for p in app.store.list('pending', family)
                   if p.get('planning_requested') and p.get('source_message_id') == job['message_id']],
                  key=lambda p: p['id'])


def awaiting_planning_decision(app, family, job):
    """Only unchanged inputs observed by this original service run suppress it."""
    if not app.strict_runtime or not job.get('run_id'):
        return False
    row = app.store.connection.execute(
        "SELECT data FROM records WHERE kind='run_outcome' AND id=? AND family_id=?",
        (job['run_id'], family)).fetchone()
    outcome = json.loads(row['data']) if row else {}
    prior = outcome.get('observed_planning_inputs')
    current = planning_input_observation(app, family, job)
    if not (prior and current == prior and any(p['await_decision'] for p in current)
            and outcome.get('outcome') in {'waiting', 'needs_input'}):
        return False
    message = app.store.get('message', job['message_id'], family)
    unfinished = {i['intent_id'] for i in message.get('intents', [])
                  if i['requires_reply'] and not i['reply_operation_refs']}
    waiting_intents = {p['source_intent_id'] for p in current if p['await_decision']}
    return bool(message.get('interpreted') and unfinished and unfinished.issubset(waiting_intents))


def planning_decision_changed(app, family, job):
    if not job.get('run_id'):
        return False
    row = app.store.connection.execute(
        "SELECT data FROM records WHERE kind='run_outcome' AND id=? AND family_id=?",
        (job['run_id'], family)).fetchone()
    observed = json.loads(row['data']).get('observed_planning_inputs') if row else None
    return bool(observed and observed != planning_input_observation(app, family, job))
