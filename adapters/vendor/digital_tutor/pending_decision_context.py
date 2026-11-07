"""Read-only source decisions; recorded requests are not effective arrangements."""
from .domain import BusinessError


def pending_decision_contracts(app, family, pending_inputs):
    result = []
    for pending in pending_inputs:
        if not pending.get('planning_requested'):
            continue
        gaps = []
        candidate_status = 'unavailable'
        try:
            source = app.store.get('candidate_source', pending['candidate_source_id'], family)
            from .candidates import candidate_view
            candidate = candidate_view(app, family, source)
            gaps = candidate.get('gaps', [])
            candidate_status = candidate['status']
        except (BusinessError, KeyError):
            gaps = ['source_unavailable']
        try:
            message = app.store.get('message', pending['source_message_id'], family)
        except (BusinessError, KeyError):
            message = {'id': pending.get('source_message_id'), 'intents': []}
            gaps = [*gaps, 'original_message_unavailable']
        intent = next((i for i in message.get('intents', []) if i['intent_id'] == pending.get('source_intent_id')), {})
        accepted = (not pending.get('await_decision') and pending.get('decision_state') in {'created', 'linked'}
                    and bool(pending.get('event_id')))
        result.append({
            'pending_id': pending['id'], 'pending_version': pending['version'],
            'source_message_id': message['id'], 'source_intent_id': pending.get('source_intent_id'),
            'requested_event_id': pending.get('requested_event_id'), 'event_id': pending.get('event_id'),
            'decision_state': pending.get('decision_state'), 'await_decision': pending.get('await_decision'),
            'decision_reason': pending.get('decision_reason'), 'acceptance_recorded': accepted,
            'current_source_gaps': gaps, 'source_candidate_status': candidate_status,
            'requested_completion_condition': pending.get('requested_completion_condition'),
            'requested_time': pending.get('requested_time'),
            'supplemental_requested_times': pending.get('supplemental_requested_times', []),
            'interpretation_status': pending.get('interpretation_status'),
            'reply_operation_refs': list(intent.get('reply_operation_refs', [])),
            'partial_reply_operation_refs': list(intent.get('partial_reply_operation_refs', [])),
            'service_kind': intent.get('service_kind', 'reply'),
            'contract': 'retained只保留请求，不表示新增要求已接纳或归并生效；本条未改变原Event，现行状态另核。安排、登记或提出方案的reply可按真实处置答完本阶段，原goal完成及待处理增量另行保留。独立future_delivery不能仅凭未接受的安排或将来履行承诺结清；须有实际完整履行或明确拒绝。该投影不授予发送或结案权限。'})
    return result
