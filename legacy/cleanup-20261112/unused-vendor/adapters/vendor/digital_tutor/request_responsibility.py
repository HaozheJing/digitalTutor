"""Host-derived ownership of an accepted delivery; no second queue or model decisions."""
import json

from .domain import BusinessError, require, timestamp
from .report_contract import message_source_digest, resolve_refs
from .storage import digest


def _get(app, family, kind, key):
    if not key:
        return None
    row = app.store.connection.execute(
        'SELECT 1 FROM records WHERE kind=? AND id=? AND family_id=?', (kind, key, family)).fetchone()
    return app.store.get(kind, key, family) if row else None


def deliveries(app, family):
    """Keep the responsibility visible even if its current source needs review."""
    if not app.strict_runtime:
        return []
    result = []
    for pending in app.store.list('pending', family):
        if not pending.get('planning_requested') or pending.get('await_decision') or pending.get('decision_state') not in {'created', 'linked'}:
            continue
        event = _get(app, family, 'event', pending.get('event_id'))
        grant = (event or {}).get('accepted_commitment') or {}
        if not event or event['completion']['kind'] != 'delivery' or grant.get('status') != 'active':
            continue
        receipt = app.store.connection.execute('SELECT result FROM receipts WHERE family_id=? AND request_id=?',
            (family, pending.get('decision_request_id'))).fetchone()
        if not receipt or grant.get('id') not in json.loads(receipt['result']).get('data', {}).get('accepted_commitment_ids', []):
            continue
        message = _get(app, family, 'message', pending.get('source_message_id'))
        source = _get(app, family, 'candidate_source', pending.get('candidate_source_id'))
        intent = next((i for i in (message or {}).get('intents', [])
                       if i['intent_id'] == pending.get('source_intent_id')), None)
        if not message or not intent or not source or source.get('source_kind') != 'family_service_request':
            continue
        source_current = (source.get('enabled') and source.get('source_message_id') == message['id']
            and source.get('source_intent_id') == intent['intent_id']
            and source.get('source_message_digest') == message_source_digest(message)
            and source.get('completion') == event['completion']
            and source.get('delivery_parts') == event.get('delivery_parts')
            and source.get('completion_condition') == event.get('completion_condition')
            and source.get('source_identity') == event.get('source_identity')
            and grant.get('requirement_revision') == event.get('requirement_revision'))
        if source_current and grant.get('source_pending_id') == pending['id']:
            from .scheduling import _versioned_record, unchanged_text_request
            from .candidates import derive
            try:
                candidate = _versioned_record(app, family, 'candidate', grant.get('candidate_ref'))
                current = derive(app, family, source)
                source_current = current.get('status') == 'ready' and (
                    current.get('source_fingerprint') == candidate.get('source_fingerprint')
                    or unchanged_text_request(app, family, source, candidate))
            except BusinessError:
                source_current = False
        result.append({'pending': pending, 'event': event, 'message': message, 'intent': intent,
                       'source_current': bool(source_current)})
    return result


def _decision(pending):
    return {'pending_id': pending['id'], 'decision_revision': pending.get('decision_revision', 1),
            'decision_request_id': pending.get('decision_request_id'), 'event_id': pending['event_id']}


def decision_reply_refs(app, family, cmd):
    """Host-only phase identity for partial confirmation of an accepted delivery."""
    if not app.strict_runtime:
        return []
    partial = {(r.message_id, r.intent_id) for r in cmd.reply_to if not r.resolves_intent}
    refs = []
    for item in deliveries(app, family):
        pair = (item['message']['id'], item['intent']['intent_id'])
        if pair not in partial or not item['source_current'] or not item['intent']['requires_reply'] or item['intent']['reply_operation_refs']:
            continue
        refs.append({**_decision(item['pending']), 'source_message_id': pair[0],
                     'source_intent_id': pair[1],
                     'commitment_id': item['event']['accepted_commitment']['id']})
    return sorted(refs, key=lambda r: (r['source_message_id'], r['source_intent_id'], r['pending_id']))


def acknowledgements(app, family, run_id):
    """Called after tool effects, before outcome storage. Text is never evidence."""
    operations = [o for o in app.store.list('operation', family)
                  if o.get('run_id') == run_id and o['stage'] in {'sent', 'delivered'}]
    result = []
    for item in deliveries(app, family):
        if not item['source_current']:
            continue
        expected = {**_decision(item['pending']), 'source_message_id': item['message']['id'],
                    'source_intent_id': item['intent']['intent_id'],
                    'commitment_id': item['event']['accepted_commitment']['id']}
        ids = [op['id'] for op in operations if expected in op.get('decision_reply_refs', []) and any(
            ref['message_id'] == item['message']['id'] and ref['intent_id'] == item['intent']['intent_id']
            and not ref.get('resolves_intent', True) for ref in op.get('reply_to', []))]
        if ids:
            result.append({**_decision(item['pending']), 'operation_ids': sorted(ids),
                           'source_digest': message_source_digest(item['message'])})
    return result


def notified(app, family, item):
    expected = _decision(item['pending'])
    for outcome in app.store.list('run_outcome', family):
        for ack in outcome.get('delegation_acknowledgements', []):
            if not all(ack.get(k) == v for k, v in expected.items()):
                continue
            # Recheck real original effects, not a model-authored acknowledgement.
            for oid in ack.get('operation_ids', []):
                op = _get(app, family, 'operation', oid)
                if (op and op.get('run_id') == outcome.get('run_id') and op['stage'] in {'sent', 'delivered'}
                        and any(r['message_id'] == item['message']['id'] and r['intent_id'] == item['intent']['intent_id']
                                and not r.get('resolves_intent', True) for r in op.get('reply_to', []))):
                    return True
        # Compatibility: an old outcome observed this precise decided pending,
        # and the same original run actually sent its partial acknowledgement.
        if any(p.get('id') == item['pending']['id'] and p.get('version') == item['pending']['version']
               and not p.get('await_decision') for p in outcome.get('observed_planning_inputs', [])):
            if any(all(a.get(k) == v for k, v in expected.items())
                   for a in acknowledgements(app, family, outcome.get('run_id'))):
                return True
    return False


def actual_delivery_started(app, family, item):
    """Original step owns in-flight delivery; this does not prove notification or completion."""
    event = item['event']
    required = set(event['completion']['required_part_ids'])
    recipient = app.store.family(family)['recipient']
    return any(op.get('action_kind') == 'deliver_feedback'
               and op.get('event_id') == event['id']
               and op.get('requirement_revision') == event.get('requirement_revision')
               and op.get('recipient') == recipient and op.get('part_id') in required
               and op.get('stage') in {'dispatching', 'accepted', 'unknown', 'sent', 'delivered'}
               and _dispatched_under_grant(app, event, op)
               for op in app.store.list('operation', family))


def held_intents(app, family):
    return {(i['message']['id'], i['intent']['intent_id']) for i in deliveries(app, family)
            if not i['intent']['reply_operation_refs'] and
            (notified(app, family, i) or actual_delivery_started(app, family, i)
             or i['intent'].get('delegated_delivery_verified'))}


def filter_reply_scope(app, family, scope, all_accepted=False):
    held = ({(i['message']['id'], i['intent']['intent_id']) for i in deliveries(app, family)}
            if all_accepted else held_intents(app, family))
    return {mid: kept for mid, ids in scope.items()
            if (kept := [iid for iid in ids if (mid, iid) not in held])}


def service_waits_for_delivery(app, family, job):
    if not app.strict_runtime or job['kind'] != 'service':
        return False
    message = _get(app, family, 'message', job.get('message_id'))
    if not message or not message.get('interpreted'):
        return False
    pending = {(message['id'], i['intent_id']) for i in message['intents']
               if i['requires_reply'] and not i['reply_operation_refs']}
    return bool(pending and pending.issubset(held_intents(app, family)))


def check_reply(app, family, cmd, operation=None):
    """Apply at preparation and dispatch, including old queued capabilities."""
    if operation is not None:
        require(operation.get('decision_reply_refs', []) == decision_reply_refs(app, family, cmd),
                'DECISION_REPLY_STALE', 'Queued reply belongs to a different or no-longer-valid planning decision')
    from .acceptance_handoff import check_reply as check_acceptance_reply
    check_acceptance_reply(app, family, cmd, operation)
    awaiting = {(p.get('source_message_id'), p.get('source_intent_id'))
                for p in app.store.list('pending', family)
                if p.get('planning_requested') and p.get('await_decision')}
    by_pair = {(i['message']['id'], i['intent']['intent_id']): i for i in deliveries(app, family)}
    for ref in cmd.reply_to:
        require(not ref.resolves_intent or (ref.message_id, ref.intent_id) not in awaiting,
                'PLANNING_DECISION_PENDING',
                'The original request awaits a planning decision; registration must not settle its intent')
        item = by_pair.get((ref.message_id, ref.intent_id))
        if item and not item['intent']['reply_operation_refs']:
            require(not ref.resolves_intent, 'DELIVERY_RESPONSIBILITY_DELEGATED',
                    '原请求已正式承接；安排确认不结清交付，实际交付由原事项步骤执行')
            require(not notified(app, family, item) and not item['intent'].get('delegated_delivery_verified'), 'DELIVERY_RESPONSIBILITY_DELEGATED',
                    '原安排已确认，等待原事项履行；不能改普通reply重复交付')


def projection(app, family):
    return [{**_decision(i['pending']), 'source_message_id': i['message']['id'],
             'source_intent_id': i['intent']['intent_id'], 'source_current': i['source_current'],
             'arrangement_notified': notified(app, family, i), 'event_status': i['event']['status'],
             'request_settled': bool(i['intent']['reply_operation_refs']),
             'delivery_verified_pending_accounting': i['intent'].get('delegated_delivery_verified'),
             'authority': 'host_pending_and_committed_event',
             'contract': '确认只答复安排；未来交付由原Event步骤履行。真实交付及完成判断经progress核验后归还原请求事实，不再发同义总结。'}
            for i in deliveries(app, family)]


def settle_completed(app, family, events, report):
    """Inside the successful progress transaction; never rewrite operation identity."""
    done = {e['id']: e for e in events if e['status'] == 'done'}
    if not app.strict_runtime or not done:
        return
    # Keep family/ref validation even if there are no matching request records.
    resolve_refs(app, family, report['payload']['evidence_refs'], report)
    for item in deliveries(app, family):
        event = done.get(item['event']['id'])
        if not event or not item['source_current'] or item['intent']['reply_operation_refs']:
            continue
        fact = _delivery_fact(app, family, item, event, report)
        if not fact:
            continue
        message = app.store.get('message', item['message']['id'], family)
        intent = next(i for i in message['intents'] if i['intent_id'] == item['intent']['intent_id'])
        observations = _confirmations(app, family, item)
        if any(op['stage'] in {'queued', 'dispatching', 'accepted', 'unknown'} for op in observations):
            intent['delegated_delivery_verified'] = {**fact, 'confirmation_observations': observations}
        else:
            intent['reply_operation_refs'] = fact['operation_ids']
            intent['delegated_completion'] = {**fact, 'confirmation_observations': observations}
        app.store.put('message', message['id'], family, message, message['version'])


def _confirmations(app, family, item):
    return [{'id': op['id'], 'version': op['version'], 'stage': op['stage']}
            for op in app.store.list('operation', family) if any(
                ref['message_id'] == item['message']['id'] and ref['intent_id'] == item['intent']['intent_id']
                and not ref.get('resolves_intent', True) for ref in op.get('reply_to', []))]


def _delivery_fact(app, family, item, event, report):
    if not item['source_current'] or event['status'] != 'done' or report['resolution'] != 'applied':
        return None
    assessment = next((a for a in report['payload'].get('completion_assessments', [])
                       if a['event_id'] == event['id'] and a['complete']), None)
    if not assessment or (event.get('completion_condition') and assessment['criterion'].strip() != event['completion_condition'].strip()):
        return None
    records = resolve_refs(app, family, report['payload']['evidence_refs'], report)
    assessed = {(r['kind'], r['id'], r['version']) for r in assessment['evidence_refs']}
    ops = [op for kind, op in records if kind == 'operation' and op.get('event_id') == event['id']
           and op.get('requirement_revision') == event['requirement_revision']
           and op.get('recipient') == app.store.family(family)['recipient']
           and (op['stage'] == 'delivered' or (event['completion']['required_stage'] == 'sent' and op['stage'] == 'sent'))
           and ('operation', op['id'], op['version']) in assessed and _dispatched_under_grant(app, event, op)]
    if not set(event['completion']['required_part_ids']).issubset({op.get('part_id') for op in ops}):
        return None
    return {**_decision(item['pending']), 'report_key': report['id'],
            'requirement_revision': event['requirement_revision'], 'operation_ids': sorted({op['id'] for op in ops}),
            'source_digest': message_source_digest(item['message']), 'recorded_at': app.clock.iso(),
            'authority': 'host_verified_progress_completion'}


def ready_request_settlements(app, family, job):
    """Original-message internal recovery only, after every original ack is known."""
    if not app.strict_runtime or job['kind'] != 'service':
        return []
    result = []
    for item in deliveries(app, family):
        marker = item['intent'].get('delegated_delivery_verified')
        if (item['message']['id'] != job.get('message_id') or item['intent']['reply_operation_refs']
                or marker and marker.get('source_digest') != message_source_digest(item['message'])):
            continue
        observed = _confirmations(app, family, item)
        if any(op['stage'] in {'queued', 'dispatching', 'accepted', 'unknown'} for op in observed):
            continue
        # Legacy binding-sensitive checks could close the delivery Event while
        # leaving its original intent unsettled. Re-derive only from an applied
        # report and actual original grant-bound channel effects, never a model
        # assertion, new send, or replacement Event. Existing markers stay bound
        # to their exact report and original scope.
        reports = ([_get(app, family, 'report', marker.get('report_key'))] if marker else
                   [r for r in app.store.list('report', family)
                    if r.get('resolution') == 'applied' and item['event']['id'] in r.get('event_ids', [])])
        for report in reports:
            if not report:
                continue
            try:
                fact = _delivery_fact(app, family, item, item['event'], report)
            except BusinessError:
                continue
            if fact and (not marker or all(marker.get(k) == fact.get(k) for k in
                         ('pending_id', 'event_id', 'requirement_revision', 'operation_ids', 'source_digest'))):
                result.append({'message_id': item['message']['id'], 'intent_id': item['intent']['intent_id'],
                               'fact': {**fact, 'confirmation_observations': observed}})
                break
    return result


def request_completion_recovery(app, family, job):
    settlements = ready_request_settlements(app, family, job)
    if not settlements:
        return None
    ids = sorted({op['id'] for s in settlements for op in s['fact']['confirmation_observations']})
    return {'operation_ids': ids, 'signature': digest([s['fact']['confirmation_observations'] for s in settlements]),
            'missing_outcome': False}


def apply_request_settlements(app, family, settlements):
    refs = []
    for settlement in settlements:
        message = app.store.get('message', settlement['message_id'], family)
        intent = next(i for i in message['intents'] if i['intent_id'] == settlement['intent_id'])
        intent['reply_operation_refs'] = settlement['fact']['operation_ids']
        intent['delegated_completion'] = settlement['fact']
        saved = app.store.put('message', message['id'], family, message, message['version'])
        refs.append({'kind': 'message', 'id': saved['id'], 'version': saved['version']})
    if refs:
        app.store.bump(family)
    return refs


def _dispatched_under_grant(app, event, operation):
    """Past dispatch can use a receipted historical grant, never a live capability."""
    cap = operation.get('send_capability') or {}
    from .scheduling import operation_step_id
    if not (operation.get('dispatch_count') == 1 and operation.get('admitted_at')
            and cap.get('job_kind') == 'step' and cap.get('event_id') == event['id']
            and cap.get('current_step_key')
            and cap['current_step_key'] == operation_step_id(operation)):
        return False
    current = event.get('accepted_commitment') or {}
    grants = [current] + [h.get('accepted_commitment') or {} for h in event.get('schedule_history', [])]
    for grant in grants:
        if not (grant.get('id') and cap.get('commitment_id') == grant['id']
                and cap.get('schedule_revision') == grant.get('schedule_revision')):
            continue
        try:
            if grant != current:
                row = app.store.connection.execute('SELECT family_id FROM records WHERE kind=? AND id=?',
                    ('event', event['id'])).fetchone()
                if not row:return False
                from .reschedule_contract import historical_grant_receipt
                historical_grant_receipt(app, row['family_id'], event, grant)
            admitted = timestamp(operation['admitted_at'])
            from .domain import SHANGHAI
            if (timestamp(grant['valid_from']) <= admitted <= timestamp(grant['valid_until'])
                    and app.service_start <= admitted.astimezone(SHANGHAI).hour < app.service_end):
                return True
        except (BusinessError, KeyError):
            continue
    return False


def step_observation(app, family, event):
    """Only relevant external facts; exclude reports, audit and family versions."""
    from .control_scope import control_event_id, control_active
    grant = event.get('accepted_commitment') or {}
    message = _get(app, family, 'message', (grant.get('source_message_ref') or {}).get('id'))
    source_pending = _get(app, family, 'pending', grant.get('source_pending_id'))
    source = _get(app, family, 'candidate_source', (source_pending or {}).get('candidate_source_id'))
    actions = {a for a in grant.get('authorized_action_kinds', [])}
    actions.add((event.get('next_action') or {}).get('action_kind'))
    return {'grant': grant, 'source_message_digest': message_source_digest(message) if message else None,
            'source': source,
            'controls': [{'id': c['id'], 'version': c['version'], 'status': c['status'], 'effective':control_active(app,c)} for c in app.store.list('control', family)
                         if control_event_id(c) in {None, event['id']} and actions.intersection(c['action_kinds'])],
            'operations': [{'id': op['id'], 'version': op['version'], 'stage': op['stage']}
                           for op in app.store.list('operation', family) if op.get('event_id') == event['id']],
            'evidence': [{'id': e['id'], 'version': e['version']} for e in app.store.list('evidence', family)
                         if e.get('event_id') == event['id'] or e.get('lesson_ref') in event['completion']['lesson_refs']]}


def step_wait_unchanged(app, family, job):
    if not app.strict_runtime or job['kind'] != 'step' or not job.get('step_wait_fingerprint'):
        return False
    event = app.event(family, job['event_id'])
    from .scheduling import step_key
    if event['status'] in {'done', 'cancelled'} or step_key(event) != job['step_key']:
        return False
    return (digest(step_observation(app, family, event)) == job['step_wait_fingerprint']
            or job.get('step_resume_count', 0) >= 3)


def changed_step_recovery(app, family, job):
    if not app.strict_runtime or job['kind'] != 'step' or not job.get('step_wait_fingerprint') or step_wait_unchanged(app, family, job):
        return None
    # A changed receipt can justify internal reporting, never a new send.
    ops = [op for op in app.store.job_operations(family,job['id'])
           if op.get('send_capability', {}).get('job_id') == job['id']
           and op['stage'] in {'sent', 'delivered', 'unknown', 'accepted', 'dispatching'}]
    if not ops:
        return None
    return {'operation_ids': [op['id'] for op in ops],
            'signature': digest([(op['id'], op['version'], op['stage']) for op in ops]),
            'missing_outcome': False}


def progress_steps(app, family, events, report):
    """No lease ownership change: this runs after the service run released it."""
    if not app.strict_runtime:
        return
    from .scheduling import step_key
    by_id = {e['id']: e for e in events}
    for job in app.store.list('job', family):
        event = by_id.get(job.get('event_id'))
        if job['kind'] != 'step' or not event or job.get('run_id') != report.get('origin_run_id') or job['status'] == 'leased':
            continue
        closed = event['status'] in {'done', 'cancelled'} or step_key(event) != job['step_key']
        job.update(status='succeeded' if closed else 'waiting',
                   reason='step_resolved_by_progress' if closed else 'STEP_WAITING_NEW_FACT',
                   not_before=None, input_version=app.store.family(family)['version'],
                   step_report_key=report['id'])
        if not closed and not job.get('step_wait_fingerprint'):
            job['step_wait_fingerprint'] = digest(step_observation(app, family, event))
        app.store.put('job', job['id'], family, job, job['version'])
