"""Narrow continuation of a terminally unexecuted delivery part, never unknown."""
import json
from .domain import BusinessError, require
from .storage import digest


def source_digest(app, family):
    from .source_rows import SOURCE_SCHEMA
    from .report_contract import message_source_digest
    db=app.store.connection; uid=app.store.family(family)['uid']
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    source={}
    batches=set()
    for table in ('learning_days','assignments',*SOURCE_SCHEMA):
        rows=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE uid=?',(uid,))] if table in tables else []
        source[table]=sorted(rows,key=digest)
        batches.update(r['batch_id'] for r in rows)
    source['imports']=[dict(r) for r in db.execute('SELECT * FROM imports ORDER BY batch_id') if r['batch_id'] in batches]
    source['course_bindings']=sorted(app.store.list('course_binding',family),key=lambda r:r['id'])
    source['configured_sources']=sorted(app.store.list('learning_source',family),key=lambda r:r['id'])
    source['candidate_sources']=sorted(app.store.list('candidate_source',family),key=lambda r:r['id'])
    source['messages']=sorted((m['id'],message_source_digest(m)) for m in app.store.list('message',family) if m.get('direction')=='inbound')
    # Include underlying evidence, but not operation/report/run accounting.
    source['evidence']=sorted(app.store.list('evidence',family),key=lambda r:r['id'])
    return digest(source)


def terminal_proof(app, operation):
    if operation.get('stage')!='failed' or not operation.get('not_executed_proof'):
        return None
    from .mock_channel import channel_for
    from .transport_proof import proof
    channel=channel_for(app)
    with channel.lock:
        value=proof(channel,operation)
    return value if value==operation.get('not_executed_proof') else None


def charged(app, operation):
    return operation['stage']!='cancelled' and not terminal_proof(app,operation)


def applied_report(app, family, operation):
    matches=[r for r in app.store.list('report',family)
             if r.get('resolution')=='applied' and operation.get('event_id') in r.get('event_ids',[])
             and any(ref.get('kind')=='operation' and ref.get('id')==operation['id']
                     and ref.get('version')==operation['version']
                     for ref in r.get('payload',{}).get('evidence_refs',[]))]
    verified=[]
    for report in matches:
        row=app.store.connection.execute('SELECT result,rowid FROM receipts WHERE family_id=? AND request_id=?',
                                        (family,report.get('commit_request_id'))).fetchone()
        links=app.store.connection.execute('SELECT event_id,resolved FROM report_links WHERE family_id=? AND report_key=?',
                                          (family,report['id'])).fetchall()
        if not row or not links or any(not link['resolved'] for link in links):continue
        receipt=json.loads(row[0]);data=receipt.get('data') or {}
        if (receipt.get('outcome')=='applied' and not receipt.get('error')
                and data.get('report_id')==report['report_id'] and data.get('revision')==report['revision']
                and {e.get('id') for e in data.get('events',[])}==set(report['event_ids'])
                and {link['event_id'] for link in links}==set(report['event_ids'])):
            verified.append((row[1],report))
    # The first actual consumption is stable when later accounting reports are
    # added. A repeated scan/report must not mint another continuation identity.
    return min(verified,key=lambda pair:pair[0])[1] if verified else None


def nonexecution_chain(app, family, operation):
    """A never-dispatched cancelled successor may continue its proven root only."""
    current=operation;seen=set()
    while current['id'] not in seen:
        seen.add(current['id'])
        if terminal_proof(app,current):return current
        if current.get('stage')!='cancelled' or current.get('dispatch_count')!=0 or not current.get('retry_of'):return None
        previous=app.store.get('operation',current['retry_of'],family)
        if any(current.get(k)!=previous.get(k) for k in ('purpose_key','content_digest','part_id','event_id','recipient','requirement_revision')):return None
        if current.get('attempt_number')!=previous.get('attempt_number',1)+1:return None
        current=previous
    return None


def validate_parent(app, family, event, parent, *, require_head=True):
    from .scheduling import require_step_authority
    require(parent.get('action_kind')=='deliver_feedback' and parent.get('event_id')==event['id']
            and event['completion']['kind']=='delivery' and parent.get('part_id') in event['completion']['required_part_ids'],
            'RETRY_SCOPE','Only an original delivery part can be retried')
    proven=nonexecution_chain(app,family,parent)
    require(proven is not None,'RETRY_PROOF_REQUIRED','Unknown, generic failed, and absence are not retry permission')
    require(parent.get('attempt_number',1)<3,'RETRY_LIMIT','Automatic delivery attempts are bounded')
    require(parent.get('requirement_revision')==event['requirement_revision']
            and parent.get('recipient')==app.store.family(family)['recipient'],
            'RETRY_CONTRACT_CHANGED','Original delivery contract changed')
    require_step_authority(app,family,event)
    step=event.get('next_action') or {};targets=set(step.get('target_refs',[]))
    require(step.get('action_kind')=='deliver_feedback' and (parent['part_id'] in targets
            or bool(targets.intersection(event['completion']['lesson_refs']))),
            'RETRY_CONTRACT_CHANGED','Current committed delivery step must still cover the missing part')
    grant=event.get('accepted_commitment') or {}
    require(parent.get('send_capability',{}).get('schedule_revision')==grant.get('schedule_revision'),
            'RETRY_CONTRACT_CHANGED','Original schedule changed')
    require(parent.get('body_source_digest')==source_digest(app,family),
            'RETRY_SOURCE_CHANGED','Original text source changed; retain the obligation without reusing stale text')
    require(not any(o.get('event_id')==event['id'] and o.get('part_id')==parent['part_id']
                    and o.get('requirement_revision')==event['requirement_revision']
                    and o['stage'] in {'sent','delivered'} for o in app.store.list('operation',family)),
            'ALREADY_DELIVERED','A successful part is never retried')
    if require_head:
        head=app.store.connection.execute('SELECT operation_id FROM operation_keys WHERE family_id=? AND purpose_key=?',
                                         (family,parent['purpose_key'])).fetchone()
        require(head and head[0]==parent['id'],'RETRY_HEAD_CHANGED','Original attempt already has a successor')
    report=applied_report(app,family,parent)
    require(report is not None,'RETRY_REPORT_PENDING','Original failed fact must be reported and consumed before normal continuation')
    return report


def candidate(app, family, event):
    from .scheduling import require_step_authority, step_key
    try:
        require_step_authority(app,family,event)
        require(not event.get('update_pending'),'RETRY_REPORT_PENDING','Original report pending')
        operations=app.store.list('operation',family)
        require(not any(o.get('event_id')==event['id'] and o['stage'] in {'queued','dispatching','unknown','accepted'} for o in operations),
                'OPERATION_UNCERTAIN','Account for original uncertain attempts first')
        for parent in operations:
            if parent.get('event_id')!=event['id'] or parent.get('stage') not in {'failed','cancelled'}:continue
            try: report=validate_parent(app,family,event,parent)
            except BusinessError:continue
            identity={'event_id':event['id'],'step_key':step_key(event),'operation_id':parent['id'],
                      'requirement_revision':event['requirement_revision'],
                      'report_key':report['id'],'report_revision':report['revision'],
                      'proof_digest':digest(nonexecution_chain(app,family,parent)['not_executed_proof'])}
            return {**identity,'key':'delivery-retry:'+digest(identity)}
    except BusinessError:
        pass
    return None


def continuation_allowed(app, family, job):
    current=candidate(app,family,app.event(family,job['event_id']))
    return bool(current and current==job.get('delivery_retry'))


def check_attempt(app, family, cmd, capability, operation=None):
    retry_id=cmd.retry_operation_id
    cap=capability or {}
    job=None
    if cap.get('job_id'):
        job=app.store.get('job',cap['job_id'],family)
    if job and job.get('delivery_retry'):
        require(retry_id==job['delivery_retry']['operation_id'],'RETRY_SCOPE','Continuation may send only its original missing part')
    if not retry_id:return None
    require(app.strict_runtime and cap.get('job_kind')=='step' and not cap.get('no_contact') and not cap.get('recovery_only'),
            'RETRY_SCOPE','Retry requires a current normal step capability')
    parent=app.store.get('operation',retry_id,family)
    event=app.event(family,cmd.event_id)
    validate_parent(app,family,event,parent,require_head=operation is None)
    require(cmd.action_kind==parent['action_kind'] and cmd.part_id==parent['part_id']
            and cmd.body==parent['body'] and not cmd.reply_to and cmd.proposal_ref is None,
            'RETRY_PAYLOAD_CHANGED','Retry preserves exact recipient, part, and original text')
    require(parent.get('material_approval_digest')==digest(getattr(app,'material_approval',None)),
            'RETRY_SOURCE_CHANGED','Approved material identity changed')
    if operation:
        head=app.store.connection.execute('SELECT operation_id FROM operation_keys WHERE family_id=? AND purpose_key=?',
                                         (family,parent['purpose_key'])).fetchone()
        require(head and head[0]==operation['id'] and operation.get('retry_of')==parent['id'],
                'RETRY_HEAD_CHANGED','Dispatch must use the unique successor')
    return parent
