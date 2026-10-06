"""Bounded revision of an unconsumed report whose trusted learning source changed."""
from .domain import BusinessError, require
from .storage import digest


def stale_learning_sources(app, family, report):
    from .learning_evidence import ORIGIN, validate_adapter_evidence
    invalid, sources = [], {}
    for ref in report['payload']['evidence_refs']:
        if ref['kind']!='evidence':
            continue
        evidence=app.store.get('evidence',ref['id'],family)
        if evidence.get('source_origin')!=ORIGIN:
            continue
        try:
            require(evidence['version']==ref['version'],'EVIDENCE_SUPERSEDED','Learning evidence changed')
            validate_adapter_evidence(app,family,evidence)
        except BusinessError:
            invalid.append(ref)
            scope=evidence['source_snapshot']['scope_id']
            sources[scope]=app.store.get('learning_source',scope,family)
    return invalid, list(sources.values())


def observation_id(source):
    return 'learning-observation:'+digest([source['scope_id'],source['source_revision'],source['source_hash']])


def report_review_state(app, family, report):
    if report['resolution'] is not None:
        return None
    invalid,sources=stale_learning_sources(app,family,report)
    if not invalid:
        return None
    refs=[]
    for source in sources:
        ids=[observation_id(source), *source.get('evidence_ids',[]), *source.get('correction_ids',[])]
        for eid in ids:
            row=app.store.connection.execute("SELECT 1 FROM records WHERE kind='evidence' AND id=? AND family_id=?",
                                             (eid,family)).fetchone()
            if row:
                record=app.store.get('evidence',eid,family)
                # Event-specific corrections for unrelated Events are not this report's scope.
                if record.get('event_id') and record['event_id'] not in report['event_ids']:
                    continue
                refs.append({'kind':'evidence','id':eid,'version':record['version']})
    return {'invalidated_refs':invalid,'current_evidence_refs':refs,
            'fingerprint':digest([report['id'],report['version'],invalid,
                sorted((s['scope_id'],s['source_revision'],s['source_hash']) for s in sources)])}


def queue_report_revisions(app, family):
    """Called by host import transaction; never changes report text or resolution."""
    from .learning_evidence import ORIGIN
    from .jobs import JobQueue
    queued=[]
    for report in app.store.list('report',family):
        if report['resolution'] is not None:
            continue
        invalid,sources=stale_learning_sources(app,family,report)
        if not invalid:
            continue
        for source in sources:
            eid=observation_id(source)
            if not app.store.connection.execute("SELECT 1 FROM records WHERE kind='evidence' AND id=? AND family_id=?",
                                                 (eid,family)).fetchone():
                app.store.put('evidence',eid,family,{'kind':'learning_snapshot','trusted':True,'valid':True,
                    'source_origin':ORIGIN,'lesson_ref':source['lesson_ref'],'source_snapshot':source,
                    'source_locator':source['locator'],'source_revision':source['source_revision'],
                    'source_hash':source['source_hash'],'completion_status':source['completion_status'],
                    'source_refs':[]})
        state=report_review_state(app,family,report)
        key='review-report:'+digest([family,report['id'],state['fingerprint']])
        JobQueue(app).enqueue(family,key,{'kind':'review','event_id':report['event_ids'][0],
            'contact_allowed':False,'report_review_key':report['id'],
            'report_review_id':report['report_id'],'report_review_revision':report['revision'],
            'report_review_event_ids':report['event_ids'],'report_review_fingerprint':state['fingerprint'],
            'report_review_evidence_refs':state['current_evidence_refs']})
        queued.append(key)
    return queued


def report_review_allowed(app, family, job):
    if job.get('kind')!='review' or not job.get('report_review_key'):
        return False
    try:
        report=app.store.get('report',job['report_review_key'],family)
        state=report_review_state(app,family,report)
        return bool(state and state['current_evidence_refs']
            and state['fingerprint']==job.get('report_review_fingerprint')
            and report['report_id']==job.get('report_review_id')
            and report['revision']==job.get('report_review_revision')
            and set(report['event_ids'])==set(job.get('report_review_event_ids',[])))
    except BusinessError:
        return False


def validate_report_revision(app, family, binding, cmd):
    if not binding.get('report_review_key'):
        return
    job=app.store.get('job',binding['job_id'],family)
    require(report_review_allowed(app,family,job),'REPORT_REVIEW_STALE','Report/source changed since this review was queued')
    require(cmd.report_id==job['report_review_id'] and cmd.revision==job['report_review_revision']+1
            and cmd.supersedes_revision==job['report_review_revision'],
            'INVALID_REVISION','This review may only append the bound report next revision')
    require(set(cmd.event_ids)==set(job['report_review_event_ids']),
            'PROGRESS_SCOPE_VIOLATION','Revision cannot change the original Event set')
    refs=[r.model_dump() for r in cmd.evidence_refs]
    require(all(ref in refs for ref in job['report_review_evidence_refs']),
            'EVIDENCE_MISSING','Revision must reference the current host-verified source observations')


def revised_report(app, family, job):
    if not job.get('report_review_key'):
        return None
    key=app.report_key(family,job['report_review_id'],job['report_review_revision']+1)
    row=app.store.connection.execute("SELECT 1 FROM records WHERE kind='report' AND id=? AND family_id=?",
                                     (key,family)).fetchone()
    return app.store.get('report',key,family) if row else None
