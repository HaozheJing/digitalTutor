"""Host-only evidence adapter for versioned synthetic learning snapshots.

The model cannot supply trusted facts, evidence identities, targets or corrections.
"""
import json
from .domain import BusinessError,require,timestamp
from .storage import digest

ORIGIN='synthetic_learning_adapter_v1'


def source_snapshot(app,family,batch_id,day):
    uid=app.store.family(family)['uid']
    row=app.store.connection.execute('SELECT * FROM learning_days WHERE batch_id=? AND uid=? AND day=?',
                                  (batch_id,uid,day)).fetchone()
    imported=app.store.connection.execute('SELECT metadata FROM imports WHERE batch_id=?',(batch_id,)).fetchone()
    require(row is not None and imported is not None,'LEARNING_SOURCE_MISSING','No source row in bound family')
    row=dict(row);meta=json.loads(imported[0]);contract=meta.get('source_contract',{})
    require(meta.get('synthetic') is True and contract.get('kind')=='versioned_learning_snapshot',
            'SOURCE_CONTRACT_MISSING','This proposal only supports explicitly synthetic versioned sources')
    require(isinstance(contract.get('source_id'),str) and contract['source_id']
            and type(contract.get('revision')) is int and contract['revision']>=1,
            'SOURCE_CONTRACT_MISSING','Stable dataset identity and positive integer revision are required')
    bindings=[b for b in app.store.list('course_binding',family) if b['batch_id']==batch_id]
    require(len(bindings)==1,'COURSE_BINDING_MISSING','Exactly one confirmed source binding is required')
    binding=bindings[0]
    lessons=[m['lesson_ref'] for m in binding.get('lesson_map') or [] if m['day']==day]
    require(binding.get('source_kind')=='trusted_host_confirmation' and binding.get('course_id')
            and binding.get('course_version') and len(lessons)==1,
            'COURSE_BINDING_MISSING','Source day must map to an authoritative original lesson')
    required=contract.get('observed_at')
    require(required and meta.get('coverage_through') and binding.get('coverage_through') and meta.get('imported_at'),
            'COVERAGE_MISSING','Coverage and observation timestamps are required')
    observed=timestamp(required)
    require(timestamp(meta['coverage_through'])>=observed and timestamp(binding['coverage_through'])>=observed
            and observed<=timestamp(meta['imported_at'])<=app.clock.now(),
            'COVERAGE_INCOMPLETE','Source/binding coverage must include the observed fact')
    require(meta.get('source_timezone')=='Asia/Shanghai' and binding.get('source_timezone')=='Asia/Shanghai',
            'SOURCE_TIMEZONE_MISSING','Source and binding timezone must be confirmed')
    require(row['evidence_locator'] and row['completion_status'] in {'completed','incomplete','unknown'},
            'SOURCE_VALUE_UNSUPPORTED','Completion and locator must be explicit supported source values')
    scope=[family,contract['source_id'],binding['course_id'],binding['course_version'],lessons[0]]
    return {'scope_id':'learning-source:'+digest(scope),'batch_id':batch_id,'day':day,
            'source_id':contract['source_id'],'source_revision':contract['revision'],
            'supersedes_batch_id':contract.get('supersedes_batch_id'),'observed_at':required,
            'coverage_through':meta['coverage_through'],'lesson_ref':lessons[0],
            'course_id':binding['course_id'],'course_version':binding['course_version'],
            'completion_status':row['completion_status'],'locator':row['evidence_locator'],
            'source_hash':digest([row,meta,binding]),'binding_id':binding['id'],'binding_version':binding['version']}


def _references(event,evidence_id):
    refs=list(event.get('evidence_refs',[]))
    for entry in event.get('completion_history',[]):refs.extend(entry.get('evidence_refs',[]))
    return any(ref['kind']=='evidence' and ref['id']==evidence_id for ref in refs)


def sync_learning_evidence(app,admin_token,batch_id,day):
    """Not a model tool. Existing records/queue are the sole persistence mechanisms."""
    with app.store.transaction():
        family,_=app.auth(admin_token,{'admin'})
        source=source_snapshot(app,family,batch_id,day)
        old=next((r for r in app.store.list('learning_source',family) if r['id']==source['scope_id']),None)
        if old and old['source_revision']==source['source_revision']:
            require(old['source_hash']==source['source_hash'],'SOURCE_REVISION_CONFLICT',
                    'An existing source revision is immutable; use an explicit newer batch')
            return {'changed':False,'source':old,'evidence_ids':old.get('evidence_ids',[]),
                    'correction_ids':old.get('correction_ids',[])}
        if old:
            require(source['source_revision']==old['source_revision']+1
                    and source['supersedes_batch_id']==old['batch_id'] and source['batch_id']!=old['batch_id'],
                    'SOURCE_LINEAGE_CONFLICT','New source must explicitly supersede the current batch in order')
            require(timestamp(source['observed_at'])>=timestamp(old['observed_at'])
                    and timestamp(source['coverage_through'])>=timestamp(old['coverage_through']),
                    'SOURCE_LINEAGE_CONFLICT','Corrections cannot reduce source coverage')
        else:
            require(source['source_revision']==1 and source['supersedes_batch_id'] is None,
                    'SOURCE_LINEAGE_CONFLICT','A source lineage must start at revision one')
        evidence_ids=[];correction_ids=[]
        identity=[source['scope_id'],source['source_revision'],batch_id]
        if source['completion_status']=='completed':
            evidence_id='learning-evidence:'+digest(identity)
            app.store.put('evidence',evidence_id,family,{'kind':'lesson_completed','trusted':True,'valid':True,
                'source_origin':ORIGIN,'lesson_ref':source['lesson_ref'],'source_snapshot':source,
                'source_locator':source['locator'],'source_revision':source['source_revision'],
                'source_hash':source['source_hash'],'source_refs':[]})
            evidence_ids.append(evidence_id)
            from .jobs import JobQueue
            for event in app.store.list('event',family):
                if event['status'] not in {'done','cancelled'} and event['completion']['kind']=='goal' and source['lesson_ref'] in event['completion']['lesson_refs'] and app.evidence_matches_goal(event,{'source_origin':ORIGIN,'source_snapshot':source}):
                    JobQueue(app).enqueue(family,'review:'+digest([event['id'],evidence_id]),{
                        'kind':'review','event_id':event['id'],'event_version':event['version'],
                        'contact_allowed':False,'learning_evidence_id':evidence_id})
        prior_evidence=[e for e in app.store.list('evidence',family)
            if e.get('source_origin')==ORIGIN and e.get('source_snapshot',{}).get('scope_id')==source['scope_id']
            and e.get('source_revision',0)<source['source_revision']]
        for previous in prior_evidence:
            if previous.get('valid'):
                app.store.put('evidence',previous['id'],family,{**previous,'valid':False,
                    'superseded_by_batch':batch_id,'replacement_evidence_ids':evidence_ids,
                    'invalidated_source_revision':source['source_revision']},previous['version'])
        for old_id in [e['id'] for e in prior_evidence if e['kind']=='lesson_completed']:
            previous=app.store.get('evidence',old_id,family)
            # Only an original Event which really cited the old completion can be reopened.
            for event in app.store.list('event',family):
                if event['status']!='done' or not _references(event,old_id):continue
                correction_id='learning-correction:'+digest([identity,event['id'],old_id])
                correction=app.store.put('evidence',correction_id,family,{
                    'kind':'correction','trusted':True,'valid':True,'source_origin':ORIGIN,
                    'event_id':event['id'],'lesson_ref':source['lesson_ref'],'source_snapshot':source,
                    'source_locator':source['locator'],'source_revision':source['source_revision'],
                    'source_hash':source['source_hash'],'invalidated_evidence_id':old_id,
                    'replacement_evidence_ids':evidence_ids,'corrected_completion_status':source['completion_status'],
                    'source_refs':[]})
                correction_ids.append(correction_id)
                from .jobs import JobQueue
                JobQueue(app).enqueue(family,'review:'+digest([event['id'],correction_id]),{
                    'kind':'review','event_id':event['id'],'event_version':event['version'],
                    'contact_allowed':False,'correction_evidence_id':correction_id,
                    'correction_evidence_version':correction['version']})
        saved=app.store.put('learning_source',source['scope_id'],family,
            {**source,'evidence_ids':evidence_ids,'correction_ids':correction_ids},old['version'] if old else None)
        from .learning_review import queue_report_revisions
        report_reviews=queue_report_revisions(app,family)
        app.store.bump(family)
        return {'changed':True,'source':saved,'evidence_ids':evidence_ids,'correction_ids':correction_ids,
                'report_review_job_ids':report_reviews}


def validate_adapter_evidence(app,family,evidence):
    if evidence.get('source_origin')!=ORIGIN:return
    require(evidence.get('valid') is True,'EVIDENCE_SUPERSEDED','The original source fact was invalidated')
    source=evidence['source_snapshot']
    current=source_snapshot(app,family,source['batch_id'],source['day'])
    tracked=app.store.get('learning_source',source['scope_id'],family)
    require(current['source_hash']==source['source_hash']==tracked['source_hash'],
            'EVIDENCE_SUPERSEDED','Underlying source changed or was superseded')


def correction_review_allowed(app,family,job,event):
    if job['kind']!='review' or not job.get('correction_evidence_id') or event['status']!='done':return False
    try:
        evidence=app.store.get('evidence',job['correction_evidence_id'],family)
        validate_adapter_evidence(app,family,evidence)
        return (evidence.get('source_origin')==ORIGIN and evidence.get('kind')=='correction'
                and evidence.get('trusted') is True and evidence.get('valid') is True
                and evidence['version']==job.get('correction_evidence_version')
                and evidence['event_id']==event['id'] and _references(event,evidence['invalidated_evidence_id']))
    except BusinessError:return False


def validate_review_report(app,family,binding,cmd):
    if not binding.get('correction_evidence_id'):return
    job=app.store.get('job',binding['job_id'],family);event=app.event(family,job['event_id'])
    require(correction_review_allowed(app,family,job,event),'CORRECTION_REVIEW_STALE','Review no longer matches source')
    require(cmd.event_ids==[event['id']],'PROGRESS_SCOPE_VIOLATION','Correction review is bound to its original Event')
    require(any(r.kind=='evidence' and r.id==job['correction_evidence_id']
                and r.version==job['correction_evidence_version'] for r in cmd.evidence_refs),
            'EVIDENCE_MISSING','Review report must cite its verified correction')


def visible_learning_evidence(app,family,event_ids):
    events=[e for e in app.store.list('event',family) if e['id'] in event_ids]
    lessons={lesson for event in events for lesson in event['completion']['lesson_refs']}
    result=[]
    for evidence in app.store.list('evidence',family):
        if evidence.get('source_origin')!=ORIGIN:continue
        if not event_ids or evidence.get('event_id') in event_ids or any(evidence.get('lesson_ref') in event['completion']['lesson_refs'] and app.evidence_matches_goal(event,evidence) for event in events):
            try:
                validate_adapter_evidence(app,family,evidence);source_current=True
            except BusinessError:source_current=False
            result.append({**evidence,'record_kind':'evidence','reference_current':True,
                           'persisted_valid':evidence.get('valid'),'valid':bool(evidence.get('valid') and source_current),
                           'source_current':source_current,'usable_for_completion':source_current and evidence['kind']=='lesson_completed',
                           'reference':{'kind':'evidence','id':evidence['id'],'version':evidence['version']}})
    return result
