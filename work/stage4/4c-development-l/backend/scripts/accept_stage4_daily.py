"""Independent strict daily acceptance: synthetic source inputs, native Flash decisions."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import os
import subprocess
import sys
from threading import Thread
import time
from digital_tutor.application import Application,Clock
from digital_tutor.candidates import configure_source
from digital_tutor.course_binding import confirm_course_binding
from digital_tutor.hermes_host import HermesHost
from digital_tutor.runtime_assembly import approval
from digital_tutor.scheduler import Scheduler,Worker
from digital_tutor.mock_channel import channel_for
from digital_tutor.domain import EventCreate
from accept_stage4_passive import RecordingHandler,drain,save,ledger,sanitized_database,seed_sources,safe
from accept_stage4_behavior import seed_historical_responsibilities
from stage4_decision_confirmation_checks import decision_confirmation_evidence

KINDS=('job','message','operation','event','report','plan','run_outcome','control','pending','memory',
       'candidate','candidate_source','candidate_decision','course_binding','runtime_cursor',
       'runtime_message_read','runtime_material_wait')
CASES=('create_and_reuse','due_today','empty','coverage_gap','family_requests','report_gate','inbound_interleave')


def snapshot(app,family):
    return {kind:safe(app.store.list(kind,family)) for kind in KINDS}


def bind_course(app,family):
    token=app.open_session(family,'admin')
    try:
        return confirm_course_binding(app,token,{'request_id':'external-confirm-course','batch_id':'synthetic-4b1-snapshot',
            'expected_family_version':app.store.family(family)['version'],
            'source_note':'Confirmed synthetic family/course fixture; no real learner record',
            'course_id':'simulation-L2-fox','course_version':'simulation-v1',
            'lesson_map':[{'day':1,'lesson_ref':'LESSON-L2-FOX-D01'}],
            'coverage_through':'2026-10-04T07:30:00+08:00','source_timezone':'Asia/Shanghai'})
    finally:app.revoke_session(token)


def sources(app,family,makeup=True,delivery=True,due_at='2026-10-06T20:00:00+08:00'):
    common={'simulation':True,'confirmed':True,'enabled':True,'rule_version':'simulation-only-v1',
            'active_from':'2026-10-04T08:00:00+08:00','active_until':'2026-10-07T20:00:00+08:00',
            'due_at':due_at}
    token=app.open_session(family,'admin')
    try:
        if makeup:
            configure_source(app,token,{**common,'source_id':'simulation-makeup-day1','source_kind':'makeup_rule',
                'batch_id':'synthetic-4b1-snapshot','day':1,'required_coverage_at':'2026-10-04T07:30:00+08:00'})
        if delivery:
            configure_source(app,token,{**common,'source_id':'simulation-weekly-writing','source_kind':'periodic_text_delivery',
                'contract_id':'simulation-weekly-feedback','period_start':'2026-09-28T00:00:00+08:00',
                'period_end':'2026-10-04T07:30:00+08:00','required_stage':'sent',
                'parts':[{'part_id':'learning-feedback','media_type':'text','purpose':'据本周可用学情写一份简短文字反馈'}]})
    finally:app.revoke_session(token)


def inbound(app,family,text,external):
    token=app.open_session(family,'channel')
    try:return app.ingest_message(token,{'external_id':external,'body':text,'occurred_at':app.clock.iso()})
    finally:app.revoke_session(token)


def historical_catalog(app,family):
    """Yesterday's synthetic open obligations, never today's tested plan or new goals."""
    with app.store.transaction() as db:
        for name,waiting in (('waiting-without-review',True),('not-selected-yesterday',False)):
            item=EventCreate.model_validate({'client_ref':name,'goal_key':'historical-'+name,
                'goal_text':'历史合成责任 '+name,'completion':{'kind':'goal','lesson_refs':['SYNTHETIC-'+name]},
                'status':'waiting' if waiting else 'pending',
                'waiting':{'reason':'原资料缺失','resume_condition':'原资料补齐','review_at':None} if waiting else None})
            value=item.model_dump(exclude={'client_ref','candidate_id','delivery_parts'})
            value.update(uid=app.store.family(family)['uid'],requirement_revision=1,
                remaining_work=item.completion.lesson_refs,proposed_change=None,deadline_history=[],
                completion_history=[],evidence_refs=[],service_hint=None,fixture_provenance='historical_before_4C')
            app.store.put('event',name,family,value);db.execute('INSERT INTO goals VALUES(?,?,?)',(family,item.goal_key,name))
        app.store.put('plan','yesterday-plan',family,{'business_date':'2026-10-03','items':[],
                      'created_at':'2026-10-03T08:00:00+08:00','fixture_provenance':'historical_before_4C'})
        db.execute('INSERT INTO plans VALUES(?,?,?)',(family,'2026-10-03','yesterday-plan'))


def idle(app,worker,handler,family):
    before=(len(handler.calls),channel_for(app).count(),app.store.family(family)['version'])
    results=[]
    for _ in range(20):
        Scheduler(app).tick();results.append(worker.run_once())
    return {'scans':results,'all_idle':all(not r['ran'] for r in results),
            'unchanged':before==(len(handler.calls),channel_for(app).count(),app.store.family(family)['version'])}


def restart_probe(project,args):
    state=project/'state/backend-stage4'/args.run/args.case;out=project/'evaluations/backend-stage4'/args.run/args.case
    app=Application(state/'business.sqlite',Clock(shared_path=state/'clock.json'),mode='daily',strict_runtime=True)
    try:
        family=app.store.connection.execute('SELECT id FROM families').fetchone()[0]
        host=HermesHost(project,state/'runs',args.budget_seconds,approved_materials=approval(project))
        handler=RecordingHandler(host,app,[family],out)
        handler.calls=[json.loads(p.read_text(encoding='utf8')) for p in sorted(out.glob('model-*.json'))]
        result=idle(app,Worker(app,handler,lease_seconds=9),handler,family)
        result.update(process_id=os.getpid(),restart_kind='new_python_process')
        save(out/'restart-child.json',result)
        return int(not result['all_idle'] or not result['unchanged'])
    finally:app.close()


def run_case(project,args,case):
    state=project/'state/backend-stage4'/args.run/case;out=project/'evaluations/backend-stage4'/args.run/case
    state.mkdir(parents=True,exist_ok=False);out.mkdir(parents=True,exist_ok=False)
    clock=state/'clock.json';save(clock,{'now':'2026-10-04T07:59:00+08:00'})
    app=Application(state/'business.sqlite',Clock(shared_path=clock),mode='daily',strict_runtime=True)
    inputs={'families':['a'],'business_time':'2026-10-04T07:30:00+08:00',
            'learning_source':{'a':[('incomplete','not_submitted'),('completed','submitted'),('unknown','unknown')]},
            'scope':'all family and learning data synthetic; formal R materials unchanged'}
    save(out/'inputs.json',inputs)
    families=seed_sources(app,inputs);family=families['a']
    pin=approval(project)
    hooks={};gate=state/'inbound-gate'
    if case=='inbound_interleave':hooks={'before:planner.commit_daily':str(gate)}
    host=HermesHost(project,state/'runs',args.budget_seconds,approved_materials=pin,test_hooks=hooks)
    handler=RecordingHandler(host,app,[family],out);worker=Worker(app,handler,lease_seconds=9)
    checks={};phases=[]
    try:
        if case not in {'empty','coverage_gap'}:bind_course(app,family)
        if case in {'create_and_reuse','coverage_gap'}:sources(app,family,delivery=case=='create_and_reuse')
        if case=='due_today':sources(app,family,due_at='2026-10-04T16:00:00+08:00')
        if case=='create_and_reuse':historical_catalog(app,family)
        if case=='inbound_interleave':sources(app,family,makeup=False)
        save(out/'external-sources.json',snapshot(app,family))
        save(out/'source-rows.json',{table:[dict(r) for r in app.store.connection.execute('SELECT * FROM '+table)]
                                    for table in ('imports','learning_days')})
        save(out/'manifest.json',{'case':case,'stage':'4C','approval':pin,'model':'qwen3.8-flash',
            'source_sha256':{str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest()
                for directory in ('backend/src','backend/scripts','agent/prompts')
                for p in (project/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts},
            'semantic_status':'not_reviewed','gate_passed':False})
        Scheduler(app).tick();early=worker.run_once()
        checks['before_0800_zero_calls']=not early['ran'] and not handler.calls
        save(clock,{'now':'2026-10-04T08:00:00+08:00'})
        if case=='family_requests':
            # Natural requests only; service alone must decide how to register them.
            app.mode='passive'
            texts=['请安排明天下午四点前补完第1天。',
                   '请帮我明天补读第1天。',
                   '我还想要一份这周阅读情况的文字总结，10月5日下午四点前需要，先记下需求。']
            for i,body in enumerate(texts):
                inbound(app,family,body,'family-request-'+str(i));phase=out/f'service-{i}';phase.mkdir()
                drain(worker,app,[family],phase,8,wait_cap=150)
            app.mode='daily'
            save(out/'before-daily.json',snapshot(app,family))
        if case=='report_gate':
            app.mode='passive';seed_historical_responsibilities(app,family)
            inbound(app,family,'之前约的狐狸第1天补读，我们只读了一小段，还没有读完，请帮我记住这个进度。','original-progress')
            phase=out/'service';phase.mkdir();drain(worker,app,[family],phase,8,wait_cap=150)
            app.mode='daily'
            checks['report_created_by_service']=any(r['resolution'] is None for r in app.store.list('report',family))
            before=len(handler.calls);Scheduler(app).tick();scan=idle(app,worker,handler,family)
            checks['known_reports_zero_daily_calls']=len(handler.calls)==before and scan['all_idle'] and scan['unchanged']
            phases.append({'phase':'report_gate','idle':scan})
        else:
            injector=None;injection=[]
            if case=='inbound_interleave':
                def inject():
                    deadline=time.monotonic()+args.budget_seconds
                    while not (gate/'reached').exists() and time.monotonic()<deadline:time.sleep(.1)
                    if not (gate/'reached').exists():injection.append({'reached':False});return
                    received=inbound(app,family,'请查这次记录里第1天完成了吗，作业是否提交？','late-parent-input')
                    injection.append({'reached':True,'received':safe(received)})
                    (gate/'release').touch()
                injector=Thread(target=inject,daemon=True);injector.start()
            Scheduler(app).tick();phase=out/'daily-first';phase.mkdir()
            drain(worker,app,[family],phase,10,wait_cap=150)
            if injector:injector.join(timeout=1)
            save(out/'injection.json',injection)
            first=snapshot(app,family);phases.append({'phase':'day1','records':first,'idle':idle(app,worker,handler,family)})
            checks['first_daily_committed']=sum(p['business_date']=='2026-10-04' for p in first['plan'])==1
            if case=='family_requests':
                receipt_rows={row[0]:json.loads(row[1]) for row in app.store.connection.execute(
                    'SELECT request_id,result FROM receipts WHERE family_id=?',(family,))}
                decision_confirmation=decision_confirmation_evidence(
                    first,ledger(app),receipt_rows,app.store.family(family)['recipient'])
                checks['accepted_delivery_has_decision_confirmation']=decision_confirmation[
                    'accepted_delivery_has_decision_confirmation']
                save(out/'decision-confirmation-evidence.json',decision_confirmation)
            if case=='create_and_reuse':
                checks['real_goal_and_delivery_created']={e['completion']['kind'] for e in first['event'] if e.get('candidate_ref')}=={'goal','delivery'}
                assembly=json.loads((out/'assembly-01.json').read_text(encoding='utf8'))
                context=json.loads(assembly['prompt'])
                checks['all_old_responsibilities_in_context']={'waiting-without-review','not-selected-yesterday'}<={e['id'] for e in context['events']}
                checks['old_responsibilities_preserved']=all(e['status'] not in {'done','cancelled'} for e in first['event'] if e.get('fixture_provenance'))
            if case=='due_today':
                today=next((p for p in first['plan'] if p['business_date']=='2026-10-04'),None)
                members={item['event_id'] for item in today['items']} if today else set()
                checks['active_members_observed']=bool(members)
                checks['selected_members_have_original_steps']=bool(members) and all(
                    event.get('next_action') for event in first['event'] if event['id'] in members)
            if case in {'empty','coverage_gap'}:checks['no_untrusted_events']=not first['event']
            if case=='inbound_interleave':
                checks['arrival_revoked_original_planner']=any(j.get('attempt',0)>=2 and j['kind']=='daily' for j in first['job'])
                checks['late_input_answered']=bool(injection) and any(op['stage']=='sent' and op['reply_to'] for op in first['operation'])
            if case in {'create_and_reuse','coverage_gap'}:
                if case=='coverage_gap':
                    bind_course(app,family)
                    calls=len(handler.calls);after_fill=idle(app,worker,handler,family)
                    checks['late_source_no_second_daily']=len(handler.calls)==calls and after_fill['unchanged'] and len(app.store.list('plan',family))==1
                save(clock,{'now':'2026-10-05T08:00:00+08:00'})
                Scheduler(app).tick();phase=out/'daily-next';phase.mkdir()
                drain(worker,app,[family],phase,10,wait_cap=150)
                second=snapshot(app,family);phases.append({'phase':'day2','records':second,'idle':idle(app,worker,handler,family)})
                checks['next_day_one_daily']=sum(p['business_date']=='2026-10-05' for p in second['plan'])==1
                if case=='create_and_reuse':
                    checks['canonical_original_ids_reused']={e['id'] for e in first['event']}=={e['id'] for e in second['event']}
                else:checks['confirmed_source_later_creates_goal']=any(e['completion']['kind']=='goal' for e in second['event'])
        checks['every_phase_stable']=all(p['idle']['all_idle'] and p['idle']['unchanged'] for p in phases)
        checks['zero_active_sends']=all(op['reply_to'] and not op.get('event_id') for op in app.store.list('operation',family))
        checks['unique_day_plans']=len({p['business_date'] for p in app.store.list('plan',family)})==len(app.store.list('plan',family))
        before=(len(handler.calls),channel_for(app).count(),app.store.family(family)['version'])
        app.close()
        restarted=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--run',args.run,
            '--case',case,'--restart-probe','--budget-seconds',str(args.budget_seconds)],cwd=project,
            timeout=args.budget_seconds+90,check=False)
        restart=json.loads((out/'restart-child.json').read_text(encoding='utf8'))
        app=Application(state/'business.sqlite',Clock(shared_path=clock),mode='daily',strict_runtime=True)
        handler.app=app;worker=Worker(app,handler,lease_seconds=9)
        handler.calls=[json.loads(p.read_text(encoding='utf8')) for p in sorted(out.glob('model-*.json'))]
        checks['restart_no_new_model_send_or_version']=restarted.returncode==0 and restart['process_id']!=os.getpid() and restart['all_idle'] and restart['unchanged'] and before==(len(handler.calls),channel_for(app).count(),app.store.family(family)['version'])
        save(out/'phases.json',phases);save(out/'restart.json',restart)
        save(out/'records.json',snapshot(app,family));save(out/'channel-ledger.json',ledger(app))
        traces=[t for call in handler.calls for t in call['native'].get('tool_trace',[])]
        save(out/'tool-trace.json',traces);sanitized_database(app,out/'database.redacted.sqlite')
        result={'case':case,'program_checks':checks,'program_passed':all(checks.values()),
                'model_runs':len(handler.calls),'api_calls':sum(c['native'].get('api_calls') or 0 for c in handler.calls),
                'semantic_status':'not_reviewed','gate_passed':False}
        save(out/'result.json',result);return result
    finally:app.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True)
    parser.add_argument('--case',choices=['all',*CASES],default='all');parser.add_argument('--budget-seconds',type=int,default=360)
    parser.add_argument('--restart-probe',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',args.run):parser.error('Use a fresh simple run name')
    project=Path(__file__).resolve().parents[2];results=[]
    if args.restart_probe:
        if args.case=='all':parser.error('A restart probe requires one case')
        return restart_probe(project,args)
    for case in CASES if args.case=='all' else [args.case]:
        result=run_case(project,args,case);results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
    save(project/'evaluations/backend-stage4'/args.run/('summary-'+args.case+'.json'),results)
    return int(not all(r['program_passed'] for r in results))


if __name__=='__main__':raise SystemExit(main())
