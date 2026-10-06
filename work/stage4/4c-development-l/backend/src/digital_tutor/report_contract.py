"""Versioned service observations; immutable source snapshots are host-owned."""
import json
from .domain import require
from .storage import digest


def message_source_digest(message):
    value = {key: message.get(key) for key in
             ('id', 'external_id', 'body', 'occurred_at', 'direction', 'source_kind', 'interpreted')}
    value['intents'] = [{k: i.get(k) for k in ('intent_id', 'text', 'requires_reply')}
                        for i in message.get('intents', [])]
    for original, semantic in zip(message.get('intents', []), value['intents']):
        # Absent/default reply is the historical contract; new authorization is
        # source semantics, unlike the derived receipt/completion fields.
        if original.get('service_kind', 'reply') != 'reply' or original.get('service_authorization') is not None:
            semantic.update(service_kind=original.get('service_kind', 'reply'),
                            service_authorization=original.get('service_authorization'))
    return digest(value)


def snapshots(app, family, refs):
    result = []
    for ref in refs:
        ref = ref.model_dump() if hasattr(ref, 'model_dump') else ref
        record = app.store.get(ref['kind'], ref['id'], family)
        require(record['version'] == ref['version'], 'EVIDENCE_SUPERSEDED', '报告来源已变化')
        result.append({'ref': ref, 'message_digest': message_source_digest(record)
                       if ref['kind'] == 'message' else None})
    return result


def resolve_refs(app, family, refs, report=None):
    """Only report-bound message receipt metadata may advance; all else stays strict."""
    result = []
    allowed = {(r['kind'], r['id'], r['version']) for r in
               (report or {}).get('payload', {}).get('evidence_refs', [])}
    for raw in refs:
        ref = raw.model_dump() if hasattr(raw, 'model_dump') else raw
        record = app.store.get(ref['kind'], ref['id'], family)
        if record['version'] != ref['version']:
            identity = (ref['kind'], ref['id'], ref['version'])
            require(report and ref['kind'] == 'message' and identity in allowed,
                    'EVIDENCE_SUPERSEDED', '证据版本已变化')
            snapshot = next((x for x in report.get('source_snapshots', []) if x['ref'] == ref), None)
            if snapshot:
                expected = snapshot['message_digest']
            else:
                # Historical reports can use immutable original history, never a guessed latest ref.
                row = app.store.connection.execute(
                    'SELECT data FROM history WHERE kind=? AND id=? AND family_id=? AND version=?',
                    (ref['kind'], ref['id'], family, ref['version'])).fetchone()
                require(row is not None, 'EVIDENCE_SUPERSEDED', '旧报告缺少可核的原来源快照')
                expected = message_source_digest({'id': ref['id'], **json.loads(row['data'])})
            require(expected == message_source_digest(record), 'EVIDENCE_SUPERSEDED', '消息业务内容或意图已更正')
        if ref['kind']=='evidence':
            from .learning_evidence import validate_adapter_evidence
            validate_adapter_evidence(app,family,record)
            from .commitment_review import validate_expiration_evidence
            validate_expiration_evidence(app,family,record,historical_operations=bool(report and
                (ref['kind'],ref['id'],ref['version']) in allowed))
        result.append((ref['kind'], record))
    return result


def validate_assessments(app, family, cmd):
    seen = set()
    allowed = {tuple(sorted(r.model_dump().items())) for r in cmd.evidence_refs}
    for assessment in cmd.completion_assessments:
        require(assessment.event_id in cmd.event_ids and assessment.event_id not in seen,
                'PROGRESS_SCOPE_VIOLATION', '完成判断必须唯一绑定报告原事项')
        seen.add(assessment.event_id)
        require(all(tuple(sorted(r.model_dump().items())) in allowed for r in assessment.evidence_refs),
                'EVIDENCE_MISSING', '判断依据必须属于本报告实际证据')
        event = app.event(family, assessment.event_id)
        condition = (event.get('completion_condition') or '').strip()
        require(not condition or assessment.criterion.strip() == condition,
                'COMPLETION_CONDITION_MISMATCH', '不得替换已有明确完成说明')


def require_assessment(app, family, event, report):
    if not event.get('completion_contract_version'):
        return
    require(report is not None, 'SERVICE_ASSESSMENT_REQUIRED', '新事项完成需Service真实结果判断')
    assessment = next((a for a in report['payload'].get('completion_assessments', [])
                       if a['event_id'] == event['id']), None)
    require(assessment and assessment['complete'], 'SERVICE_ASSESSMENT_REQUIRED', 'Service未判断原事项完成')
    condition = (event.get('completion_condition') or '').strip()
    require(not condition or assessment['criterion'].strip() == condition,
            'COMPLETION_CONDITION_MISMATCH', '判断与原条件不一致')
    resolve_refs(app, family, assessment['evidence_refs'], report)


def report_payload(cmd, legacy=False):
    payload = cmd.model_dump()
    if not payload['completion_assessments']:
        payload.pop('completion_assessments')
    if legacy and payload['remaining_work'] is None:
        payload['remaining_work'] = []
    return payload


def compact_new_defaults(payload):
    """Preserve original request digests when all added fields carry no information."""
    if isinstance(payload, list):
        return [compact_new_defaults(v) for v in payload]
    if not isinstance(payload, dict):
        return payload
    return {k: compact_new_defaults(v) for k, v in payload.items()
            if not (k in {'execute_at', 'completion_condition'} and v is None)}
