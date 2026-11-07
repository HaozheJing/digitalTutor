"""Host-derived acceptance handoff. No interpretation, schedule or send authority."""
import json

from .domain import BusinessError, require
from .report_contract import message_source_digest
from .reschedule_response import ORIGIN, validate_change_evidence


def _ref(evidence):
    return {'kind': 'evidence', 'id': evidence['id'], 'version': evidence['version']}


def phase(app, family, evidence):
    """Separate historical application, current applicability and pending review."""
    event = app.event(family, evidence['event_id'])
    message = app.store.get('message', evidence['source_message_ref']['id'], family)
    source_digest = message_source_digest(message)
    result = {'source_message_id': message['id'], 'source_intent_id': evidence['source_intent_id'],
              'source_digest': source_digest, 'event_id': event['id'],
              'change_id': evidence['change_id'], 'acceptance_evidence_ref': _ref(evidence)}
    history = next((h for h in event.get('schedule_history', [])
                    if h.get('change_id') == evidence['change_id']
                    and h.get('acceptance_evidence_ref', {}).get('id') == evidence['id']), None)
    if history:
        from .scheduling import _versioned_record
        from .reschedule_contract import validate_schedule_receipt
        original = _versioned_record(app, family, 'evidence', history['acceptance_evidence_ref'])
        validate_change_evidence(app, family, original)
        grant = history.get('applied_commitment') or {}
        require(grant.get('schedule_change_request_id') == history.get('commit_request_id')
                and grant.get('schedule_change_id') == evidence['change_id'],
                'COMMITMENT_INVALID', '接受生效历史必须绑定原提交和改约身份')
        validate_schedule_receipt(app, family, event, grant)
        result.update(phase='applied', commit_request_id=history['commit_request_id'],
                      current_schedule=(event.get('accepted_commitment') == grant),
                      current_source=(source_digest == evidence['source_message_digest']),
                      event_status=event['status'])
        return result
    # These are explicit invalidations, not an arbitrary validation error.
    closed = None
    if source_digest != evidence['source_message_digest']:
        closed = 'source_corrected'
    elif not evidence.get('valid') and evidence.get('superseded_by_response_id'):
        newer = app.store.get('change_response', evidence['superseded_by_response_id'], family)
        require(newer.get('change_id') == evidence['change_id']
                and newer.get('interpretation') != 'unrelated',
                'EVIDENCE_INVALID', '接受失效须对应真实后续实质回应')
        closed = 'response_superseded'
    elif event['status'] in {'done', 'cancelled'}:
        closed = 'event_closed'
    elif (event.get('proposed_change') or {}) != evidence['proposal_snapshot']['proposal']:
        closed = 'proposal_superseded'
    if closed:
        result.update(phase='superseded_or_closed', reason=closed, event_status=event['status'])
        return result
    try:
        validate_change_evidence(app, family, evidence, for_consumption=True)
    except BusinessError as error:
        if error.code != 'CHANGE_RESPONSE_REVIEW_REQUIRED':
            raise
        result.update(phase='requires_review', reason=error.code)
    else:
        result.update(phase='pending_application')
    return result


def scoped(app, family, scope, run_id=None, event_ids=None):
    if not app.strict_runtime:
        return []
    result = []
    for evidence in app.store.list('evidence', family):
        if evidence.get('source_origin') != ORIGIN or evidence.get('kind') != 'accept_change':
            continue
        mid = evidence['source_message_ref']['id']
        iid = evidence['source_intent_id']
        allowed = scope.get(mid, [])
        message = app.store.get('message', mid, family)
        intent = next((i for i in message.get('intents', []) if i['intent_id'] == iid), None)
        in_scope = ('*' in allowed or iid in allowed) and intent and intent['requires_reply'] and not intent['reply_operation_refs']
        same_run = run_id is not None and evidence.get('origin_run_id') == run_id
        if (in_scope or same_run) and (event_ids is None or evidence['event_id'] in event_ids):
            result.append(phase(app, family, evidence))
    return sorted(result, key=lambda p: (p['source_message_id'], p['source_intent_id'], p['acceptance_evidence_ref']['id']))


def validate_report(app, family, binding, cmd):
    """New reports only. A canonical old report replay returns before this guard."""
    if binding.get('job_kind') != 'service':
        return
    supplied = [r.model_dump() for r in cmd.evidence_refs]
    for item in scoped(app, family, binding.get('reply_scope', {}), binding.get('run_id'), set(cmd.event_ids)):
        if item['phase'] == 'pending_application':
            require(item['acceptance_evidence_ref'] in supplied, 'CHANGE_ACCEPTANCE_EVIDENCE_REQUIRED',
                    '本报告原事项有本轮或同未答来源的有效接受；显式引用真实凭证：'
                    + json.dumps(item['acceptance_evidence_ref'], ensure_ascii=False))


def reply_phases(app, family, cmd):
    scope = {}
    for ref in cmd.reply_to:
        scope.setdefault(ref.message_id, []).append(ref.intent_id)
    return scoped(app, family, scope)


def reply_identity(items):
    # Current status/schedule flags constrain a prepared send but their changes
    # are not another reply obligation. Family versions and clocks never enter.
    keys = ('source_message_id', 'source_intent_id', 'source_digest', 'event_id',
            'change_id', 'acceptance_evidence_ref', 'phase', 'commit_request_id')
    return [{key: item[key] for key in keys if key in item} for item in items]


def check_reply(app, family, cmd, operation=None):
    if not app.strict_runtime:
        return
    items = reply_phases(app, family, cmd)
    if operation is not None:
        require(operation.get('acceptance_reply_phases', []) == items,
                'CHANGE_REPLY_STALE', '原排队回复的接受处置阶段已变化，不能重标旧操作后发送')
    final = {(r.message_id, r.intent_id) for r in cmd.reply_to if r.resolves_intent}
    partial = {(r.message_id, r.intent_id) for r in cmd.reply_to if not r.resolves_intent}
    for item in items:
        pair = (item['source_message_id'], item['source_intent_id'])
        if pair in partial:
            # An old confirmed partial has no reliable acceptance-phase label.
            # Reading it remains legal; do not label it retroactively or send a
            # second partial. A real applied/closed final disposition can follow.
            for prior in app.store.list('operation', family):
                if (prior['stage'] in {'sent', 'delivered'} and not prior.get('acceptance_reply_phases')
                        and any((r['message_id'], r['intent_id']) == pair and not r.get('resolves_intent', True)
                                for r in prior.get('reply_to', []))):
                    require(False, 'CHANGE_LEGACY_REPLY_EXISTS',
                            '原来源已有未标记接受阶段的实际部分答复，不能重标后重复发送：' + prior['id'])
        if pair in final:
            require(item['phase'] in {'applied', 'superseded_or_closed'}, 'CHANGE_APPLICATION_PENDING',
                    '接受尚未生效或后续来信待复核；保留原意图。可说明真实待处理状态，不能最终确认新安排。')


def pending_reports(app, family, item):
    from .learning_review import stale_learning_sources
    jobs = {j['id']: j for j in app.store.list('job', family)}
    result = []
    for report in app.store.list('report', family):
        if (report.get('resolution') is not None or item['event_id'] not in report.get('event_ids', [])
                or item['acceptance_evidence_ref'] not in report.get('payload', {}).get('evidence_refs', [])):
            continue
        job = jobs.get('progress:' + report['id'], {})
        if (job.get('status') in {'ready', 'leased', 'waiting'}
                and job.get('reason') != 'REPORT_REVISION_REQUIRED'
                and not stale_learning_sources(app, family, report)[0]):
            result.append(report)
    return result


def require_handoff(app, family, binding):
    if binding.get('job_kind') != 'service' or binding.get('recovery_only'):
        return
    for item in scoped(app, family, binding.get('reply_scope', {}), binding.get('run_id')):
        if item['phase'] == 'pending_application':
            require(pending_reports(app, family, item), 'CHANGE_ACCEPTANCE_REPORT_REQUIRED',
                    '本次有效接受仍待交接，结束运行前提交含真实接受凭证的原事项报告：'
                    + json.dumps(item['acceptance_evidence_ref'], ensure_ascii=False))


def service_waits_for_progress(app, family, job):
    """Yield only when every currently runnable intent has a real pending report.

    Does not depend on the job's original message being the acceptance source;
    mixed input, missing/revised reports and consumed reports remain runnable.
    """
    if not app.strict_runtime or job['kind'] != 'service':
        return False
    from .runtime_config import reply_scope
    scope = reply_scope(app, family)
    wanted = {(mid, iid) for mid, ids in scope.items() for iid in ids}
    items = scoped(app, family, scope)
    held = {(i['source_message_id'], i['source_intent_id']) for i in items
            if i['phase'] == 'pending_application' and pending_reports(app, family, i)}
    return bool(wanted and wanted.issubset(held))
