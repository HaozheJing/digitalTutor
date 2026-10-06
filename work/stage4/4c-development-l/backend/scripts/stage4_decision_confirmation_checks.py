"""Independent post-run decision-confirmation evidence, never used as model input."""
from stage4_predecision_checks import source_digest


def decision_confirmation_evidence(records, channel, receipts, recipient):
    events = {e['id']: e for e in records.get('event', [])}
    messages = {m['id']: m for m in records.get('message', [])}
    sources = {s['id']: s for s in records.get('candidate_source', [])}
    deliveries = {o['operation_id']: o for o in channel}
    selected = [p for p in records.get('pending', [])
                if p.get('planning_requested') is True and p.get('await_decision') is False
                and p.get('decision_state') in {'created', 'linked'}
                and events.get(p.get('event_id'), {}).get('completion', {}).get('kind') == 'delivery']
    found, missing = [], []
    for pending in selected:
        event = events[pending['event_id']]
        grant = event.get('accepted_commitment') or {}
        receipt = receipts.get(pending.get('decision_request_id'), {})
        message = messages.get(pending.get('source_message_id'), {})
        intent = next((i for i in message.get('intents', [])
                       if i['intent_id'] == pending.get('source_intent_id')), {})
        source = sources.get(pending.get('candidate_source_id'), {})
        decision = {k: pending.get(k) for k in ('decision_revision', 'decision_request_id', 'event_id')}
        decision['pending_id'] = pending['id']
        phase = {**decision, 'source_message_id': pending.get('source_message_id'),
                 'source_intent_id': pending.get('source_intent_id'), 'commitment_id': grant.get('id')}
        source_ok = bool(message and intent and grant.get('id')
            and grant.get('id') in receipt.get('data', {}).get('accepted_commitment_ids', [])
            and source.get('enabled') is True and source.get('source_kind') == 'family_service_request'
            and source.get('pending_id') == pending['id']
            and source.get('source_message_id') == message.get('id')
            and source.get('source_intent_id') == intent.get('intent_id')
            and source.get('source_message_digest') == source_digest(message))
        matches = []
        for op in records.get('operation', []):
            physical = deliveries.get(op['id'], {})
            if not (source_ok and phase in op.get('decision_reply_refs', [])
                    and op.get('action_kind') in {'reply', 'clarify_existing'}
                    and op.get('stage') in {'sent', 'delivered'} and op.get('dispatch_count') == 1
                    and op.get('admitted_at') and op.get('recipient') == recipient
                    and op['id'] in intent.get('partial_reply_operation_refs', [])
                    and any(r.get('message_id') == message.get('id')
                            and r.get('intent_id') == intent.get('intent_id')
                            and r.get('resolves_intent', True) is False for r in op.get('reply_to', []))
                    and physical.get('stage') == op['stage']
                    and all(physical.get('payload', {}).get(k) == op.get(k)
                            for k in ('body', 'recipient', 'admitted_at'))):
                continue
            has_ack = any(outcome.get('run_id') == op.get('run_id')
                          and any(all(ack.get(k) == v for k, v in decision.items())
                                  and op['id'] in ack.get('operation_ids', [])
                                  and ack.get('source_digest') == source_digest(message)
                                  for ack in outcome.get('delegation_acknowledgements', []))
                          for outcome in records.get('run_outcome', []))
            if has_ack:
                matches.append({'operation_id': op['id'], 'run_id': op['run_id'],
                                'admitted_at': op['admitted_at'], 'phase': phase})
        if matches:
            found.append({'pending_id': pending['id'], 'confirmations': matches})
        else:
            missing.append({'pending_id': pending['id'], 'phase': phase,
                            'source_and_commit_receipt_valid': source_ok})
    return {'accepted_delivery_has_decision_confirmation': bool(selected) and not missing,
            'expected_pending_ids': [p['id'] for p in selected], 'matched': found, 'missing': missing}
