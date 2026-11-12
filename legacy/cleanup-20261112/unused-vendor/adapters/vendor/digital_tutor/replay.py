"""Replay external arrivals through the existing Scheduler/Worker and mock channel."""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .application import Application, Clock
from .domain import BusinessError, require, timestamp, Inbound
from .jobs import JobQueue
from .outbox import drain
from .replay_runtime import (configure, configuration, arrival_batch, bootstrap_history,
                             next_business_time, wall_blockers, write_clock, set_dispatch_paused)
from .scheduler import Scheduler, Worker


def append(path, record):
    with Path(path).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str)+'\n')
        handle.flush()


def atomic_json(path, value):
    path=Path(path)
    temporary=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    os.replace(temporary,path)


def ok(value):
    if not value.get('ok'):
        error=value.get('error',{})
        raise BusinessError(error.get('code','REPLAY_TOOL_FAILED'),error.get('message','Fixture tool failed'))
    return value['result']


class FixtureHandler:
    """Only for synthetic wiring checks; never scores or answers real chat."""
    def __init__(self, delay=0):
        self.delay=delay

    def __call__(self, job, tools):
        if self.delay:
            time.sleep(self.delay)
        app=tools.app
        role='planner_daily' if job['kind']=='daily' else 'service'
        family,binding=app.auth(tools.token,{role})
        if job['kind']=='daily':
            require(job.get('planning_reason','baseline')=='baseline','FIXTURE_SCOPE','No synthetic admission fixture')
            state=ok(tools.call('state.read',{}))
            ok(tools.call('planner.commit_daily',{'request_id':'fixture-daily:'+job['id'],
                'business_date':app.clock.now().date().isoformat(),
                'expected_family_version':state['family_version'], 'new_events':[],
                'event_updates':[], 'plan_refs':[], 'candidate_decisions':[]}))
            return {'reason':'synthetic_daily_committed'}
        require(job['kind']=='service','FIXTURE_SCOPE','Synthetic fixture supports daily and service only')
        first=ok(tools.call('wechat.read_message_stream',{'after_seq':0,'limit':1,'direction':'inbound'}))
        cursor=first['next_cursor']
        while first['has_more']:
            first=ok(tools.call('wechat.read_message_stream',{'after_seq':cursor,'limit':1,'direction':'inbound'}))
            cursor=first['next_cursor']
        messages=ok(tools.call('wechat.read_all_messages',{'direction':'inbound'}))['messages']
        refs=[]
        for message in messages:
            if message['id'] not in binding['reply_scope']:
                continue
            if not message['interpreted']:
                ok(tools.call('message.interpret',{'request_id':'fixture-interpret:'+message['id'],
                    'message_id':message['id'],'expected_version':message['version'],
                    'intents':[{'intent_id':'reply','text':message['body'],'requires_reply':True}]}))
            current=app.store.get('message',message['id'],family)
            refs += [{'message_id':current['id'],'intent_id':i['intent_id']} for i in current['intents']
                     if i['requires_reply'] and not i['reply_operation_refs']]
        require(refs,'FIXTURE_SCOPE','No synthetic reply obligation')
        state=ok(tools.call('state.read',{}))
        operation=ok(tools.call('wechat.send_message',{'request_id':'fixture-send:'+binding['run_id'],
            'expected_family_version':state['family_version'],'body':'合成接线回复：已收到本次消息。',
            'action_kind':'reply','reply_to':refs}))['data']
        outcome=ok(tools.call('service.record_outcome',{'request_id':'fixture-outcome:'+binding['run_id'],
            'outcome':'action_taken','refs':[{'kind':'operation','id':operation['id'],'version':operation['version']}],
            'reason':'Synthetic wiring response was actually sent to mock'}))
        return {'outcome_id':outcome['id'],'reason':'synthetic_service_completed'}


def load_pack(path):
    raw=Path(path).read_bytes()
    pack=json.loads(raw)
    require(pack.get('schema')=='teacher-agent-replay/1','REPLAY_INPUT','Unknown replay schema')
    require(isinstance(pack.get('is_synthetic'),bool),'REPLAY_INPUT','Input provenance is required')
    start,end=timestamp(pack['start']),timestamp(pack['end'])
    require(end>start,'REPLAY_INPUT','Invalid replay window')
    events=pack.get('events',[])
    seen=set()
    for event in events:
        require(event['source_id'] not in seen,'REPLAY_INPUT','Duplicate source ID in input pack')
        seen.add(event['source_id'])
        require(event.get('channel')=='wechat' and event['speaker_role']!='teacher',
                'REPLAY_TEACHER_INPUT','Reference or phone message cannot enter runtime arrivals')
        at=timestamp(event['arrival_at'])
        require(start<=at<=end and timestamp(event['occurred_at'])<=at,'REPLAY_INPUT','Invalid event arrival time')
        require(event['source_metadata']['source_id']==event['source_id']
                and event['source_metadata']['speaker_role']==event['speaker_role'],
                'REPLAY_INPUT','Source identity or speaker role differs from metadata')
        Inbound.model_validate({'external_id':event['source_id'],'body':event['body'],
            'occurred_at':event['occurred_at'],'source_metadata':event['source_metadata']})
    events.sort(key=lambda e:(timestamp(e['arrival_at']),e['source_metadata']['source_row'],e['source_id']))
    pack['events']=events
    return pack,hashlib.sha256(raw).hexdigest()


def make_handler(project, case, pack, native, fixture_delay=0):
    if not native:
        require(pack['is_synthetic'],'FIXTURE_REAL_DATA_FORBIDDEN','A fixture cannot impersonate the agent on real chat')
        return FixtureHandler(fixture_delay)
    if not pack['is_synthetic']:
        from .provider_routes import resolve_connection
        from urllib.parse import urlsplit
        connection=resolve_connection(project, 'service')
        authorization=json.loads((Path(case)/'authorization.json').read_text(encoding='utf-8'))
        require(authorization.get('input_sha256')==pack['_input_sha256']
                and authorization.get('model')==connection['model']
                and authorization.get('destination')==urlsplit(connection['base_url']).hostname
                and authorization.get('approved_by_user') is True,
                'REAL_INPUT_NOT_APPROVED','Real input needs the reviewed packet authorization')
    from .hermes_host import HermesHost
    from .runtime_assembly import replay_approval, approval
    materials=replay_approval(project) if pack.get('use_replay_materials') else approval(project)
    return HermesHost(project,Path(case)/'runs',120,approved_materials=materials)


def export_results(app, family, case):
    from .mock_channel import channel_for
    case=Path(case)
    operations=app.store.list('operation',family)
    messages={m['id']:m for m in app.store.list('message',family)}
    actual=[]
    for op in operations:
        if op['stage'] not in {'sent','delivered'}:
            continue
        actual.append({'operation_id':op['id'],'occurred_at':op['occurred_at'],
            'body':op['body'],'stage':op['stage'],'event_id':op.get('event_id'),
            'reply_source_ids':[messages[r['message_id']]['external_id'] for r in op.get('reply_to',[])],
            'run_id':op.get('run_id'),'simulated':True})
    atomic_json(case/'actual-outbound.json',actual)
    atomic_json(case/'channel-audit.json',channel_for(app).audit_snapshot())
    atomic_json(case/'final-state.json',{'now':app.clock.iso(),'events':app.store.list('event',family),
        'plans':app.store.list('plan',family),'jobs':app.store.list('job',family),
        'pending':app.store.list('pending',family),'reports':app.store.list('report',family),
        'message_count':len(messages),'operations':len(operations)})


def run_case(project, input_path, case, *, native=False, mode=None, resume=False,
             max_runs=40, max_wall_seconds=1800, fixture_delay=0, authorization=None):
    pack,input_sha=load_pack(input_path)
    requested_mode=mode
    mode=mode or ('stream' if native else 'discrete')
    pack['_input_sha256']=input_sha
    case=Path(case).resolve()
    if not resume:
        require(not case.exists(),'REPLAY_OUTPUT_EXISTS','Create a new case or explicitly resume it')
        case.mkdir(parents=True,mode=0o700)
        (case/'input.json').write_bytes(Path(input_path).read_bytes())
        if authorization:
            (case/'authorization.json').write_bytes(Path(authorization).read_bytes())
        write_clock(case/'clock.json',pack['start'])
    else:
        require((case/'input.json').read_bytes()==Path(input_path).read_bytes(),
                'REPLAY_INPUT_CHANGED','Resume cannot change the frozen input')
    app=Application(case/'business.sqlite',Clock(shared_path=case/'clock.json'),strict_runtime=True,mode='full')
    family=pack['family_alias']
    app.create_family(family,'replay-uid:'+family,'mock-replay:'+family)
    if not resume:
        configure(app,family,synthetic=pack['is_synthetic'],input_sha256=input_sha,mode=mode,
                  evaluation_protocol=pack.get('evaluation_protocol','continuous_open_loop'))
        bootstrap_history(app,family,pack.get('history',[]))
    else:
        stored=configuration(app,family)
        require(stored['input_sha256']==input_sha,'REPLAY_INPUT_CHANGED','Stored replay source differs')
        if requested_mode is None:
            mode=stored['mode']
        elif mode!=stored['mode']:
            app.close()
            require(False,'REPLAY_MODE_CHANGED','Resume must preserve the original clock/loading mode; use a new case for another mode')
    started=time.monotonic()
    atomic_json(case/'manifest.json',{'input_sha256':input_sha,'schema':pack['schema'],
        'is_synthetic':pack['is_synthetic'],'source_cohort':pack.get('source_cohort'),
        'start':pack['start'],'end':pack['end'],'mode':mode,'native_called':native,
        'handler':'HermesHost' if native else 'deterministic_ToolRegistry_fixture',
        'strict_runtime':True,'max_runs':max_runs,'max_wall_seconds':max_wall_seconds,
        'startup':'cold_start_history_not_imported_as_current_obligation'})
    try:
        if mode=='stream':
            result=run_stream(project,app,family,case,pack,native,max_runs,max_wall_seconds,fixture_delay)
        else:
            handler=make_handler(project,case,pack,native,fixture_delay)
            worker=Worker(app,handler)
            result=run_discrete(app,family,case,pack,worker,max_runs,max_wall_seconds)
        result['wall_seconds']=time.monotonic()-started
        result['replay_validity']={'mode':mode,
            'evaluation_protocol':pack.get('evaluation_protocol','continuous_open_loop'),
            'clock_continues_during_generation':mode=='stream',
            'response_latency_comparable':mode=='stream' and pack.get('evaluation_protocol')!='matched_decision_snapshot',
            'historical_replies_follow_agent_questions':False,
            'missing_teacher_task_context_must_be_scored_separately':True}
        export_results(app,family,case)
        atomic_json(case/'summary.json',result)
        return result
    finally:
        app.close()


def inject_due(app,family,case,events):
    now=app.clock.now()
    arrived={m['external_id'] for m in app.store.list('message',family)}
    due=[e for e in events if timestamp(e['arrival_at'])<=now and e['source_id'] not in arrived]
    if not due:
        return 0
    token=app.open_session(family,'channel')
    try:
        with arrival_batch(app,family):
            for event in due:
                value=app.ingest_message(token,{'external_id':event['source_id'],'body':event['body'],
                    'occurred_at':event['occurred_at'],'source_metadata':event['source_metadata']})
                append(case/'arrivals.jsonl',{'source_id':event['source_id'],'now':app.clock.iso(),
                    'arrival_at':event['arrival_at'],'message_id':value['data']['id'],
                    'wall_time':Clock().iso(),'outcome':value['outcome']})
    finally:
        app.revoke_session(token)
    return len(due)


def snapshot(app,family,case):
    append(case/'day-state.jsonl',{'now':app.clock.iso(),'plans':len(app.store.list('plan',family)),
        'events':[{k:e.get(k) for k in ('id','status','waiting','next_action')} for e in app.store.list('event',family)],
        'jobs':[{k:j.get(k) for k in ('id','kind','status','reason','attempt')} for j in app.store.list('job',family)]})


def run_discrete(app,family,case,pack,worker,max_runs,max_wall_seconds):
    started=time.monotonic(); runs=0; advances=0
    while True:
        inject_due(app,family,case,pack['events'])
        drain(app); Scheduler(app).tick()
        if runs>=max_runs or time.monotonic()-started>=max_wall_seconds:
            return {'completed':False,'reason':'budget_limited','worker_runs':runs,'advances':advances}
        value=worker.run_once()
        append(case/'worker-events.jsonl',{'now':app.clock.iso(),'result':value})
        if value['ran']:
            runs+=1
            continue
        blockers=wall_blockers(app,family)
        if blockers['leased']:
            time.sleep(0.1);continue
        if blockers['next_wall_wakeup'] and app.service_start<=app.clock.now().hour<app.service_end:
            wait=(timestamp(blockers['next_wall_wakeup'])-Clock().now()).total_seconds()
            time.sleep(min(0.25,max(0.01,wait)));continue
        snapshot(app,family,case)
        if app.clock.now()>=timestamp(pack['end']):
            return {'completed':True,'reason':'window_elapsed','worker_runs':runs,'advances':advances}
        next_time=next_business_time(app,family,pack['end'],[e['arrival_at'] for e in pack['events']])
        require(next_time>app.clock.now(),'REPLAY_NO_PROGRESS','No strictly future clock candidate')
        old=app.clock.iso();write_clock(case/'clock.json',next_time.isoformat());advances+=1
        append(case/'clock-events.jsonl',{'from':old,'to':next_time.isoformat(),'mode':'discrete'})


def run_stream(project,app,family,case,pack,native,max_runs,max_wall_seconds,fixture_delay):
    """Separate loader and Worker process; active runs use 1x virtual time."""
    # This runtime's isolated Python ignores PYTHONPATH. Bind the frozen source
    # explicitly in both parent and child, without modifying installed packages.
    bootstrap='import sys;sys.path.insert(0,sys.argv.pop(1));from digital_tutor.replay_worker import main;main()'
    command=[sys.executable,'-c',bootstrap,str(Path(project)/'backend/src'),'--project',str(project),'--case',str(case),
             '--max-runs',str(max_runs),'--fixture-delay',str(fixture_delay)]
    if native:command.append('--native')
    log=(case/'worker-process.log').open('x',encoding='utf-8')
    process=subprocess.Popen(command,cwd=case,env=dict(os.environ),stdout=log,stderr=log)
    started=time.monotonic();last=started;advances=0;result=None
    try:
        while time.monotonic()-started<max_wall_seconds:
            inject_due(app,family,case,pack['events'])
            current=time.monotonic();elapsed=current-last;last=current
            status_path=case/'worker-status.json'
            status=json.loads(status_path.read_text()) if status_path.exists() else {}
            if status.get('budget_limited'):
                result={'completed':False,'reason':'budget_limited'};break
            if process.poll() is not None:
                result={'completed':False,'reason':'worker_exited','exit_code':process.returncode};break
            now=app.clock.now();end=timestamp(pack['end'])
            idle=status.get('idle') and status.get('now')==now.isoformat()
            blockers=wall_blockers(app,family)
            if now>=end:
                if idle and not blockers['leased']:
                    result={'completed':True,'reason':'window_elapsed'};break
            elif idle and not blockers['leased'] and not blockers['next_wall_wakeup']:
                target=next_business_time(app,family,pack['end'],[e['arrival_at'] for e in pack['events']])
                with arrival_batch(app,family):
                    # Pausing and claiming share a transaction. A claim that won
                    # just before pausing must keep the clock at real speed.
                    if wall_blockers(app,family)['leased']:
                        continue
                    write_clock(case/'clock.json',target.isoformat())
                    # inject_due owns a batch independently on the next iteration.
                    # Keep dispatch paused until due arrivals have been committed.
                    token=app.open_session(family,'channel')
                    arrived={m['external_id'] for m in app.store.list('message',family)}
                    try:
                        for event in pack['events']:
                            if timestamp(event['arrival_at'])<=target and event['source_id'] not in arrived:
                                v=app.ingest_message(token,{'external_id':event['source_id'],'body':event['body'],
                                    'occurred_at':event['occurred_at'],'source_metadata':event['source_metadata']})
                                append(case/'arrivals.jsonl',{'source_id':event['source_id'],'now':app.clock.iso(),
                                    'arrival_at':event['arrival_at'],'message_id':v['data']['id'],'wall_time':Clock().iso()})
                    finally:app.revoke_session(token)
                advances+=1;append(case/'clock-events.jsonl',{'from':now.isoformat(),'to':target.isoformat(),'mode':'idle_jump'})
            elif now<end:
                # Fractional seconds preserve real short-gap arrivals during native generation.
                target=min(end,now+timedelta(seconds=elapsed))
                write_clock(case/'clock.json',target.isoformat())
            time.sleep(0.05)
        result=result or {'completed':False,'reason':'wall_budget_limited'}
    finally:
        (case/'worker-stop').touch()
        try:process.wait(timeout=170)
        except subprocess.TimeoutExpired:
            # Request native cancellation, never guess whether an entered send occurred.
            for run in (case/'runs').glob('*') if (case/'runs').exists() else []:
                (run/'cancel').touch()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        log.close()
    status=json.loads((case/'worker-status.json').read_text()) if (case/'worker-status.json').exists() else {}
    return {**result,'worker_runs':status.get('runs',0),'advances':advances,'loader_worker_separate_processes':True}
