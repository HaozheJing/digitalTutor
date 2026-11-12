"""Service-facing execution facts, never raw Planner proposals or decision rationale."""
from copy import deepcopy

from .domain import BusinessError
from .scheduling import require_step_authority, step_ready, event_order


EVENT_FIELDS = ('service_action_id', 'next_execute_at', 'action_check', 'id', 'version', 'goal_key', 'goal_text', 'status', 'completion',
                'completion_condition', 'completion_contract_version', 'remaining_work',
                'due_at', 'waiting', 'next_action', 'update_pending', 'requirement_revision',
                'proposed_change', 'deadline_history', 'evidence_refs', 'creation_source_refs',
                'source_identity', 'original_deadline', 'requested_time', 'delivery_parts',
                'effective_requested_time', 'schedule_history', 'accepted_request_refs')


def project_event(app, family, event, cap=None):
    value = {name: deepcopy(event[name]) for name in EVENT_FIELDS if name in event}
    from .source_period_scope import reporting_period
    period = reporting_period(event)
    if period is not None:
        value['reporting_period'] = period
    from .delivery_retry import candidate
    retry=candidate(app,family,event) if app.strict_runtime else None
    if retry:
        value['delivery_retry']=retry
        value['delivery_retry_contract']='原失败及原报告保留；当前正常step可仅为该operation的欠part创建新attempt，retry_operation_id引用原ID，原正文不变。内部核账不发送；来源或安排改变需重新处理，不能借重试改正文。'
    grant = event.get('accepted_commitment')
    if grant:
        value['accepted_commitment'] = {name: deepcopy(grant[name]) for name in
            ('id', 'status', 'requirement_revision', 'authorized_target_refs', 'authorized_action_kinds',
             'valid_from', 'valid_until', 'schedule_revision', 'source_message_ref') if name in grant}
    try:
        authority = require_step_authority(app, family, event)
        ready = step_ready(app, family, event)
        # Preserve the real business blocker (expired grant, pause, not due)
        # before checking whether this particular run owns a ready step.
        if ready and cap is not None:
            authority = require_step_authority(app, family, event, cap)
            ready = step_ready(app, family, event, cap)
        value['execution'] = {'eligible_now': ready, **authority}
        if not ready:
            value['execution']['blocked_by'] = ('EXECUTION_MODE' if app.mode!='full'
                                                else 'ORIGINAL_OPERATION_REQUIRES_ACCOUNTING')
    except BusinessError as exc:
        value['execution'] = {'eligible_now': False, 'blocked_by': exc.code}
    return value


def responsibility_index(events):
    return [{name: deepcopy(event[name]) for name in
             ('id', 'version', 'goal_text', 'status', 'due_at', 'completion_condition',
              'completion', 'remaining_work', 'requested_time', 'effective_requested_time',
              'original_deadline', 'source_identity') if name in event}
            for event in events if event.get('status') not in {'done', 'cancelled'}]


def accepted_reply_events(app, family, binding):
    """Read current committed arrangements for this run's original reply sources.

    Selection conveys facts only; it cannot create a step capability or receipt.
    A pending label alone is insufficient: the canonical grant must bind the
    same source decision, including a linked request's durable accepted alias.
    """
    if (binding or {}).get('report_review_key'):
        return []
    sources = (binding or {}).get('reply_scope', {})
    events = {e['id']: e for e in app.store.list('event', family)}
    selected = {}
    for pending in app.store.list('pending', family):
        if (pending.get('source_message_id') not in sources
                or pending.get('await_decision') is not False
                or pending.get('decision_state') not in {'created', 'linked'}):
            continue
        intents = sources[pending['source_message_id']]
        if '*' not in intents and pending.get('source_intent_id') not in intents:
            continue
        event = events.get(pending.get('event_id'))
        grant = (event or {}).get('accepted_commitment') or {}
        request = pending.get('decision_request_id')
        if not request or not grant.get('id'):
            continue
        refs = [{'pending_id': grant.get('source_pending_id'),
                 'commit_request_id': grant.get('commit_request_id'),
                 'source_message_ref': grant.get('source_message_ref') or {}}]
        refs.extend(event.get('accepted_request_refs', []))
        if any(ref.get('pending_id') == pending['id']
               and ref.get('commit_request_id') == request
               and (ref.get('source_message_ref') or {}).get('id') == pending['source_message_id']
               for ref in refs):
            selected[event['id']] = event
    return list(selected.values())


def project_service_state(app, family, state, binding=None, *, selected_event_ids=None):
    binding = binding or {}
    result = deepcopy(state)
    selected_ids = set(selected_event_ids or ())
    if binding.get('event_id'):
        selected_ids.add(binding['event_id'])
    selected_ids.update(binding.get('report_review_event_ids', []))
    accepted = accepted_reply_events(app, family, binding)
    selected_ids.update(event['id'] for event in accepted)
    available = {event['id']: event for event in state.get('events', [])}
    available.update({event['id']: event for event in accepted})
    selected = [event for event in available.values() if event['id'] in selected_ids]
    result['responsibility_index'] = responsibility_index(app.store.list('event', family))
    # A due Event is not an execution capability for an ordinary reply run.
    # Use the same bound-session check as the actual send gate.
    result['events'] = [project_event(app, family, event, binding) for event in selected]
    message_ids = set(binding.get('reply_scope', {}))
    for event in selected:
        source = (event.get('accepted_commitment') or {}).get('source_message_ref') or {}
        if source.get('id'):
            message_ids.add(source['id'])
    relevant_pending = [p for p in state.get('pending_inputs', [])
               if p.get('event_id') in selected_ids or p.get('requested_event_id') in selected_ids
               or p.get('source_message_id') in message_ids]
    message_ids.update(p.get('source_message_id') for p in relevant_pending)
    from .reschedule_projection import source_message_ids
    message_ids.update(source_message_ids(app, family, selected_ids))
    result['messages'] = [deepcopy(m) for m in state.get('messages', []) if m['id'] in message_ids]
    result['operations'] = [deepcopy(op) for op in state.get('operations', [])
        if op.get('event_id') in selected_ids or op['id'] in binding.get('recovery_operation_ids', [])
        or any(ref.get('message_id') in message_ids for ref in op.get('reply_to', []))]
    result['plans'] = [{name: deepcopy(plan[name]) for name in
                       ('id', 'version', 'business_date', 'items', 'baseline_committed', 'revision') if name in plan}
                      for plan in state.get('plans', [])
                      if any(item['event_id'] in selected_ids for item in plan.get('items', []))]
    for plan in result['plans']:
        plan['items'] = [{key: item[key] for key in ('event_id', 'rank', 'authority') if key in item}
                         for item in plan['items'] if item['event_id'] in selected_ids]
    result['pending_inputs'] = [{name: deepcopy(pending[name]) for name in
        ('id', 'version', 'source_message_id', 'source_intent_id', 'request', 'completion_condition', 'original_deadline',
         'requested_time', 'supplemental_requested_times', 'interpretation_status', 'requested_completion_condition', 'requested_event_id',
         'disposition', 'event_id', 'planning_requested', 'decision_revision', 'await_decision',
         'decision_state', 'decision_request_id') if name in pending}
        for pending in relevant_pending]
    # Reports retain actual service observations and evidence, not submission
    # snapshots or Planner recommendation fields masquerading as instructions.
    reports = [r for r in state.get('reports', []) if selected_ids.intersection(r.get('event_ids', []))]
    result['reports'] = [{name: deepcopy(report[name]) for name in
        ('id', 'version', 'report_id', 'revision', 'event_ids', 'resolution', 'origin_run_id', 'commitment_observation') if name in report}
        for report in reports]
    for projected, source in zip(result['reports'], reports):
        projected['observation'] = {name: deepcopy(source.get('payload', {})[name]) for name in
            ('facts', 'remaining_work', 'completion_assessments', 'evidence_refs', 'next_review_at')
            if name in source.get('payload', {})}
    result['execution_agenda'] = [project_event(app, family, event) for event in
        sorted(selected, key=lambda event: event_order(app, family, event))
        if event.get('status') not in {'done', 'cancelled'} and event.get('next_action')]
    from .pending_decision_context import pending_decision_contracts
    result['pending_decision_contracts'] = pending_decision_contracts(app, family, relevant_pending)
    result['arrangement_reply_contracts'] = arrangement_reply_contracts(app, family, binding)
    from .reschedule_projection import change_projection
    result.update(change_projection(app, family, selected_ids, message_ids))
    result['agenda_is_projection_not_queue'] = True
    return result



def arrangement_reply_contracts(app, family, binding):
    """Only this run's source-bound, committed makeup arrangement facts.

    The model still judges the actual response and selects resolves_intent.
    No message, Event, grant, reply scope or receipt is modified here.
    """
    from .family_candidates import message_content
    from .storage import digest
    from .candidates import derive
    from .scheduling import _versioned_record
    import json
    events = {e['id']: e for e in accepted_reply_events(app, family, binding)}
    contracts = []
    for pending in app.store.list('pending', family):
        event = events.get(pending.get('event_id'))
        scope = (binding or {}).get('reply_scope', {}).get(pending.get('source_message_id'), [])
        if (not event or event['completion']['kind'] != 'goal'
                or pending.get('source_kind') != 'host_verified_explicit_makeup'
                or pending.get('await_decision') is not False
                or pending.get('decision_state') not in {'created', 'linked'}
                or ('*' not in scope and pending.get('source_intent_id') not in scope)):
            continue
        grant = event.get('accepted_commitment') or {}
        # An alias currently lacks its own immutable candidate reference. Keep
        # its ordinary arrangement facts visible, but do not infer this stronger
        # reply contract from another source's grant snapshot.
        if (grant.get('source_pending_id') != pending['id'] or grant.get('status') != 'active'
                or grant.get('requirement_revision') != event.get('requirement_revision')):
            continue
        aliases = [{'pending_id': grant.get('source_pending_id'),
                    'commit_request_id': grant.get('commit_request_id'),
                    'source_message_ref': grant.get('source_message_ref') or {}}]
        aliases.extend(event.get('accepted_request_refs', []))
        if not pending.get('decision_request_id') or not any(
                ref.get('pending_id') == pending['id']
                and ref.get('commit_request_id') == pending['decision_request_id']
                and (ref.get('source_message_ref') or {}).get('id') == pending['source_message_id']
                for ref in aliases):
            continue
        try:
            source = app.store.get('candidate_source', pending['candidate_source_id'], family)
            message = app.store.get('message', pending['source_message_id'], family)
            candidate = _versioned_record(app, family, 'candidate', grant.get('candidate_ref'))
        except BusinessError:
            continue
        intent = next((i for i in message.get('intents', [])
                       if i['intent_id'] == pending.get('source_intent_id')), None)
        if (not intent or not intent['requires_reply'] or intent['reply_operation_refs']
                or source.get('source_kind') != 'family_makeup_request'
                or not source.get('enabled')
                or source.get('source_content_fingerprint') != digest(message_content(message))):
            continue
        current_source = derive(app, family, source)
        receipt = app.store.connection.execute('SELECT result FROM receipts WHERE family_id=? AND request_id=?',
            (family, grant.get('commit_request_id'))).fetchone()
        if (candidate.get('pending_id') != pending['id'] or candidate.get('source_id') != source['id']
                or current_source.get('status') != 'ready'
                or current_source.get('source_fingerprint') != candidate.get('source_fingerprint')
                or current_source.get('completion') != event.get('completion')
                or current_source.get('completion_condition') != event.get('completion_condition')
                or current_source.get('source_identity') != event.get('source_identity')
                or not receipt or grant['id'] not in json.loads(receipt['result']).get('data', {}).get('accepted_commitment_ids', [])):
            continue
        contracts.append({'source_message_id': message['id'], 'source_message_version': message['version'],
            'source_intent_id': intent['intent_id'], 'pending_id': pending['id'],
            'decision_request_id': pending['decision_request_id'], 'event_id': event['id'],
            'event_status': event['status'], 'commitment_status': grant.get('status'),
            'kind': 'accepted_makeup_arrangement',
            'reply_completion_basis': '据当前正式决定完整答复本安排意图，可选择resolves_intent=true；仍须核对实际正文、当前有效安排和原回复权限。',
            'goal_completion_is_separate': True,
            'meaning': '本来源进入补读目标安排，未成为独立文字交付委托。安排确认不要求先证明学习完成；学习目标继续沿原Event保留。模型service_kind标签不改变该宿主责任关系。',
            'authority': 'read_only_original_source_and_committed_decision_not_send_permission'})
    return contracts
