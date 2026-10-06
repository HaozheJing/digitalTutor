"""Finite manual daily session; synthetic source setup is not model planning."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator
from digital_tutor.application import Application, Clock
from digital_tutor.candidates import CandidateSource, configure_source
from digital_tutor.course_binding import confirm_course_binding, LessonBinding
from digital_tutor.domain import Strict, ID, timestamp
from digital_tutor.hermes_host import HermesHost
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import reconcile
from digital_tutor.runtime_assembly import approval
from digital_tutor.runtime_config import reply_scope
from digital_tutor.scheduler import Scheduler, Worker
from digital_tutor.storage import encode
from accept_stage4_passive import RecordingHandler, ledger, save, safe, sanitized_database
from manual_demo_runtime import DemoError, validate_message, read_json

FAMILY='synthetic-manual-daily-family'
UID='synthetic-manual-daily-uid'
RECIPIENT='mock:manual-daily'
BATCH='synthetic-manual-daily-batch'
KINDS=('job','message','operation','event','report','plan','run_outcome','control','pending','memory',
       'candidate','candidate_source','candidate_decision','course_binding')


class DaySource(Strict):
    day: int = Field(strict=True,ge=1,le=240)
    attendance_status: Literal['attended','absent','unknown'] = 'unknown'
    completion_status: Literal['completed','incomplete','unknown'] = 'unknown'
    homework_status: Literal['submitted','not_submitted','unknown'] = 'unknown'


class CourseSource(Strict):
    course_id: ID
    course_version: ID
    lesson_map: list[LessonBinding] = Field(max_length=30)
    coverage_through: str | None = None


class ManualSources(Strict):
    synthetic: Literal[True]
    imported_at: str
    coverage_through: str | None = None
    learning_days: list[DaySource] = Field(default_factory=list,max_length=30)
    course: CourseSource | None = None
    rules: list[CandidateSource] = Field(default_factory=list,max_length=10)

    @model_validator(mode='after')
    def source_contract(self):
        timestamp(self.imported_at)
        if self.coverage_through:timestamp(self.coverage_through)
        days={d.day for d in self.learning_days}
        if len(days)!=len(self.learning_days):raise ValueError('Duplicate source day')
        if self.course:
            if self.course.coverage_through:timestamp(self.course.coverage_through)
            mapped=[x.day for x in self.course.lesson_map]
            if len(set(mapped))!=len(mapped) or not set(mapped)<=days:
                raise ValueError('Course map must use distinct actual source days')
        if len({r.source_id for r in self.rules})!=len(self.rules):raise ValueError('Duplicate source rule')
        for rule in self.rules:
            for field in ('active_from','active_until','due_at'):timestamp(getattr(rule,field))
            if rule.source_kind=='makeup_rule':
                if rule.batch_id!=BATCH or rule.day not in days or not rule.required_coverage_at:
                    raise ValueError('Makeup rule requires this synthetic batch/day/coverage')
                timestamp(rule.required_coverage_at)
            else:
                if not rule.contract_id or not rule.period_start or not rule.period_end or not rule.parts:
                    raise ValueError('Delivery requires a confirmed text-only contract/period')
                timestamp(rule.period_start);timestamp(rule.period_end)
        return self


def fingerprint(project):
    paths=[]
    for directory,pattern in (('backend/src','*.py'),('backend/scripts','*.py'),('agent/prompts','*.md')):
        paths.extend((project/directory).rglob(pattern))
    required=['backend/scripts/demo_daily.py','backend/scripts/manual_daily_runtime.py']
    if not all((project/name).is_file() for name in required):raise DemoError('Daily demo is not installed')
    return {p.relative_to(project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def locations(project,run):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',run):raise DemoError('Use a fresh plain run identifier')
    paths=[project/base/run for base in ('state/backend-manual-daily','evaluations/backend-manual-daily')]
    for path in paths:
        if path.resolve()!=path or not path.resolve().is_relative_to(project):
            raise DemoError('Daily demo namespace must not redirect through links')
    return paths


def check_storage(project,run,state,out):
    if locations(project,run)!=[state,out]:raise DemoError('Daily demo location changed')
    paths=[state/'daily-demo.json',state/'clock.json',state/'runs',out/'daily-demo.json',out/'source-inputs.json']
    for name in ('business.sqlite','business.channel.sqlite'):
        paths.extend(state/(name+s) for s in ('','-wal','-shm','-journal'))
    if any(p.is_symlink() or (p.is_file() and p.stat().st_nlink>1) for p in paths):
        raise DemoError('Daily demo storage cannot link to other business/evidence files')


def initialize_sources(app,spec):
    """Only original external source data/contracts; never create Event/Plan/Report."""
    app.create_family(FAMILY,UID,RECIPIENT)
    with app.store.transaction() as db:
        db.execute('INSERT INTO imports VALUES(?,?)',(BATCH,encode({
            'batch_id':BATCH,'synthetic':True,'imported_at':spec.imported_at,
            'coverage_through':spec.coverage_through,'source_timezone':'Asia/Shanghai','course_binding':None})))
        for row in spec.learning_days:
            db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (BATCH,UID,row.day,row.attendance_status,row.completion_status,row.homework_status,
                 'not_configured','not_checked',None,None,encode({'synthetic':True,'source':'manual-source-json','day':row.day})))
    admin=app.open_session(FAMILY,'admin')
    try:
        if spec.course:
            confirm_course_binding(app,admin,{'request_id':'manual-course-source','batch_id':BATCH,
                'expected_family_version':app.store.family(FAMILY)['version'],
                'source_note':'Explicit synthetic manual demo source; no real learner data',
                **spec.course.model_dump(),'source_timezone':'Asia/Shanghai'})
        for rule in spec.rules:configure_source(app,admin,rule.model_dump())
    finally:app.revoke_session(admin)


class ManualDaily:
    def __init__(self,project,run,*,resume=False,clock=None,sources=None,
                 host_factory=HermesHost,approval_loader=approval):
        self.project=Path(project).resolve();self.run=run;self.app=None;self.approval_loader=approval_loader
        self.state,self.out=locations(self.project,run)
        if clock:timestamp(clock)
        if resume and sources is not None:raise DemoError('Source replacement is not supported on resume; create a new run')
        spec=ManualSources.model_validate(sources if sources is not None else {'synthetic':True,'imported_at':clock or Clock().iso()})
        approved=approval_loader(self.project);sha=fingerprint(self.project)
        if (self.state.exists() or self.out.exists()) and not resume:raise DemoError('Existing run requires explicit --resume')
        if resume:
            check_storage(self.project,run,self.state,self.out)
            marker=read_json(self.state/'daily-demo.json')
            if marker!=read_json(self.out/'daily-demo.json') or any(marker.get(k)!=v for k,v in
                    {'owner':'manual_daily','schema':1,'run':run,'mode':'daily','channel':'mock','strict_runtime':True,'initialized':True}.items()):
                raise DemoError('Ownership marker mismatch; cannot resume an acceptance database')
            if marker.get('source_sha256')!=sha or marker.get('approved_materials')!=approved:
                raise DemoError('Source/material version changed; use a new manual run')
            if marker.get('source_config_sha256')!=hashlib.sha256(encode(read_json(self.out/'source-inputs.json')).encode()).hexdigest():
                raise DemoError('Original synthetic source snapshot changed')
            if not (self.state/'business.sqlite').is_file():raise DemoError('Manual daily database is missing')
            self.marker=marker
        else:
            self.state.mkdir(parents=True,exist_ok=False);self.out.mkdir(parents=True,exist_ok=False)
            self.state.chmod(0o700);self.out.chmod(0o700)
            self.marker={'owner':'manual_daily','schema':1,'run':run,'instance_id':uuid4().hex,'initialized':False,
                'mode':'daily','channel':'mock','strict_runtime':True,'classification':'synthetic_only',
                'source_sha256':sha,'approved_materials':approved,'model_constraint':'qwen3.8-flash',
                'source_config_sha256':hashlib.sha256(encode(spec.model_dump()).encode()).hexdigest(),
                'acceptance':'manual_demo_not_gate'}
            save(self.state/'daily-demo.json',self.marker);save(self.out/'daily-demo.json',self.marker)
            save(self.state/'clock.json',{'mode':'fixed','now':clock} if clock else {'mode':'realtime'})
        check_storage(self.project,run,self.state,self.out)
        if clock:save(self.state/'clock.json',{'mode':'fixed','now':clock})
        clock_spec=read_json(self.state/'clock.json')
        if clock_spec.get('mode') not in {'fixed','realtime'}:raise DemoError('Invalid business clock')
        business_clock=Clock(shared_path=self.state/'clock.json') if clock_spec['mode']=='fixed' else Clock()
        self.app=Application(self.state/'business.sqlite',business_clock,
                             mode='daily',strict_runtime=True)
        try:
            if resume:
                actual=[dict(r) for r in self.app.store.connection.execute('SELECT id,uid,recipient,mock FROM families')]
                if actual!=[{'id':FAMILY,'uid':UID,'recipient':RECIPIENT,'mock':1}]:raise DemoError('Synthetic family scope changed')
            else:
                initialize_sources(self.app,spec);save(self.out/'source-inputs.json',spec.model_dump())
                self.marker['initialized']=True
                save(self.state/'daily-demo.json',self.marker);save(self.out/'daily-demo.json',self.marker)
            self.host=host_factory(self.project,self.state/'runs',300,approved_materials=approved)
            self.handler=RecordingHandler(self.host,self.app,[FAMILY],self.out)
            self.worker=Worker(self.app,self.handler,lease_seconds=9)
        except BaseException:self.close();raise

    def close(self):
        if self.app is not None:self.app.close();self.app=None

    def turn(self,message=None,*,tick=False,clock=None,max_steps=8):
        if message is not None:validate_message(message)
        if clock:timestamp(clock)
        if not 1<=max_steps<=20:raise DemoError('max_steps must be 1..20')
        check_storage(self.project,self.run,self.state,self.out)
        if fingerprint(self.project)!=self.marker['source_sha256'] or self.approval_loader(self.project)!=self.marker['approved_materials']:
            raise DemoError('Source/material version changed during session')
        if clock:
            save(self.state/'clock.json',{'mode':'fixed','now':clock})
            self.app.clock=Clock(shared_path=self.state/'clock.json')
        numbers=[int(p.name[5:]) for p in self.out.glob('turn-*') if re.fullmatch(r'turn-[0-9]+',p.name)]
        number=max(numbers,default=0)+1;out=self.out/f'turn-{number:04d}';out.mkdir();out.chmod(0o700)
        self.handler.out=out;self.handler.calls=[]
        before={'family_version':self.app.store.family(FAMILY)['version'],'channel_count':channel_for(self.app).count()}
        save(out/'input.json',{'message':message,'tick_requested':tick,'business_time':self.app.clock.iso(),
                             'classification':'synthetic_manual_input'})
        if message is not None:
            channel=self.app.open_session(FAMILY,'channel')
            try:self.app.ingest_message(channel,{'external_id':f'manual-daily-{number}',
                                               'body':message,'occurred_at':self.app.clock.iso()})
            finally:self.app.revoke_session(channel)
        scans=[];observations=[];failures=[];exhausted=False
        try:
            if tick:
                for index in range(max_steps):
                    check_storage(self.project,self.run,self.state,self.out)
                    for old in self.app.store.list('operation',FAMILY):
                        if old['stage'] in {'unknown','accepted','dispatching'}:
                            observed=channel_for(self.app).query(old['id']);current=reconcile(self.app,FAMILY,old['id'])
                            observations.append({'id':old['id'],'observed':observed,'before':old['stage'],'after':current['stage']})
                    Scheduler(self.app).tick();result=self.worker.run_once();scans.append(result)
                    save(out/f'worker-{index:02d}.json',{'worker':result,'business_time':self.app.clock.iso()})
                    if not result['ran']:break
                else:exhausted=True
        except Exception as exc:failures.append({'type':type(exc).__name__,'code':getattr(exc,'code',None)})
        records={kind:safe(self.app.store.list(kind,FAMILY)) for kind in KINDS}
        jobs=records['job'];failures.extend({'job_id':j['id'],'reason':j.get('reason')} for j in jobs if j['status']=='failed')
        failures.extend({'error':r['error']} for r in scans if r.get('error'))
        reports=[r for r in records['report'] if r.get('resolution') is None]
        pending=[p for p in records['pending'] if p.get('disposition') in {'pending','retained'}]
        plans=[p for p in records['plan'] if p['business_date']==self.app.clock.now().date().isoformat()]
        members={item['event_id'] for p in plans for item in p['items']}
        opened=[e for e in records['event'] if e['status'] not in {'done','cancelled'}]
        waits=[{'id':j['id'],'kind':j['kind'],'reason':j.get('reason'),'retry_at':j.get('not_before')}
               for j in jobs if j['status'] in {'ready','waiting','leased'}]
        unanswered=reply_scope(self.app,FAMILY)
        active_sends=[op['id'] for op in records['operation'] if op.get('event_id') or not op.get('reply_to')]
        if active_sends:failures.append({'error':'DAILY_ACTIVE_SEND_BOUNDARY','operation_ids':active_sends})
        failures.extend({'operation_id':op['id'],'error':'CHANNEL_FAILED'} for op in records['operation'] if op['stage']=='failed')
        code=1 if failures else 2 if waits or unanswered or reports or exhausted else 0
        channel_rows=ledger(self.app)
        summary={'status':'failed' if failures else 'waiting' if code==2 else 'planned' if plans else 'idle',
            'exit_code':code,'completed_native_runs':len(self.handler.calls),
            'known_api_calls':sum(c['native'].get('api_calls') or 0 for c in self.handler.calls),
            'before':before,'after':{'family_version':self.app.store.family(FAMILY)['version'],'channel_count':channel_for(self.app).count()},
            'business_time':self.app.clock.iso(),'mode':'daily','strict_runtime':True,'channel':'mock',
            'plans':plans,'selected_events':[e for e in opened if e['id'] in members],
            'unselected_open_events':[e for e in opened if e['id'] not in members],
            'pending_inputs':pending,'unconsumed_reports':reports,'unanswered':unanswered,
            'candidate_gaps':[c for c in records['candidate'] if c['status']!='ready'],
            'channel_replies':[{'id':row['operation_id'],'stage':row['stage'],'body':row['payload']['body']}
                               for row in channel_rows if row['stage'] in {'sent','delivered'}],
            'waiting':waits,'failures':failures,'bounded_steps_exhausted':exhausted,
            'evidence':str(out),'acceptance':'manual_demo_not_gate','gate_passed':False}
        save(out/'records.json',records);save(out/'channel-ledger.json',channel_rows)
        save(out/'receipt-observations.json',observations)
        try:sanitized_database(self.app,out/'database.redacted.sqlite')
        except Exception as exc:
            summary['failures'].append({'error':'EVIDENCE_EXPORT_FAILED','type':type(exc).__name__})
            summary.update(status='failed',exit_code=1);code=1
        save(out/'summary.json',summary)
        save(out/'manifest.json',{**self.marker,'turn':number,'business_time':self.app.clock.iso(),
                                  'status':summary['status'],'exit_code':code})
        return summary
