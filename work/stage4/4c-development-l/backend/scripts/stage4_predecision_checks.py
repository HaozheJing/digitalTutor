"""Post-run source-bound waiting checks only; never included in model input."""
import hashlib
import json
import sqlite3
from pathlib import Path

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_digest(message):
    value = {k: message.get(k) for k in
             ('id', 'external_id', 'body', 'occurred_at', 'direction', 'source_kind', 'interpreted')}
    value['intents'] = []
    for intent in message.get('intents', []):
        item = {k: intent.get(k) for k in ('intent_id', 'text', 'requires_reply')}
        if intent.get('service_kind', 'reply') != 'reply' or intent.get('service_authorization') is not None:
            item.update(service_kind=intent.get('service_kind', 'reply'),
                        service_authorization=intent.get('service_authorization'))
        value['intents'].append(item)
    return digest(value)


def coverage(records, channel):
    """One family's unresolved pairs must ALL retain source-backed decision ownership."""
    failures, covered = [], []
    operations = {o['id']: o for o in records.get('operation', [])}
    sources = {s['id']: s for s in records.get('candidate_source', [])}
    receipts = {r['operation_id']: r for r in channel}
    for message in records.get('message', []):
        if message.get('direction') != 'inbound':
            continue
        if not message.get('interpreted'):
            failures.append({'message_id': message['id'], 'reason': 'uninterpreted'})
            continue
        for intent in message.get('intents', []):
            if not intent.get('requires_reply') or intent.get('reply_operation_refs'):
                continue
            if not isinstance(intent.get('intent_id'), str) or not intent['intent_id'].strip():
                failures.append({'message_id': message['id'], 'reason': 'missing_intent_id'})
                continue
            pair = {'message_id': message['id'], 'intent_id': intent['intent_id']}
            matched = []
            for pending in records.get('pending', []):
                if not (pending.get('source_message_id') == message['id']
                        and pending.get('source_intent_id') == intent['intent_id']
                        and pending.get('planning_requested') is True
                        and pending.get('await_decision') is True
                        and pending.get('disposition') == 'pending'
                        and pending.get('event_id') is None):
                    continue
                source = sources.get(pending.get('candidate_source_id'), {})
                authorization = intent.get('service_authorization')
                if not (intent.get('service_kind') == 'future_delivery'
                        and isinstance(authorization, str) and authorization.strip()
                        and authorization in message['body']
                        and source.get('source_kind') == 'family_service_request'
                        and source.get('enabled') is True
                        and source.get('pending_id') == pending['id']
                        and source.get('source_message_id') == message['id']
                        and source.get('source_intent_id') == intent['intent_id']
                        and source.get('source_message_digest') == source_digest(message)):
                    continue
                partials = []
                for oid in intent.get('partial_reply_operation_refs', []):
                    op, receipt = operations.get(oid, {}), receipts.get(oid, {})
                    if (op.get('stage') in {'sent', 'delivered'}
                            and receipt.get('stage') == op.get('stage')
                            and receipt.get('payload', {}).get('body') == op.get('body')
                            and any(r.get('message_id') == message['id']
                                    and r.get('intent_id') == intent['intent_id']
                                    and r.get('resolves_intent', True) is False
                                    for r in op.get('reply_to', []))):
                        partials.append(oid)
                if not partials:
                    continue
                service = next((j for j in records.get('job', []) if j.get('kind') == 'service'
                                and j.get('message_id') == message['id'] and j.get('status') == 'waiting'), None)
                outcome = next((o for o in records.get('run_outcome', []) if service
                                and o['id'] == service.get('run_id') and o.get('job_id') == service['id']), {})
                observed = any(o.get('id') == pending['id'] and o.get('version') == pending['version']
                               and o.get('decision_revision') == pending.get('decision_revision')
                               and o.get('await_decision') is True
                               for o in outcome.get('observed_planning_inputs', []))
                bound = any(j.get('kind') == 'daily' and j.get('planning_reason') == 'admission'
                            and j.get('pending_id') == pending['id']
                            and j.get('decision_revision') == pending.get('decision_revision')
                            and j.get('status') in {'ready', 'waiting'} and j.get('attempt') == 0
                            for j in records.get('job', []))
                if (outcome.get('outcome') in {'waiting', 'needs_input'} and outcome.get('remaining_work')
                        and outcome.get('resume_condition') and observed and bound):
                    matched.append({'pending_id': pending['id'], 'partial_operation_ids': partials,
                                    'service_job_id': service['id'], 'outcome_id': outcome['id']})
            if len(matched) == 1:
                covered.append({**pair, **matched[0]})
            else:
                failures.append({**pair, 'reason': 'missing_or_ambiguous_source_bound_wait',
                                 'matching_count': len(matched)})
    return {'covered': covered, 'uncovered': failures,
            'all_unresolved_covered': bool(covered) and not failures}


def read_channel_rows(app):
    """Read an existing independent channel only; never create missing evidence."""
    channel = getattr(app, 'channel', None)
    if channel is not None:
        return [{'operation_id': row[0], 'payload': json.loads(row[1]), 'stage': row[2]}
                for row in channel.db.execute('SELECT operation_id,payload,stage FROM delivery')]
    path = Path(app.store.path).with_suffix('.channel.sqlite')
    if not path.is_file():
        return []
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return [{'operation_id': row[0], 'payload': json.loads(row[1]), 'stage': row[2]}
                for row in connection.execute('SELECT operation_id,payload,stage FROM delivery')]
    finally:
        connection.close()


def all_waiting_covered(families, channel):
    results = {family: coverage(records, channel) for family, records in families.items()}
    return bool(any(r['covered'] for r in results.values())
                and all(not r['uncovered'] for r in results.values())), results
