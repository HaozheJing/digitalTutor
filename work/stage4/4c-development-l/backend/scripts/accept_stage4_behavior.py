"""Prepare/execute independent 4B2 cases only after prior gates are accepted."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from digital_tutor.application import Application, Clock
from digital_tutor.domain import EventCreate
from digital_tutor.hermes_host import HermesHost
from digital_tutor.mock_channel import channel_for
from digital_tutor.runtime_assembly import approval
from digital_tutor.scheduler import Worker
from accept_stage4_passive import (RecordingHandler,drain,ledger,safe,save,sanitized_database,seed_sources,snapshot)
from stage4_behavior_fixtures import CASES,inputs_for,oracle_for
from stage4_behavior_checks import evaluate


def seed_extra_sources(app, families, inputs):
    """Independent synthetic source content, never actions/reports/expected answers."""
    with app.store.transaction() as db:
        if inputs.get('synthetic_entry'):
            row=db.execute('SELECT batch_id,metadata FROM imports').fetchone()
            meta=json.loads(row['metadata'])
            meta['course_binding']=json.dumps({'kind':'synthetic_mock_course_entry',
                'entry_url':'https://example.invalid/mock-course/fox-day1',
                'scope':'isolated_simulation_only','source_id':'synthetic-entry-config-v1'},ensure_ascii=False)
            db.execute('UPDATE imports SET metadata=? WHERE batch_id=?',(json.dumps(meta),row['batch_id']))
        if inputs.get('synthetic_work'):
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('synthetic-4b1-snapshot',app.store.family(families['a'])['uid'],1,901,
                 'synthetic_single_character_question','submitted',1,'2026-10-04 09:00',
                 '合成单次人物信息题','博吉斯养什么，喜欢吃什么？',None,
                 '博吉斯养鸡，他喜欢吃水煮鸡。',1,0,
                 json.dumps({'synthetic':True,'source_id':'synthetic-work-v1','scope':'one_answer_only'})))


def seed_historical_responsibilities(app,family):
    """Explicit yesterday-only source fixtures, never the current model's plan/report."""
    specs=[{'client_ref':'fox','goal_key':'historical-fox-day1','goal_text':'补读 L2《了不起的狐狸爸爸》第1天',
        'completion':{'kind':'goal','lesson_refs':['LESSON-L2-FOX-D01']},
        'due_at':'2026-10-05T20:00:00+08:00'},
        {'client_ref':'unrelated','goal_key':'historical-synthetic-other','goal_text':'保留另一个合成阅读课节的原补读责任',
         'completion':{'kind':'goal','lesson_refs':['SYNTHETIC-OTHER-LESSON']},'due_at':'2026-10-06T20:00:00+08:00'}]
    ids=[]
    with app.store.transaction() as db:
        for spec in specs:
            item=EventCreate.model_validate(spec)
            eid='historical-event-'+item.client_ref
            value=item.model_dump(exclude={'client_ref'})
            value.update(uid=app.store.family(family)['uid'],requirement_revision=1,
                remaining_work=item.completion.lesson_refs,proposed_change=None,deadline_history=[],
                completion_history=[],evidence_refs=[],service_hint=None,
                fixture_provenance='4B2 synthetic historical responsibility before current input')
            app.store.put('event',eid,family,value)
            db.execute('INSERT INTO goals VALUES(?,?,?)',(family,item.goal_key,eid));ids.append(eid)
        plan_id='historical-plan-20261003'
        app.store.put('plan',plan_id,family,{'business_date':'2026-10-03',
            'items':[{'event_id':eid,'rank':i+1} for i,eid in enumerate(ids)],
            'created_at':'2026-10-03T08:00:00+08:00','fixture_provenance':'historical_fixture_not_model_daily'})
        db.execute('INSERT INTO plans VALUES(?,?,?)',(family,'2026-10-03',plan_id))
    return ids


def run_case(project,run,case,args,approved):
    state=project/'state/backend-stage4'/run/case;out=project/'evaluations/backend-stage4'/run/case
    state.mkdir(parents=True,exist_ok=False);out.mkdir(parents=True,exist_ok=False)
    state.chmod(0o700);out.chmod(0o700)
    inputs,oracle=inputs_for(case),oracle_for(case)
    save(out/'inputs.json',inputs);save(out/'oracle.json',oracle)
    clock_path=state/'clock.json';save(clock_path,{'now':inputs['business_time']})
    runtime_mode=getattr(args,'runtime_mode','passive')
    app=Application(state/'business.sqlite',Clock(shared_path=clock_path),mode=runtime_mode,strict_runtime=True)
    try:
        families=seed_sources(app,inputs)
        seed_extra_sources(app,families,inputs)
        app.mock_send_outcome=inputs.get('mock_send_outcome','sent')
        if inputs.get('history'):
            seed_historical_responsibilities(app,families['a'])
        initial_events={r['id']:r for f in families.values() for r in app.store.list('event',f)}
        initial_plans={r['id']:r for f in families.values() for r in app.store.list('plan',f)}
        save(out/'historical-fixtures.json',{'events':initial_events,'plans':initial_plans})
        save(out/'source-rows.json',[dict(r) for r in app.store.connection.execute('SELECT * FROM learning_days')])
        save(out/'extra-sources.json',{table:[dict(r) for r in app.store.connection.execute('SELECT * FROM '+table)]
             for table in ('assignments','imports')})
        manifest={'case':case,'stage':'4B2','runtime_mode':runtime_mode,'prepared_only':args.prepare_only,'real_channel':False,
            'trigger_scope':'inbound only; no unrelated daily baseline scheduled',
            'approved_materials':approved,'semantic_status':'not_reviewed','state_root':str(state),
            'input_sha256':hashlib.sha256((out/'inputs.json').read_bytes()).hexdigest(),
            'oracle_sha256':hashlib.sha256((out/'oracle.json').read_bytes()).hexdigest(),
            'source_sha256':{str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest()
                for directory in ('backend/src','backend/scripts','agent/prompts')
                for p in (project/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts}}
        save(out/'manifest.json',manifest)
        if args.prepare_only:
            return {'case':case,'prepared_only':True,'model_calls':0,'gate_passed':False}
        hooks={'before:skills.read':{'fault':'required_guide_unavailable'}} if inputs.get('missing_guide') else {}
        host=HermesHost(project,state/'runs',args.budget_seconds,approved_materials=approved,test_hooks=hooks)
        handler=RecordingHandler(host,app,list(families.values()),out)
        planner_attempts=[]
        def guarded_handler(job,tools):
            if inputs.get('planner_unavailable') and job['kind']!='service':
                planner_attempts.append(job['kind'])
                raise RuntimeError('Isolated injected Planner unavailability')
            return handler(job,tools)
        guarded_handler.observe_sources=handler.observe_sources
        worker=Worker(app,guarded_handler,lease_seconds=9)
        duplicate_checks=[]
        for turn,batch in enumerate(inputs['rounds']):
            if inputs.get('send_outcomes'):app.mock_send_outcome=inputs['send_outcomes'][turn]
            round_payloads=[]
            for index,(name,body) in enumerate(batch):
                channel=app.open_session(families[name],'channel')
                payload={'external_id':f'{case}-{turn}-{index}','body':body,'occurred_at':app.clock.iso()}
                round_payloads.append((name,payload))
                try:
                    first=app.ingest_message(channel,payload)
                    if inputs.get('duplicate_first') and turn==index==0:
                        count=len(app.store.list('message',families[name]))
                        replay=app.ingest_message(channel,payload)
                        duplicate_checks.append(replay['outcome']=='duplicate' and replay['data']['id']==first['data']['id']
                            and count==len(app.store.list('message',families[name])))
                    if inputs.get('output_mode'):
                        with app.store.transaction():
                            job=app.store.get('job','message:'+first['data']['id'],families[name])
                            job['output_mode']=inputs['output_mode']
                            app.store.put('job',job['id'],families[name],job,job['version'])
                finally:
                    app.revoke_session(channel)
            turn_out=out/('round-%02d'%turn);turn_out.mkdir()
            drain(worker,app,list(families.values()),turn_out,args.max_worker_steps,wait_cap=args.retry_wait_cap)
            if inputs.get('duplicate_first'):
                before=(len(handler.calls),channel_for(app).count(),
                    {f:app.store.family(f)['version'] for f in families.values()})
                for name,payload in round_payloads:
                    token=app.open_session(families[name],'channel')
                    try:duplicate_checks.append(app.ingest_message(token,payload)['outcome']=='duplicate')
                    finally:app.revoke_session(token)
                idle=worker.run_once()
                duplicate_checks.append(not idle['ran'] and before==(len(handler.calls),channel_for(app).count(),
                    {f:app.store.family(f)['version'] for f in families.values()}))
        checks,traces,unresolved,reports=evaluate(app,list(families.values()),handler,case,oracle,
                                                initial_events,initial_plans,duplicate_checks)
        checks['injected_planner_not_called']=not planner_attempts
        save(out/'faults.json',{'planner_unavailable':bool(inputs.get('planner_unavailable')),
            'planner_attempts':planner_attempts,'tool_faults':hooks,'send_outcomes':inputs.get('send_outcomes',[])})
        calls=len(handler.calls);count=channel_for(app).count()
        versions={f:app.store.family(f)['version'] for f in families.values()}
        checks['twenty_idle_scans']=all(not worker.run_once()['ran'] for _ in range(20))
        checks['idle_zero_new_model_or_send']=len(handler.calls)==calls and channel_for(app).count()==count
        checks['idle_no_business_bump']=versions=={f:app.store.family(f)['version'] for f in families.values()}
        save(out/'tool-trace.json',traces)
        save(out/'sources-read.json',[t for t in traces if t['tool'].startswith(('knowledge.','skills.','learning.'))])
        save(out/'records.json',snapshot(app,list(families.values())))
        save(out/'channel-ledger.json',ledger(app));sanitized_database(app,out/'database.redacted.sqlite')
        save(out/'continuation-for-4d.json',{'source_database':str(state/'business.sqlite'),
            'channel_database':str(Path(app.store.path).with_suffix('.channel.sqlite')),
            'instruction':'Preserve this evidence. Continue only from a fresh isolated copy, never consume reports here.',
            'families':families,'unconsumed_report_ids':[r['id'] for r in reports if r['resolution'] is None],
            'original_event_ids':list(initial_events),'business_clock':app.clock.iso(),'source_manifest':str(out/'manifest.json')})
        result={'case':case,'program_checks':checks,'program_passed':all(checks.values()),
            'semantic_status':'not_reviewed','gate_passed':False,'model_runs':len(handler.calls),
            'api_calls':sum(c['native'].get('api_calls') or 0 for c in handler.calls),
            'unanswered_message_ids':unresolved,'unconsumed_reports':len(reports),
            'message_read_modes':sorted({t.get('arguments',{}).get('mode','') for t in traces
                                         if t['tool']=='wechat.read_messages' and t['ok']})}
        save(out/'result.json',result);return result
    finally:
        app.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True);parser.add_argument('--case',choices=['all',*CASES],default='all')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--runtime-mode',choices=['passive','full'],default='passive',
        help='Full permits actual admission jobs; ordinary inquiry tests must still create none.')
    parser.add_argument('--budget-seconds',type=int,default=300)
    parser.add_argument('--max-worker-steps',type=int,default=10)
    parser.add_argument('--retry-wait-cap',type=int,default=150)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',args.run):parser.error('Use a new simple run identifier')
    if not 1<=args.budget_seconds<=900 or not 1<=args.max_worker_steps<=30 or not 0<=args.retry_wait_cap<=600:
        parser.error('Invalid bounded execution budget')
    project=Path(__file__).resolve().parents[2];selected=list(CASES) if args.case=='all' else [args.case]
    for name in selected:
        if any((project/p/args.run/name).exists() for p in ('state/backend-stage4','evaluations/backend-stage4')):
            parser.error('Existing state/evidence is immutable; choose a new run')
    approved=approval(project);results=[]
    for name in selected:
        result=run_case(project,args.run,name,args,approved);results.append(result)
        print(json.dumps(safe(result),ensure_ascii=False),flush=True)
    modes=sorted({m for r in results for m in r.get('message_read_modes',[])})
    save(project/'evaluations/backend-stage4'/args.run/('summary-'+args.case+'.json'),
         {'stage':'4B2','cases':results,'message_read_modes':modes,
          'all_latest_since_observed':{'all','latest','since'}<=set(modes),
          'semantic_status':'not_reviewed','gate_passed':False})
    return 0 if args.prepare_only or all(r['program_passed'] for r in results) else 1


if __name__=='__main__':raise SystemExit(main())
