"""Unapplied read-only projection for original change sources and decisions."""
from copy import deepcopy
from .domain import BusinessError
from .reschedule_response import ORIGIN, validate_change_evidence


def source_message_ids(app, family, event_ids):
    wanted = set()
    for event in app.store.list('event', family):
        if event['id'] in event_ids:
            source = (event.get('proposed_change') or {}).get('source_message_ref') or {}
            if source.get('id'):wanted.add(source['id'])
    wanted.update(r['source_message_ref']['id'] for r in app.store.list('change_response', family)
                  if r.get('event_id') in event_ids)
    return wanted


def change_projection(app, family, event_ids, message_ids):
    responses = [deepcopy(r) for r in app.store.list('change_response', family)
                 if r.get('event_id') in event_ids or r.get('source_message_ref', {}).get('id') in message_ids]
    response_ids = {r['id'] for r in responses}
    operation_ids = {r['proposal_operation_id'] for r in responses}
    evidence = []
    for item in app.store.list('evidence', family):
        if item.get('source_origin') != ORIGIN or item.get('response_ref', {}).get('id') not in response_ids:
            continue
        current = True
        reason = None
        try:validate_change_evidence(app, family, item)
        except BusinessError as error:
            current = False
            reason = error.code
        consumable = current
        consumption_reason = reason
        if consumable:
            try:validate_change_evidence(app, family, item, for_consumption=True)
            except BusinessError as error:
                consumable = False
                consumption_reason = error.code
        evidence.append({**deepcopy(item), 'historical_reference_valid': current, 'reference_blocked_by': reason,
                         'acceptance_consumable': consumable, 'consumption_blocked_by': consumption_reason,
                         'record_kind': 'evidence', 'reference': {'kind': 'evidence', 'id': item['id'], 'version': item['version']}})
    operations = [{key: deepcopy(op[key]) for key in
                  ('id', 'version', 'stage', 'body', 'recipient', 'content_digest', 'receipts',
                   'proposal_snapshot', 'occurred_at', 'admitted_at', 'reply_to') if key in op}
                  for op in app.store.list('operation', family) if op.get('proposal_snapshot') and (
                      op['id'] in operation_ids or op['proposal_snapshot'].get('event_id') in event_ids
                      or any(ref['message_id'] in message_ids for ref in op.get('reply_to', [])))]
    return {'change_responses': responses, 'change_acceptance_evidence': evidence, 'proposal_operations': operations,
            'change_response_contract': '回应解释属于模型判断；实际来源与送达关系由宿主核验。接受凭证不是生效改约，须原报告/progress提交；投影不增加发送权限。'}
