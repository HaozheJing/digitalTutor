"""Native Flash acceptance. Fixtures change only incoming text, source facts and time.

No fixture creates the tested Events, plans, reports, assessments or model outcomes.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
import time
from threading import Event, Thread

from digital_tutor.application import Application, Clock
from digital_tutor.candidates import configure_source
from digital_tutor.course_binding import confirm_course_binding
from digital_tutor.hermes_host import HermesHost
from digital_tutor.learning_evidence import sync_learning_evidence
from digital_tutor.scheduler import Scheduler, Worker
from digital_tutor.runtime_assembly import approval
from digital_tutor.mock_channel import channel_for
from digital_tutor.storage import encode
from accept_stage4_passive import RecordingHandler, save, ledger, sanitized_database, seed_sources, safe
from accept_stage4_daily import bind_course, inbound, sources

KINDS=('job','message','operation','event','report','plan','pending','candidate','candidate_source',
       'candidate_decision','run_outcome','control','memory','evidence','learning_source','course_binding')


@contextmanager
def continuous_business_clock(run, start):
    """External fixture clock only; the real native child reads this same file."""
    origin=datetime.fromisoformat(start);started=time.monotonic();stop=Event()
    stats={'kind':'monotonic_shared_business_clock','start':start,'interval_seconds':0.2,
           'wall_started_at':datetime.now(timezone.utc).isoformat(),'ticks':0,'errors':[]}
    staging=run.clock.with_name(run.clock.name+'.advancing.tmp')
    def advance():
        elapsed=time.monotonic()-started
        current=origin+timedelta(seconds=elapsed)
        staging.write_text(json.dumps({'now':current.isoformat()}),encoding='utf-8')
        staging.replace(run.clock)
        stats.update(ticks=stats['ticks']+1,last_business_at=current.isoformat(),elapsed_seconds=elapsed)
    advance()
    def progress():
        try:
            while not stop.wait(0.2):advance()
        except Exception as exc:
            stats['errors'].append(type(exc).__name__);stop.set()
    thread=Thread(target=progress,name='external-business-clock',daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set();thread.join(timeout=5)
        if thread.is_alive():stats['errors'].append('clock_thread_not_stopped')
        elif not stats['errors']:advance()
        stats['wall_stopped_at']=datetime.now(timezone.utc).isoformat()
        operations=[op for op in run.app.store.list('operation',run.family) if op.get('event_id')]
        stats['outbound_timeline']=[{'operation_id':op['id'],'event_id':op['event_id'],
            'prepared_at':op.get('occurred_at'),'admitted_at':op.get('admitted_at'),
            'stage':op['stage'],'receipts':op.get('receipts',[])} for op in operations]
        stats['independent_channel_admissions']=[{'operation_id':row['operation_id'],
            'admitted_at':row['payload'].get('admitted_at'),'stage':row['stage']}
            for row in ledger(run.app) if row['payload'].get('event_id')]
        save(run.out/'continuous-clock.json',stats)
        run.checks['continuous_clock_progressed']=not stats['errors'] and stats['ticks']>=2 and stats.get('elapsed_seconds',0)>0.2
        if stats['errors']:raise RuntimeError('Continuous fixture clock failed')


class Run:
    def __init__(self, project, name, case, budget):
        self.project, self.case, self.name, self.budget = project, case, name, budget
        self.state=project/'state/backend-stage4'/name/case
        self.out=project/'evaluations/backend-stage4'/name/case
        self.state.mkdir(parents=True,exist_ok=False);self.out.mkdir(parents=True,exist_ok=False)
        self.clock=self.state/'clock.json';save(self.clock,{'now':'2026-10-04T10:00:00+08:00'})
        self.app=Application(self.state/'business.sqlite',Clock(shared_path=self.clock),mode='full',strict_runtime=True)
        inputs={'families':['a'],'business_time':'2026-10-04T07:30:00+08:00',
            'learning_source':{'a':[('incomplete','not_submitted'),('completed','submitted'),('unknown','unknown')]},
            'scope':'all families, messages, works and learning rows synthetic; approved R material bytes unchanged'}
        save(self.out/'inputs.json',inputs)
        self.family=seed_sources(self.app,inputs)['a'];bind_course(self.app,self.family)
        self.pin=approval(project)
        host=HermesHost(project,self.state/'runs',budget,approved_materials=self.pin)
        self.handler=RecordingHandler(host,self.app,[self.family],self.out)
        self.worker=Worker(self.app,self.handler,lease_seconds=9)
        self.phases=[];self.checks={}
        save(self.out/'manifest.json',{'case':case,'model':'qwen3.8-flash','approval':self.pin,
            'sha256':{str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest()
                for directory in ('backend/src','backend/scripts','agent/prompts')
                for p in (project/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts},
            'semantic_status':'not_reviewed','gate_passed':False})

    def snapshot(self):
        return {kind:safe(self.app.store.list(kind,self.family)) for kind in KINDS}

    def at(self, value):
        save(self.clock,{'now':value})

    def message(self, text, name):
        return inbound(self.app,self.family,text,name)

    def pump(self, name, maximum=16):
        phase=self.out/name;phase.mkdir()
        runs=[]
        for index in range(maximum):
            Scheduler(self.app).tick()
            result=self.worker.run_once();runs.append(result)
            save(phase/('%02d.json'%index),{'worker':result,'clock':self.app.clock.iso(),
                 'records':self.snapshot(),'channel':ledger(self.app)})
            print(json.dumps({'phase':name,'worker':result,'calls':len(self.handler.calls)},ensure_ascii=False),flush=True)
            if not result['ran']:break
            if result.get('error'):break  # Preserve a failure; don't burn API quota against the same state.
        self.phases.append({'name':name,'workers':runs,'clock':self.app.clock.iso()})
        return runs

    def idle(self, name):
        before=(len(self.handler.calls),channel_for(self.app).count(),self.app.store.family(self.family)['version'])
        scans=[]
        for _ in range(20):
            Scheduler(self.app).tick();scans.append(self.worker.run_once())
            if scans[-1]['ran']:break
        after=(len(self.handler.calls),channel_for(self.app).count(),self.app.store.family(self.family)['version'])
        result=all(not s['ran'] for s in scans) and before==after
        save(self.out/(name+'.json'),{'before':before,'after':after,'scans':scans,'unchanged':result})
        return result

    def restart_idle(self):
        # A fresh process uses the real Worker/Host, isolated DB and shared clock.
        child=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--run',self.name,
            '--case',self.case,'--budget-seconds',str(self.budget),'--restart-probe'],
            cwd=self.project,timeout=self.budget+90,check=False)
        result=json.loads((self.out/'restart.json').read_text(encoding='utf8'))
        return child.returncode==0 and result['all_idle'] and result['unchanged']

    def versioned_source(self, revision, status, previous=None):
        batch='synthetic-live-progress-'+str(revision);at=self.app.clock.iso()
        meta={'synthetic':True,'batch_id':batch,'imported_at':at,'coverage_through':at,
              'source_timezone':'Asia/Shanghai','source_contract':{'kind':'versioned_learning_snapshot',
              'source_id':'synthetic-live-progress','revision':revision,'supersedes_batch_id':previous,'observed_at':at}}
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO imports VALUES(?,?)',(batch,encode(meta)))
            db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (batch,self.app.store.family(self.family)['uid'],1,'attended',status,'unknown',
                 'unknown','unknown',None,None,batch+'!row-2'))
        admin=self.app.open_session(self.family,'admin')
        try:
            confirm_course_binding(self.app,admin,{'request_id':'bind-'+batch,'batch_id':batch,
                'expected_family_version':self.app.store.family(self.family)['version'],
                'source_note':'Synthetic versioned source ingestion',
                'course_id':'simulation-L2-fox','course_version':'simulation-v1',
                'lesson_map':[{'day':1,'lesson_ref':'LESSON-L2-FOX-D01'}],
                'coverage_through':at,'source_timezone':'Asia/Shanghai'})
            result=sync_learning_evidence(self.app,admin,batch,1)
            save(self.out/('source-'+str(revision)+'.json'),{'metadata':meta,'sync':result})
        finally:self.app.revoke_session(admin)
        return batch

    def finish(self):
        records=self.snapshot();outgoing=ledger(self.app)
        dialogue=[]
        for message in records['message']:
            if message['direction']=='inbound':dialogue.append({'role':'parent','at':message['occurred_at'],'text':message['body']})
        for operation in outgoing:
            payload=operation['payload'];dialogue.append({'role':'service','operation_id':operation['operation_id'],
                'at':payload.get('occurred_at'),'stage':operation['stage'],'text':payload.get('body'),'payload':payload})
        save(self.out/'dialogue.json',dialogue)
        save(self.out/'final.json',{'records':records,'independent_channel':outgoing,'phases':self.phases})
        save(self.out/'source-rows.json',{table:[dict(r) for r in self.app.store.connection.execute('SELECT * FROM '+table)]
                                      for table in ('imports','learning_days')})
        summary={'case':self.case,'checks':self.checks,'program_passed':bool(self.checks) and all(self.checks.values()),
                 'native_calls':len(self.handler.calls),
                 'api_calls':sum(c['native'].get('api_calls') or 0 for c in self.handler.calls),
                 'semantic_status':'not_reviewed','gate_passed':False}
        save(self.out/'summary.json',summary);sanitized_database(self.app,self.out/'redacted.sqlite')
        self.app.close();print(json.dumps(summary,ensure_ascii=False),flush=True)
        return summary


def condition_faithfully_propagated(event, pending, source, message, assessments, explicit=False):
    """An unspecified separate criterion may remain null or quote the request.

    This oracle is not sent to the model; semantic adequacy is reviewed separately.
    """
    condition=event.get('completion_condition')
    if not pending or not source or not message or not assessments:return False
    if not (pending.get('event_id')==event['id'] and pending.get('candidate_source_id')==source['id']
            and source.get('source_identity')==event.get('source_identity')
            and source.get('source_message_id')==message['id']
            and pending.get('source_message_id')==message['id']
            and pending.get('source_intent_id')==source.get('source_intent_id')):return False
    if pending.get('completion_condition')!=condition or source.get('completion_condition')!=condition:return False
    if condition is not None:
        if not isinstance(condition,str) or not condition.strip() or condition not in message['body']:return False
        if not all(a.get('criterion')==condition for a in assessments):return False
    elif not all(isinstance(a.get('criterion'),str) and a['criterion'].strip() for a in assessments):return False
    return not explicit or (bool(condition) and '总结要说清完成情况和作业提交情况' in condition)


def intraday(run, explicit=False, continuous=False):
    run.pump('baseline')
    initial=len(run.handler.calls)
    run.message('请查一下这次记录第1天完成了吗？另外，请明天下午四点给我发一份第1天的文字学情总结，今天不用发。'+
                ('总结要说清完成情况和作业提交情况。' if explicit else ''),'request')
    run.pump('admission-and-confirmation')
    events=run.app.store.list('event',run.family)
    accepted=[e for e in events if e.get('accepted_commitment')]
    run.checks['committed_intraday_delivery']=len(accepted)==1 and accepted[0]['completion']['kind']=='delivery'
    run.checks['baseline_and_admission_same_date_container']=len(run.app.store.list('plan',run.family))==1 and run.app.store.list('plan',run.family)[0].get('revision',0)>=2
    run.checks['future_not_delivered_early']=not any(op.get('event_id') for op in run.app.store.list('operation',run.family))
    run.checks['planning_then_reply_observed']=any(c['job_kind']=='daily' for c in run.handler.calls[initial:]) and bool(ledger(run.app))
    run.checks['idle_before_due']=run.idle('idle-before-due')
    if accepted:
        event=accepted[0];grant=event['accepted_commitment']
        if continuous:
            # This time is an external scenario input, not a model target-state hint.
            with continuous_business_clock(run,'2026-10-05T16:00:15+08:00'):
                run.pump('crossday-execution')
            actual=[op for op in run.app.store.list('operation',run.family) if op.get('event_id')==event['id']]
            bound=datetime.fromisoformat('2026-10-05T16:00:15+08:00')
            run.checks['actual_dispatch_after_requested_instant']=bool(actual) and all(
                op.get('admitted_at') and datetime.fromisoformat(op['admitted_at'])>=bound for op in actual)
            run.checks['receipt_time_after_actual_dispatch']=bool(actual) and all(op.get('admitted_at') and op.get('receipts') and all(
                datetime.fromisoformat(receipt['observed_at'])>=datetime.fromisoformat(op['admitted_at'])
                for receipt in op['receipts']) for op in actual)
            channel={row['operation_id']:row for row in ledger(run.app)}
            run.checks['independent_channel_records_actual_admission']=bool(actual) and all(
                op['id'] in channel and channel[op['id']]['payload'].get('admitted_at')==op.get('admitted_at') for op in actual)
        else:
            run.at(grant['valid_from']);run.pump('crossday-execution')
        latest=run.app.event(run.family,event['id'])
        run.checks['original_delivery_done']=latest['status']=='done'
        assessments=[a for r in run.app.store.list('report',run.family) for a in r['payload'].get('completion_assessments',[])
                     if a['event_id']==event['id']]
        run.checks['service_completion_assessment']=any(a['complete'] and a['criterion'].strip() and a['reason'].strip() and a['evidence_refs'] for a in assessments)
        pending=next((p for p in run.app.store.list('pending',run.family) if p.get('event_id')==event['id']),None)
        source=next((s for s in run.app.store.list('candidate_source',run.family)
                     if pending and s['id']==pending.get('candidate_source_id')),None)
        message=next((m for m in run.app.store.list('message',run.family)
                      if pending and m['id']==pending.get('source_message_id')),None)
        run.checks['condition_propagated']=condition_faithfully_propagated(event,pending,source,message,assessments,explicit)
        save(run.out/'completion-condition-coverage.json',{'event_id':event['id'],
            'condition':event.get('completion_condition'),'explicit_separate_condition_input':explicit,
            'null_condition_observed':event.get('completion_condition') is None,
            'note':'Legacy intraday-empty names the input without a separate criterion; it does not guarantee null output.'})
        if explicit:
            run.checks['assessment_keeps_explicit_condition']=bool(assessments) and all(a['criterion']==event.get('completion_condition') for a in assessments)
        run.checks['reports_consumed']=all(r['resolution'] is not None for r in run.app.store.list('report',run.family))
        run.checks['exactly_one_actual_delivery']=sum(bool(op.get('event_id')) and op['stage'] in {'sent','delivered'} for op in run.app.store.list('operation',run.family))==1
        run.checks['independent_channel_exactly_once']=sum(row['payload'].get('event_id')==event['id'] and row['stage'] in {'sent','delivered'} for row in ledger(run.app))==1
        run.checks['idle_after_delivery']=run.idle('idle-after-delivery')
        run.checks['restart_idle']=run.restart_idle()


def daily_chain(run):
    sources(run.app,run.family,due_at='2026-10-04T18:00:00+08:00')
    run.pump('daily-and-current-actions')
    events=run.app.store.list('event',run.family)
    run.checks['daily_created_goal_and_delivery']={e['completion']['kind'] for e in events}=={'goal','delivery'}
    for index in range(4):
        pending=[e for e in run.app.store.list('event',run.family) if e['status'] not in {'done','cancelled'}
                 and (e.get('next_action') or {}).get('execute_at')
                 and datetime.fromisoformat(e['next_action']['execute_at'])>run.app.clock.now()
                 and datetime.fromisoformat(e['next_action']['execute_at']).date().isoformat()=='2026-10-04']
        if not pending:break
        run.at(min(e['next_action']['execute_at'] for e in pending));run.pump('scheduled-'+str(index))
    goals=[e for e in run.app.store.list('event',run.family) if e['completion']['kind']=='goal']
    run.checks['reminder_not_goal_completion']=bool(goals) and all(e['status']!='done' for e in goals)
    run.at('2026-10-04T20:00:00+08:00')
    batch=run.versioned_source(1,'completed');run.pump('source-completion')
    run.checks['goal_done_after_real_source']=bool(goals) and all(run.app.event(run.family,e['id'])['status']=='done' for e in goals)
    run.at('2026-10-05T09:00:00+08:00')
    run.versioned_source(2,'incomplete',batch);run.pump('source-correction')
    run.checks['correction_reopens_original']=bool(goals) and all(run.app.event(run.family,e['id'])['status'] not in {'done','cancelled'} for e in goals)
    run.checks['no_duplicate_events']=len(run.app.store.list('event',run.family))==len(events)
    run.checks['reports_consumed']=all(r['resolution'] is not None for r in run.app.store.list('report',run.family))
    run.checks['daily_resumed_day2']=any(p['business_date']=='2026-10-05' and p.get('baseline_committed') for p in run.app.store.list('plan',run.family))
    run.checks['delivery_done']=any(e['completion']['kind']=='delivery' and e['status']=='done' for e in run.app.store.list('event',run.family))
    assessments=[a for r in run.app.store.list('report',run.family) for a in r['payload'].get('completion_assessments',[])]
    current=[run.app.event(run.family,e['id']) for e in events]
    run.checks['configured_null_condition_preserved']=bool(events) and all(e.get('completion_condition') is None for e in events+current)
    run.checks['null_goal_and_delivery_assessed']={e['completion']['kind'] for e in events}=={'goal','delivery'} and all(any(a['event_id']==e['id'] and a.get('complete')
        and a.get('criterion','').strip() and a.get('reason','').strip() and a.get('evidence_refs') for a in assessments) for e in events)


def restart_probe(project,args):
    state=project/'state/backend-stage4'/args.run/args.case
    out=project/'evaluations/backend-stage4'/args.run/args.case
    app=Application(state/'business.sqlite',Clock(shared_path=state/'clock.json'),mode='full',strict_runtime=True)
    try:
        family=app.store.connection.execute('SELECT id FROM families').fetchone()[0]
        host=HermesHost(project,state/'runs',args.budget_seconds,approved_materials=approval(project))
        handler=RecordingHandler(host,app,[family],out)
        handler.calls=[json.loads(p.read_text(encoding='utf8')) for p in sorted(out.glob('model-*.json'))]
        worker=Worker(app,handler,lease_seconds=9)
        before=(len(handler.calls),channel_for(app).count(),app.store.family(family)['version'])
        scans=[]
        for _ in range(20):
            Scheduler(app).tick();scans.append(worker.run_once())
            if scans[-1]['ran']:break
        after=(len(handler.calls),channel_for(app).count(),app.store.family(family)['version'])
        result={'before':before,'after':after,'all_idle':all(not r['ran'] for r in scans),'unchanged':before==after}
        save(out/'restart.json',result)
        return 0 if result['all_idle'] and result['unchanged'] else 1
    finally:app.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True)
    parser.add_argument('--case',choices=['intraday-empty','intraday-explicit','intraday-continuous','daily-chain'],required=True)
    parser.add_argument('--budget-seconds',type=int,default=240)
    parser.add_argument('--restart-probe',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args();project=Path(__file__).resolve().parents[2]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',args.run):parser.error('Use a fresh simple run name')
    if args.restart_probe:return restart_probe(project,args)
    run=Run(project,args.run,args.case,args.budget_seconds)
    try:
        daily_chain(run) if args.case=='daily-chain' else intraday(run,args.case=='intraday-explicit',args.case=='intraday-continuous')
    except Exception as exc:
        run.checks['runner_completed']=False
        save(run.out/'exception.json',{'type':type(exc).__name__,'code':getattr(exc,'code',None)})
        print(json.dumps({'runner_error':type(exc).__name__,'code':getattr(exc,'code',None)}),flush=True)
    finally:summary=run.finish()
    return int(not summary['program_passed'])


if __name__=='__main__':raise SystemExit(main())
