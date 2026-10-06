"""Service-facing execution facts, never raw Planner proposals or decision rationale."""
from copy import deepcopy

from .domain import BusinessError
from .scheduling import require_step_authority, step_ready, event_order


EVENT_FIELDS = ('id', 'version', 'goal_key', 'goal_text', 'status', 'completion',
                'completion_condition', 'completion_contract_version', 'remaining_work',
                'due_at', 'waiting', 'next_action', 'update_pending', 'requirement_revision',
                'proposed_change', 'deadline_history', 'evidence_refs', 'creation_source_refs',
                'source_identity', 'original_deadline', 'requested_time', 'delivery_parts')


def project_event(app, family, event):
    value = {name: deepcopy(event[name]) for name in EVENT_FIELDS if name in event}
    grant = event.get('accepted_commitment')
    if grant:
        value['accepted_commitment'] = {name: deepcopy(grant[name]) for name in
            ('id', 'status', 'requirement_revision', 'authorized_target_refs', 'authorized_action_kinds',
             'valid_from', 'valid_until', 'schedule_revision', 'source_message_ref') if name in grant}
    try:
        authority = require_step_authority(app, family, event)
        ready = step_ready(app, family, event)
        value['execution'] = {'eligible_now': ready, **authority}
        if not ready:
            value['execution']['blocked_by'] = ('EXECUTION_MODE' if app.mode!='full'
                                                else 'ORIGINAL_OPERATION_REQUIRES_ACCOUNTING')
    except BusinessError as exc:
        value['execution'] = {'eligible_now': False, 'blocked_by': exc.code}
    return value


def responsibility_index(events):
    return [{name: deepcopy(event[name]) for name in
             ('id', 'version', 'goal_text', 'status', 'due_at', 'completion_condition') if name in event}
            for event in events if event.get('status') not in {'done', 'cancelled'}]


def project_service_state(app, family, state, binding=None, *, selected_event_ids=None):
    binding = binding or {}
    result = deepcopy(state)
    selected_ids = set(selected_event_ids or ())
    if binding.get('event_id'):
        selected_ids.add(binding['event_id'])
    selected_ids.update(binding.get('report_review_event_ids', []))
    selected = [event for event in state.get('events', []) if event['id'] in selected_ids]
    result['responsibility_index'] = responsibility_index(app.store.list('event', family))
    result['events'] = [project_event(app, family, event) for event in selected]
    message_ids = set(binding.get('reply_scope', {}))
    for event in selected:
        source = (event.get('accepted_commitment') or {}).get('source_message_ref') or {}
        if source.get('id'):
            message_ids.add(source['id'])
    relevant_pending = [p for p in state.get('pending_inputs', [])
               if p.get('event_id') in selected_ids or p.get('source_message_id') in message_ids]
    message_ids.update(p.get('source_message_id') for p in relevant_pending)
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
    result['agenda_is_projection_not_queue'] = True
    return result
