"""Read-only lateness projection and one internal review per original acceptance."""
from .domain import BusinessError, require, timestamp
from .storage import digest
import json

ORIGIN='host_commitment_expiration_v1'


def expired_commitment(app, event):
    grant=event.get('accepted_commitment') or {}
    if not app.strict_runtime or grant.get('status')!='active' or event['status'] in {'done','cancelled'}:
        return None
    try:
        end=timestamp(grant['valid_until'])
        now=app.clock.now()
        expired=now>end or (now>=end and not app.service_start<=now.hour<app.service_end)
        if expired and grant.get('id') and grant.get('schedule_revision') and grant.get('authorized_step_key'):
            return grant
    except (BusinessError,KeyError):
        return None
    return None


def observe_commitment(app, family, event):
    from .scheduling import operation_step_id
    grant=expired_commitment(app,event)
    if not grant:
        return None
    operations=[op for op in app.store.list('operation',family) if op.get('event_id')==event['id'] and (
        operation_step_id(op)==grant['authorized_step_key'] or
        (op.get('send_capability',{}).get('commitment_id')==grant['id'] and
         op.get('send_capability',{}).get('schedule_revision')==grant['schedule_revision']))]
    if any(op['stage'] in {'unknown','accepted','dispatching'} for op in operations):
        status='original_receipt_uncertain'
    elif any(op['stage'] in {'sent','delivered'} for op in operations):
        status='original_delivery_confirmed'
    elif operations:
        status='original_operations_without_confirmed_delivery'
    else:
        status='no_recorded_external_execution'
    return {'event_id':event['id'],'commitment_id':grant['id'],'schedule_revision':grant['schedule_revision'],
            'original_step_key':grant['authorized_step_key'],'valid_from':grant['valid_from'],
            'valid_until':grant['valid_until'],'original_due_at':event.get('due_at'),
            'original_deadline':event.get('original_deadline'),'observed_at':app.clock.iso(),
            'execution_fact':status,'grant_expired':True,'authorizes_contact':False,
            'operations':[{'id':op['id'],'version':op['version'],'stage':op['stage'],
                'dispatch_count':op.get('dispatch_count',0)} for op in operations],
            'interpretation':'期限已过只说明原授权窗口结束；有已发或未知原操作时不能推断未执行。保留原责任和期限，核原号并报告，不再次外发或自行延长承诺。'}


def enqueue_expired_review(app, family, event):
    grant=expired_commitment(app,event)
    if not grant:
        return False
    from .jobs import JobQueue
    key='review-expired:'+digest([family,event['id'],grant['id'],grant['schedule_revision'],grant['authorized_step_key']])
    return JobQueue(app).enqueue(family,key,{'kind':'review','event_id':event['id'],'contact_allowed':False,
        'commitment_review_id':grant['id'],'commitment_review_revision':grant['schedule_revision'],
        'commitment_review_step_key':grant['authorized_step_key']})


def expiration_review_allowed(app, family, job):
    if job.get('kind')!='review' or not job.get('commitment_review_id'):
        return False
    event=app.event(family,job['event_id']);grant=expired_commitment(app,event)
    return bool(grant and grant['id']==job['commitment_review_id']
                and grant['schedule_revision']==job.get('commitment_review_revision')
                and grant['authorized_step_key']==job.get('commitment_review_step_key'))


def _source_facts(app, family, event):
    grant=event['accepted_commitment']
    source_ref=grant.get('source_message_ref') or {}
    from .report_contract import message_source_digest
    source_digest=message_source_digest(app.store.get('message',source_ref['id'],family)) if source_ref.get('id') else None
    return {'family_id':family,'event_id':event['id'],'accepted_commitment':grant,
            'requirement_revision':event.get('requirement_revision'),'completion':event['completion'],
            'source_identity':event.get('source_identity'),'completion_condition':event.get('completion_condition'),
            'due_at':event.get('due_at'),'original_deadline':event.get('original_deadline'),
            'current_source_message_digest':source_digest}


def _stable_observation(value):
    return {key:value[key] for key in value if key!='observed_at'}


def ensure_expiration_evidence(app, family, event):
    """Host-derived claim/context observation. Identical facts retain one record."""
    observation=observe_commitment(app,family,event)
    require(observation is not None,'COMMITMENT_REVIEW_STALE','Original window is no longer expired')
    source=_source_facts(app,family,event)
    key='commitment-expired:'+digest([family,source,_stable_observation(observation)])
    if app.store.connection.execute("SELECT 1 FROM records WHERE kind='evidence' AND id=? AND family_id=?",(key,family)).fetchone():
        evidence=app.store.get('evidence',key,family)
        validate_expiration_evidence(app,family,evidence)
    else:
        evidence=app.store.put('evidence',key,family,{'kind':'commitment_expired','source_origin':ORIGIN,
            'event_id':event['id'],'trusted':True,'valid':True,'source_facts':source,
            'observation':observation,'usable_for_completion':False,'source_refs':[]})
    return {'kind':'evidence','id':evidence['id'],'version':evidence['version']}


def validate_expiration_evidence(app, family, evidence, *, historical_operations=False):
    if evidence.get('source_origin')!=ORIGIN:
        return
    require(evidence.get('kind')=='commitment_expired' and evidence.get('trusted') is True
            and evidence.get('valid') is True and evidence.get('usable_for_completion') is False,
            'EVIDENCE_SUPERSEDED','Invalid expiration evidence')
    event=app.event(family,evidence['event_id'])
    current=observe_commitment(app,family,event)
    require(current is not None,'EVIDENCE_SUPERSEDED','Original expiration no longer applies')
    source=_source_facts(app,family,event);observation=evidence['observation']
    require(source==evidence['source_facts'] and evidence['id']=='commitment-expired:'+digest(
        [family,source,_stable_observation(observation)]),'EVIDENCE_SUPERSEDED','Original grant/source or observed fact changed')
    require(timestamp(observation['observed_at'])<=app.clock.now()
            and timestamp(observation['observed_at'])>=timestamp(source['accepted_commitment']['valid_until']),
            'EVIDENCE_SUPERSEDED','Expiration observation time is not valid')
    if not historical_operations:
        require(_stable_observation(current)==_stable_observation(observation),
                'EVIDENCE_SUPERSEDED','Original operation facts changed; read current facts before reporting')
    else:
        # A submitted report records what was known then. Unknown becoming sent
        # does not erase that observation or make it proof of final delivery.
        for operation in observation['operations']:
            row=app.store.connection.execute('SELECT data FROM history WHERE family_id=? AND kind=? AND id=? AND version=?',
                (family,'operation',operation['id'],operation['version'])).fetchone()
            require(row is not None,'EVIDENCE_SUPERSEDED','Original operation history is missing')
            old=json.loads(row['data'])
            require(old.get('event_id')==event['id'] and old.get('stage')==operation['stage']
                    and old.get('dispatch_count',0)==operation['dispatch_count'],
                    'EVIDENCE_SUPERSEDED','Original operation observation is not authentic')


def validate_expiration_report(app, family, binding, cmd):
    if not binding.get('commitment_review_id'):
        return
    job=app.store.get('job',binding['job_id'],family)
    require(expiration_review_allowed(app,family,job),'COMMITMENT_REVIEW_STALE','Original acceptance changed')
    require(cmd.event_ids==[job['event_id']],'PROGRESS_SCOPE_VIOLATION','Expiry review is bound to its original Event')
    event=app.event(family,job['event_id'])
    refs=[r.model_dump() for r in cmd.evidence_refs]
    required=ensure_expiration_evidence(app,family,event)
    require(required and required in refs,'EVIDENCE_MISSING','Expiry report must cite the host-derived original acceptance observation')
    validate_expiration_evidence(app,family,app.store.get('evidence',required['id'],family))
    # The evidence contains verified original-operation observations. A separate
    # operation ref is optional, and is needed for any actual delivery claim.


def report_observation(app, family, binding):
    if not binding.get('commitment_review_id'):
        return None
    return observe_commitment(app,family,app.event(family,binding['event_id']))


def has_expiration_report(app, family, job):
    return any(report.get('origin_run_id')==job.get('run_id') and
        report.get('event_ids')==[job['event_id']] and
        (report.get('commitment_observation') or {}).get('commitment_id')==job.get('commitment_review_id') and
        report['commitment_observation']['schedule_revision']==job.get('commitment_review_revision')
        for report in app.store.list('report',family))
