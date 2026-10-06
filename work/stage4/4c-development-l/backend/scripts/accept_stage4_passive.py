"""4B1 real-model runner. Execute only after the independent 4A gate is accepted.

Only external inputs, source rows, business time and channel faults are fixtures.
No test code interprets input, creates plans/reports, or tells the model an oracle.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from digital_tutor.application import Application, Clock
from digital_tutor.hermes_host import HermesHost
from digital_tutor.hermes_tools import redacted
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import reconcile
from digital_tutor.runtime_assembly import approval
from digital_tutor.scheduler import Worker
from digital_tutor.storage import encode
from stage4_passive_fixtures import CASES, frozen_inputs, frozen_oracle
from stage4_sql_evidence import family_sources, learning_query_evidence

RECORDS = ('job','message','operation','event','report','plan','run_outcome','control','pending','memory')


def safe(value):
    value = redacted(value)
    if isinstance(value, dict):
        return {k:safe(v) for k,v in value.items() if k.lower() not in
                {'password','authorization','access_token','refresh_token','secret','api_key'}}
    if isinstance(value, list):
        return [safe(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'\b(?:cap|lease)-[0-9a-f]{32}\b', '[redacted-capability]', value)
        return re.sub(r'(?i)bearer\s+[A-Za-z0-9._-]+', 'Bearer [redacted]', value)
    return value


def save(path, value):
    path.write_text(json.dumps(safe(value), ensure_ascii=False, indent=2), encoding='utf-8')
    path.chmod(0o600)


def snapshot(app, families):
    return {family:{kind:safe(app.store.list(kind,family)) for kind in RECORDS} for family in families}


def ledger(app):
    return [{'operation_id':row[0], 'payload':safe(json.loads(row[1])), 'stage':row[2]}
            for row in channel_for(app).db.execute('SELECT operation_id,payload,stage FROM delivery ORDER BY rowid')]


def sanitized_database(app, target):
    """Publish one closed, compact, redacted DB; never mutate the source or old export."""
    import os
    import tempfile
    target=Path(target)
    if target.exists() or target.is_symlink():
        raise FileExistsError('Refusing to replace an existing evidence export')
    if any(Path(str(target)+suffix).exists() or Path(str(target)+suffix).is_symlink()
           for suffix in ('-wal','-shm','-journal')):
        raise FileExistsError('Export destination has pre-existing SQLite sidecars')
    fd,name=tempfile.mkstemp(prefix='.'+target.name+'-',suffix='.tmp',dir=target.parent)
    os.close(fd);temporary=Path(name);copy=None
    try:
        copy=sqlite3.connect(temporary)
        # backup reads a consistent snapshot without deleting or updating source rows.
        app.store.connection.backup(copy)
        mode=copy.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
        if mode.lower()!='delete':raise RuntimeError('Export journal mode could not be closed')
        copy.execute('PRAGMA secure_delete=ON')
        with copy:
            copy.execute('DELETE FROM sessions')
            for table in ('records','history'):
                for rowid,payload in copy.execute(f'SELECT rowid,data FROM {table}').fetchall():
                    copy.execute(f'UPDATE {table} SET data=? WHERE rowid=?',
                                 (encode(safe(json.loads(payload))),rowid))
            for rowid,payload in copy.execute('SELECT rowid,result FROM receipts').fetchall():
                copy.execute('UPDATE receipts SET result=? WHERE rowid=?',
                             (encode(safe(json.loads(payload))),rowid))
        # Compact only after redaction commits. DELETE mode means the resulting
        # main file contains the final pages, not pre-redaction pages behind a WAL.
        copy.execute('VACUUM')
        if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise RuntimeError('Redacted export integrity check failed')
        copy.close();copy=None
        if any(Path(str(temporary)+s).exists() for s in ('-wal','-shm','-journal')):
            raise RuntimeError('Export did not close all SQLite sidecars')
        temporary.chmod(0o600)
        with temporary.open('rb') as handle:os.fsync(handle.fileno())
        # Same-directory hard-link publication is atomic and fails if another
        # writer already published target; unlike replace(), it never overwrites.
        os.link(temporary,target)
    finally:
        if copy is not None:copy.close()
        temporary.unlink(missing_ok=True)
        for suffix in ('-wal','-shm','-journal'):
            Path(str(temporary)+suffix).unlink(missing_ok=True)


def seed_sources(app, inputs):
    families = {name:'synthetic-4b1-'+name for name in inputs['families']}
    meta = {'batch_id':'synthetic-4b1-snapshot', 'imported_at':inputs['business_time'],
            'coverage_through':inputs['business_time'], 'source_timezone':'Asia/Shanghai',
            'course_binding':None, 'synthetic':True}
    for name,family in families.items():
        app.create_family(family, 'synthetic-4b1-uid-'+name, 'mock:4b1:'+name)
    with app.store.transaction() as db:
        db.execute('INSERT INTO imports VALUES(?,?)', (meta['batch_id'], encode(meta)))
        for name,days in inputs['learning_source'].items():
            for day,(completion,homework) in enumerate(days,1):
                db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (meta['batch_id'],'synthetic-4b1-uid-'+name,day,'attended',completion,
                     homework,'not_configured','not_checked','simulation day '+str(day),None,
                     encode({'synthetic':True,'source':'frozen_inputs','day':day,'family_fixture':name})))
    return families


class RecordingHandler:
    def __init__(self, host, app, families, out):
        self.host,self.app,self.families,self.out = host,app,families,out
        self.calls = []

    def observe_sources(self, app):
        observer=getattr(self.host,'observe_sources',None)
        if observer:observer(app)

    def __call__(self, job, tools):
        family,_ = self.app.auth(tools.token, {'service','planner_daily','planner_progress'})
        decision = self.host(job, tools)
        native = self.host.last_result or {}
        entry = {'index':len(self.calls)+1,'family_id':family,'job_id':job['id'],
                 'job_kind':job['kind'],'recovery_only':job.get('recovery_only',False),
                 'decision':safe(decision),'native':safe(native)}
        self.calls.append(entry)
        save(self.out/('model-%02d.json'%entry['index']), entry)
        run_dir = self.host.runtime_root/native.get('run_id','missing')
        assembled = run_dir/'assembly.json'
        if assembled.exists():
            save(self.out/('assembly-%02d.json'%entry['index']),json.loads(assembled.read_text()))
        return decision


def drain(worker, app, families, out, steps, *, unknown=False, wait_cap=150):
    events = []; waited = 0.0
    for index in range(steps):
        result = worker.run_once()
        record = {'worker':result,'business_time':app.clock.iso(),'records':snapshot(app,families),
                  'independent_channel':ledger(app)}
        events.append(record)
        save(out/('worker-%02d.json'%index),record)
        if unknown:
            receipts = []
            for family in families:
                for op in app.store.list('operation',family):
                    if op['stage'] in {'unknown','accepted','dispatching'}:
                        observed = channel_for(app).query(op['id'])
                        current = reconcile(app,family,op['id'])
                        receipts.append({'family_id':family,'operation_id':op['id'],
                            'before':op,'independent_observation':observed,'after':current})
            if receipts:
                save(out/('reconciliation-%02d.json'%index),receipts)
                continue
        if result['ran']:
            continue
        retries = [datetime.fromisoformat(j['not_before']) for family in families
                   for j in app.store.list('job',family)
                   if j['status']=='waiting' and j.get('not_before')]
        remaining = max(0,(min(retries)-app.runtime_clock.now()).total_seconds()) if retries else 0
        if retries and 0 < remaining <= wait_cap-waited:
            print(json.dumps({'event':'bounded_runtime_backoff','seconds':round(remaining,2)}),flush=True)
            deadline = time.monotonic()+remaining
            while time.monotonic()<deadline:
                time.sleep(min(1,deadline-time.monotonic()))
            waited += remaining
            continue
        break
    return events


def checks_for(app, families, handler, oracle, case, events):
    traces = [trace for call in handler.calls for trace in call['native'].get('tool_trace',[])]
    ops = [op for f in families for op in app.store.list('operation',f)]
    messages = [m for f in families for m in app.store.list('message',f)]
    unresolved = [m['id'] for m in messages if m['direction']=='inbound' and
        (not m['interpreted'] or any(i['requires_reply'] and not i['reply_operation_refs'] for i in m['intents']))]
    successful = {t['tool'] for t in traces if t['ok']}
    checks = {'real_model_called':bool(handler.calls),
        'planner_calls_zero':all(c['job_kind']=='service' for c in handler.calls),
        'no_plans':all(not app.store.list('plan',f) for f in families),
        'required_tool_evidence':set(oracle['required_successful_tools'])<=successful,
        'all_operations_mock':all(op.get('simulated') and op['channel']=='mock_wechat' for op in ops),
        'at_most_one_dispatch_each':all(op['dispatch_count']<=1 for op in ops),
        'effective_reply_to_only':all(op['reply_to'] and not op.get('event_id') for op in ops),
        'task_status_allowed':all(j['status'] in ({'succeeded','waiting'} if oracle.get('allow_waiting') else {'succeeded'})
                                  for f in families for j in app.store.list('job',f)),
        'unanswered_contract':not unresolved or bool(oracle.get('allow_unanswered')),
        'confirmed_reply_contract':any(op['stage'] in {'sent','delivered'} for op in ops)
                                   or oracle.get('require_confirmed_reply') is False,
        'internal_outcomes_exist':all(app.store.list('run_outcome',f) for f in families),
        'inactive_capabilities':app.store.connection.execute('SELECT count(*) FROM sessions WHERE active=1').fetchone()[0]==0}
    if oracle.get('require_parameterized_sql'):
        target_oracle=oracle['parameterized_sql_target']
        family='synthetic-4b1-'+target_oracle['family_fixture']
        if family not in families:
            raise ValueError('SQL evidence oracle family is outside this case')
        target={'uid':app.store.family(family)['uid'],'batch_id':target_oracle['batch_id'],
                'day':target_oracle['day']}
        handler.parameterized_sql_evidence=learning_query_evidence(
            handler.calls,family,family_sources(app,family),target)
        checks['parameterized_sql_observed']=handler.parameterized_sql_evidence['observed']
    if case=='unknown':
        checks['unknown_first_preserved'] = any(op['stage']=='unknown' for event in events for data in
            event['records'].values() for op in data['operation'])
        checks['same_job_internal_recovery'] = any(c['recovery_only'] for c in handler.calls)
        checks['single_original_operation'] = len(ops)==1 and channel_for(app).count()==1
    if case in {'missing','knowledge_error'}:
        checks['unmet_question_remains_actionable'] = bool(unresolved) and any(j['status']=='waiting' for f in families for j in app.store.list('job',f))
    if case=='knowledge_error':
        checks['actual_knowledge_error_observed'] = any(t['tool']=='knowledge.read' and t.get('error')=='KNOWLEDGE_UNAVAILABLE' for t in traces)
    if case=='isolation':
        checks['no_other_uid_in_family_trace'] = all('synthetic-4b1-uid-'+other not in json.dumps(call['native'])
            for call in handler.calls for other in ('a','b') if call['family_id']!='synthetic-4b1-'+other)
        checks['reply_family_binding'] = all(op['recipient']==app.store.family(f)['recipient']
            for f in families for op in app.store.list('operation',f))
    return checks, traces, unresolved


def run_case(project, run, case, args, approved):
    state = project/'state/backend-stage4'/run/case
    out = project/'evaluations/backend-stage4'/run/case
    state.mkdir(parents=True,exist_ok=False); out.mkdir(parents=True,exist_ok=False)
    state.chmod(0o700); out.chmod(0o700)
    inputs,oracle = frozen_inputs(case),frozen_oracle(case)
    save(out/'inputs.json',inputs); save(out/'oracle.json',oracle)
    clock = state/'clock.json'; save(clock,{'now':inputs['business_time']})
    app = Application(state/'business.sqlite',Clock(shared_path=clock),mode='passive',strict_runtime=True)
    try:
        families = seed_sources(app,inputs)
        app.mock_send_outcome = inputs.get('mock_send_outcome','sent')
        save(out/'source-rows.json',[dict(r) for r in app.store.connection.execute('SELECT * FROM learning_days')])
        manifest = {'case':case,'execution_host':'server','real_model':not args.prepare_only,'real_channel':False,
            'semantic_status':'not_reviewed','approval':approved,'state_root':str(state),
            'input_sha256':hashlib.sha256((out/'inputs.json').read_bytes()).hexdigest(),
            'oracle_sha256':hashlib.sha256((out/'oracle.json').read_bytes()).hexdigest(),
            'source_sha256':{str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest()
                for directory in ('backend/src','agent/prompts','backend/scripts')
                for p in (project/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts}}
        save(out/'manifest.json',manifest)
        if args.prepare_only:
            return {'case':case,'prepared_only':True,'model_calls':0,'passed':False}
        host = HermesHost(project,state/'runs',args.budget_seconds,approved_materials=approved,
            test_hooks={'before:knowledge.read':{'fault':'knowledge_unavailable'}} if inputs.get('knowledge_fault') else None)
        handler = RecordingHandler(host,app,list(families.values()),out)
        worker = Worker(app,handler,lease_seconds=9)
        events = []
        for turn,batch in enumerate(inputs['rounds']):
            for index,(name,body) in enumerate(batch):
                channel = app.open_session(families[name],'channel')
                try:
                    app.ingest_message(channel,{'external_id':f'{case}-{turn}-{index}',
                                               'body':body,'occurred_at':app.clock.iso()})
                finally:
                    app.revoke_session(channel)
            turn_out=out/('round-%02d'%turn);turn_out.mkdir()
            events += drain(worker,app,list(families.values()),turn_out,args.max_worker_steps,
                            unknown=case=='unknown',wait_cap=args.retry_wait_cap)
        checks,traces,unresolved = checks_for(app,list(families.values()),handler,oracle,case,events)
        before = {f:app.store.family(f)['version'] for f in families.values()}
        calls = len(handler.calls); count = channel_for(app).count()
        checks['twenty_idle_scans'] = all(not worker.run_once()['ran'] for _ in range(20))
        checks['idle_no_model_or_send'] = len(handler.calls)==calls and channel_for(app).count()==count
        checks['idle_no_business_version_bump'] = before=={f:app.store.family(f)['version'] for f in families.values()}
        if oracle.get('require_parameterized_sql'):
            save(out/'parameterized-sql-evidence.json',handler.parameterized_sql_evidence)
        save(out/'tool-trace.json',traces)
        save(out/'sources-read.json',[t for t in traces if t['tool'].startswith(('knowledge.','skills.','learning.'))])
        save(out/'records.json',snapshot(app,list(families.values())))
        save(out/'channel-ledger.json',ledger(app))
        sanitized_database(app,out/'database.redacted.sqlite')
        result={'case':case,'program_checks':checks,'program_passed':all(checks.values()),
            'semantic_status':'not_reviewed','gate_passed':False,'unresolved_message_ids':unresolved,
            'model_runs':len(handler.calls),'api_calls':sum((c['native'].get('api_calls') or 0) for c in handler.calls),
            'message_read_modes':sorted({t.get('arguments',{}).get('mode','') for t in traces
                                        if t['tool']=='wechat.read_messages' and t['ok']}),
            'independent_channel_count':channel_for(app).count()}
        save(out/'result.json',result)
        return result
    finally:
        app.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--case',choices=['all',*CASES],default='all')
    parser.add_argument('--prepare-only',action='store_true',help='Freeze synthetic inputs without model calls')
    parser.add_argument('--max-worker-steps',type=int,default=10)
    parser.add_argument('--budget-seconds',type=int,default=300)
    parser.add_argument('--retry-wait-cap',type=int,default=150)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',args.run):
        parser.error('run must be a new simple identifier')
    if not 1<=args.max_worker_steps<=30 or not 1<=args.budget_seconds<=900 or not 0<=args.retry_wait_cap<=600:
        parser.error('invalid bounded execution budget')
    project=Path(__file__).resolve().parents[2]
    selected=list(CASES) if args.case=='all' else [args.case]
    for name in selected:
        if any((project/path/args.run/name).exists() for path in ('state/backend-stage4','evaluations/backend-stage4')):
            parser.error('run/case already exists; historical evidence will not be overwritten')
    approved=approval(project)
    results=[]
    for name in selected:
        result=run_case(project,args.run,name,args,approved)
        results.append(result)
        print(json.dumps(safe(result),ensure_ascii=False),flush=True)
    modes=sorted({mode for result in results for mode in result.get('message_read_modes',[])})
    summary={'stage':'4B1','cases':results,'real_model_calls_authorized':not args.prepare_only,
             'message_read_modes':modes,'all_latest_since_observed':{'all','latest','since'}<=set(modes),
             'program_only':True,'semantic_status':'not_reviewed','gate_passed':False}
    save(project/'evaluations/backend-stage4'/args.run/('summary-'+args.case+'.json'),summary)
    return 0 if args.prepare_only or all(r.get('program_passed') for r in results) else 1


if __name__=='__main__':
    raise SystemExit(main())
