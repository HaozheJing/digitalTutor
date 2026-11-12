"""Share one proven family-input review across old service waits, without settling debt."""
from .storage import digest
from .service_context import message_source_digest, _read_record

def fact_signature(kind, record):
    if kind!='event':return (record['id'],record['version'])
    # A review timestamp/version is not new family input or changed work.
    value={k:v for k,v in record.items() if k not in {'version','next_execute_at','action_check'}}
    check=record.get('action_check') or {}
    value['action_check']={k:check.get(k) for k in ('fact','completed','remaining','unknown','needs_judgment',
        'expression_received','configuration_gaps','disposition','invitation_closed','cycle_result')}
    value['action_check']['contact']={k:(check.get('contact') or {}).get(k) for k in ('decision','reason')}
    return (record['id'],digest(value))


def dependency_observation(app,family,job):
    """An idle question observes its sources and linked facts, not family churn."""
    mids=set(job.get('coalesced_message_ids',[]))|{job.get('message_id')}
    mids.discard(None)
    def mentions(value,ids):
        if isinstance(value,str):return value in ids
        if isinstance(value,dict):return any(mentions(v,ids) for v in value.values())
        if isinstance(value,list):return any(mentions(v,ids) for v in value)
        return False
    inputs=[(m['id'],message_source_digest(m),m.get('interpreted'),m.get('intents'))
            for m in app.store.list('message',family) if m['id'] in mids]
    linked=set(mids)
    facts={}
    for kind in ('pending','event','report','evidence','operation','human_request'):
        rows=[r for r in app.store.list(kind,family) if mentions(r,linked)]
        linked.update(r['id'] for r in rows)
        facts[kind]=[fact_signature(kind,r) for r in rows]
    facts['control']=[(r['id'],r['version']) for r in app.store.list('control',family)]
    return digest({'inputs':inputs,'facts':facts})


def observation(app, family):
    inputs=[(m['id'],message_source_digest(m),m.get('interpreted'),
             [{k:i.get(k) for k in ('intent_id','text','requires_reply','service_kind','service_authorization')}
              for i in m.get('intents',[])]) for m in app.store.list('message',family)
            if m['direction']=='inbound']
    # Derived interpretation/reply versions do not create new input. Actual
    # delivery and changed authoritative records still permit one fresh review.
    facts={kind:[fact_signature(kind,r) for r in app.store.list(kind,family)]
           for kind in ('control','pending','event','report','memory','evidence','operation','human_request')}
    return digest({'inputs':inputs,'facts':facts})


def read_coverage(app, family, binding):
    observed=(_read_record(app,family,binding.get('run_id')) or {}).get('messages',{})
    return [mid for mid in binding.get('reply_scope',{})
            if observed.get(mid,{}).get('source_digest')==message_source_digest(app.store.get('message',mid,family))]


def save_review(app, family, job):
    if not (app.strict_runtime and job['kind']=='service' and job.get('run_id')):return
    rows=[r for r in app.store.list('run_outcome',family) if r['id']==job['run_id']]
    if not rows:return
    outcome=rows[0]
    if outcome.get('outcome') not in {'waiting','needs_input','action_taken','no_contact'}:return
    if outcome.get('observed_service_inputs')!=observation(app,family):return
    record_id='service-review:'+family
    old=next((r for r in app.store.list('runtime_service_review',family) if r['id']==record_id),None)
    covered=set(outcome.get('observed_reply_inputs',[]))
    if old and old['input_fingerprint']==outcome['observed_service_inputs']:
        covered.update(old['message_ids'])
    app.store.put('runtime_service_review',record_id,family,{
        'input_fingerprint':outcome['observed_service_inputs'],
        'message_ids':sorted(covered),
        'outcome_id':outcome['id'], 'run_id':job['run_id'],
        'review_is_reply_or_settlement':False},old['version'] if old else None)


def unchanged_review(app, family, job):
    if not (app.strict_runtime and job['kind']=='service' and job.get('attempt')):return False
    # Only a successful business wait can borrow review coverage. Failures,
    # missing outcomes, explicit timers and recovery retain their own rules.
    outcomes=[r for r in app.store.list('run_outcome',family) if r['id']==job.get('run_id')]
    if not outcomes:return False
    from .workflow import enabled
    if enabled(app) and job.get('wait_dependencies'):
        if outcomes[0]['outcome'] not in {'waiting','needs_input','no_contact'}:return False
        return job['wait_dependencies']==dependency_observation(app,family,job)
    if outcomes[0]['outcome'] not in {'waiting','needs_input'}:return False
    from .runtime_config import reply_scope
    mid=job.get('message_id')
    if not mid:return False
    message=app.store.get('message',mid,family)
    scope=reply_scope(app,family).get(mid,[])
    pending=[i for i in message.get('intents',[]) if i['requires_reply'] and not i['reply_operation_refs']
             and ('*' in scope or i['intent_id'] in scope)]
    # Reading is not answering. Never suppress an unprocessed/new intent merely
    # because another intent in the same message already received clarification.
    if not message.get('interpreted') or not pending or any(not i.get('partial_reply_operation_refs') for i in pending):
        return False
    reviews=app.store.list('runtime_service_review',family)
    return any(job.get('message_id') in r['message_ids']
               and r['input_fingerprint']==observation(app,family) for r in reviews)
