"""Bounded manual demo runner, isolated from immutable acceptance evidence."""
from datetime import datetime
import hashlib,json,re
from pathlib import Path
from uuid import uuid4

from accept_stage4_passive import (Application,Clock,HermesHost,Worker,approval,RecordingHandler,
    ledger,save,safe,seed_sources,snapshot,sanitized_database)
from digital_tutor.domain import timestamp
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import reconcile
from digital_tutor.runtime_config import reply_scope
from stage4_passive_fixtures import frozen_inputs


class DemoError(Exception):pass


class InputEncodingError(DemoError):
    code='INPUT_ENCODING_INVALID'


def validate_message(message):
    if not isinstance(message,str) or not message.strip():raise DemoError('Message cannot be empty')
    try:message.encode('utf-8',errors='strict')
    except UnicodeEncodeError as exc:
        raise InputEncodingError('Input is not valid Unicode; use UTF-8 terminal input or select --input-encoding gb18030 for a GBK terminal. No message was accepted.') from exc


def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:raise DemoError('Invalid or missing manual-demo metadata: '+path.name) from exc


def source_fingerprint(project):
    required=('backend/scripts/demo_passive.py','backend/scripts/manual_demo_runtime.py',
              'backend/scripts/accept_stage4_passive.py','backend/scripts/stage4_passive_fixtures.py')
    paths=[project/p for p in required]
    if not all(p.is_file() for p in paths):raise DemoError('Manual demo implementation is not installed in this project')
    for directory,extension in (('backend/src','*.py'),('agent/prompts','*.md')):
        paths.extend((project/directory).rglob(extension))
    return {p.relative_to(project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def dedicated_paths(project,run):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}',run):
        raise DemoError('Run must be a plain identifier, never a path')
    roots=[project/'state/backend-manual-demo',project/'evaluations/backend-manual-demo']
    paths=[]
    for root in roots:
        if root.resolve()!=root or not root.resolve().is_relative_to(project.resolve()):raise DemoError('Manual namespace cannot redirect through a symlink')
        path=root/run
        if path.is_symlink() or path.resolve().parent!=root.resolve():raise DemoError('Manual run cannot be a symlink or external path')
        paths.append(path)
    return paths


def assert_safe_storage(project,run,state,out):
    # Validate before opening either SQLite connection and again at every turn.
    # SQLite may write sidecar files even when merely opening an existing database.
    if dedicated_paths(project,run)!=[state,out]:raise DemoError('Manual storage location changed')
    protected=[state/name for name in ('clock.json','manual-demo.json','runs')]
    protected.append(out/'manual-demo.json')
    for database in ('business.sqlite','business.channel.sqlite'):
        protected.extend(state/(database+suffix) for suffix in ('','-wal','-shm','-journal'))
    if any(path.is_symlink() for path in protected):
        raise DemoError('Manual database, ledger and sidecar files cannot redirect through symlinks')


class ManualDemo:
    def __init__(self,project,run,*,resume=False,clock=None,host_factory=HermesHost,approval_loader=approval):
        self.project=Path(project).resolve();self.run=run;self.app=None
        self.approval_loader=approval_loader
        self.state,self.out=dedicated_paths(self.project,run)
        if clock is not None:timestamp(clock)  # Validate before creating anything.
        approved=approval_loader(self.project)
        sources=source_fingerprint(self.project)
        existing=self.state.exists() or self.out.exists()
        if existing and not resume:raise DemoError('Run already exists; explicit --resume is required')
        if resume and not (self.state.is_dir() and self.out.is_dir()):raise DemoError('Resume requires an existing manual demo')
        if resume:
            assert_safe_storage(self.project,self.run,self.state,self.out)
            marker=read_json(self.state/'manual-demo.json');mirror=read_json(self.out/'manual-demo.json')
            if marker!=mirror or marker.get('owner')!='manual_demo' or marker.get('schema')!=1 or marker.get('run')!=run or marker.get('mode')!='passive' or marker.get('channel')!='mock' or marker.get('strict_runtime') is not True:
                raise DemoError('Ownership marker mismatch; acceptance states cannot be resumed')
            if marker.get('source_sha256')!=sources or marker.get('approved_materials')!=approved:
                raise DemoError('Source or approved material version changed; use a new manual run')
            if not (self.state/'business.sqlite').is_file():raise DemoError('Manual database is missing')
            self.marker=marker
        else:
            self.state.mkdir(parents=True);self.out.mkdir(parents=True)
            self.state.chmod(0o700);self.out.chmod(0o700)
            self.marker={'owner':'manual_demo','schema':1,'run':run,'instance_id':uuid4().hex,
                'mode':'passive','strict_runtime':True,'channel':'mock','classification':'synthetic_family_only',
                'source_sha256':sources,'approved_materials':approved,'acceptance':'manual_demo_not_gate'}
            save(self.state/'manual-demo.json',self.marker);save(self.out/'manual-demo.json',self.marker)
            save(self.state/'clock.json',{'mode':'fixed','now':clock} if clock else {'mode':'realtime'})
        assert_safe_storage(self.project,self.run,self.state,self.out)
        if clock is not None:save(self.state/'clock.json',{'mode':'fixed','now':clock})
        clock_spec=read_json(self.state/'clock.json')
        if clock_spec.get('mode') not in {'fixed','realtime'}:raise DemoError('Invalid manual clock mode')
        business_clock=Clock(shared_path=self.state/'clock.json') if clock_spec['mode']=='fixed' else Clock()
        self.app=Application(self.state/'business.sqlite',business_clock,mode='passive',strict_runtime=True)
        try:
            if resume:
                self.families={'a':'synthetic-4b1-a'}
                records=[dict(r) for r in self.app.store.connection.execute('SELECT * FROM families')]
                expected=[{'id':self.families['a'],'uid':'synthetic-4b1-uid-a','recipient':'mock:4b1:a','mock':1}]
                if [{k:r[k] for k in expected[0]} for r in records]!=expected:
                    raise DemoError('Manual family identity/mock scope changed')
            else:
                inputs=frozen_inputs('course');inputs.update(case='manual_demo',rounds=[],business_time=self.app.clock.iso())
                self.families=seed_sources(self.app,inputs)
                save(self.out/'source-inputs.json',inputs)
                save(self.out/'source-rows.json',[dict(r) for r in self.app.store.connection.execute('SELECT * FROM learning_days')])
            self.host=host_factory(self.project,self.state/'runs',300,approved_materials=approved)
            self.handler=RecordingHandler(self.host,self.app,list(self.families.values()),self.out)
            self.worker=Worker(self.app,self.handler,lease_seconds=9)
        except BaseException:
            self.close();raise

    def close(self):
        if self.app is not None:self.app.close();self.app=None

    def reconcile_originals(self):
        assert_safe_storage(self.project,self.run,self.state,self.out)
        changes=[];uncertain=[];observations=[]
        for family in self.families.values():
            for old in self.app.store.list('operation',family):
                if old['stage'] not in {'unknown','accepted','dispatching'}:continue
                observed=channel_for(self.app).query(old['id'])
                current=reconcile(self.app,family,old['id'])
                observations.append({'operation_id':old['id'],'independent_stage':observed,
                    'before_stage':old['stage'],'after_stage':current['stage'],'changed':current['version']!=old['version']})
                if current['version']!=old['version']:changes.append(current)
                if current['stage'] in {'unknown','accepted','dispatching'}:uncertain.append(current['id'])
        return {'observations':observations,'changes':changes,'uncertain':uncertain}

    def turn(self,message=None,*,max_steps=10):
        if message is not None:validate_message(message)
        assert_safe_storage(self.project,self.run,self.state,self.out)
        if source_fingerprint(self.project)!=self.marker['source_sha256'] or self.approval_loader(self.project)!=self.marker['approved_materials']:
            raise DemoError('Source or approved materials changed during this session; start a new manual run')
        turns=[int(p.name[5:]) for p in self.out.glob('turn-*') if p.is_dir() and re.fullmatch(r'turn-[0-9]+',p.name)]
        number=max(turns,default=0)+1;directory=self.out/('turn-%04d'%number);directory.mkdir();directory.chmod(0o700)
        self.handler.out=directory;self.handler.calls=[]
        before={row['operation_id']:row for row in ledger(self.app)}
        save(directory/'input.json',{'message':message,'business_time':self.app.clock.iso(),
            'clock':read_json(self.state/'clock.json'),'classification':'fictional_parent_input'})
        manifest={'manual_demo_instance':self.marker['instance_id'],'turn':number,'state':'started',
            'source_sha256':self.marker['source_sha256'],'approved_materials':self.marker['approved_materials'],
            'acceptance':'manual_demo_not_gate','channel':'mock','mode':'passive','strict_runtime':True}
        save(directory/'manifest.json',manifest)
        if message is not None:
            token=self.app.open_session(self.families['a'],'channel')
            try:self.app.ingest_message(token,{'external_id':'manual-turn-'+str(number),
                'body':message,'occurred_at':self.app.clock.iso()})
            finally:self.app.revoke_session(token)
        results=[];observations=[];changes=[];exhausted=False
        try:
            for index in range(max_steps):
                receipt=self.reconcile_originals();observations.extend(receipt['observations']);changes.extend(receipt['changes'])
                # The existing queue fences repeated recovery by its original-operation
                # signature. Uncertainty must not block a new inbound service/control job.
                result=self.worker.run_once();results.append(result)
                save(directory/('worker-%02d.json'%index),{'worker':result,'records':snapshot(self.app,list(self.families.values())),
                    'business_time':self.app.clock.iso(),'independent_channel':ledger(self.app)})
                if not result['ran']:break
            else:exhausted=True
        except Exception as exc:
            results.append({'ran':False,'error':type(exc).__name__})
            save(directory/'runner-error.json',{'error_type':type(exc).__name__,'error_code':getattr(exc,'code',None)})
        channel=ledger(self.app)
        changed_confirmed={op['id'] for op in changes if op['stage'] in {'sent','delivered'}}
        replies=[{'operation_id':row['operation_id'],'stage':row['stage'],'body':row['payload']['body']}
            for row in channel if row['stage'] in {'sent','delivered'} and (row['operation_id'] not in before or row['operation_id'] in changed_confirmed)]
        unresolved=[]
        for family in self.families.values():
            scope=reply_scope(self.app,family)
            unresolved.extend({'message_id':mid,'body':self.app.store.get('message',mid,family)['body'],'pending_intents':intents}
                              for mid,intents in scope.items())
        jobs=[j for f in self.families.values() for j in self.app.store.list('job',f)]
        failures=[{'job_id':j['id'],'reason':j.get('reason')} for j in jobs if j['status']=='failed']
        failures.extend({'operation_id':op['id'],'reason':'CHANNEL_OPERATION_FAILED'}
            for family in self.families.values() for op in self.app.store.list('operation',family) if op['stage']=='failed')
        failures.extend({'error':r['error']} for r in results if r.get('error'))
        waits=[{'job_id':j['id'],'reason':j.get('reason'),'retry_at':j.get('not_before')}
               for j in jobs if j['status'] in {'waiting','ready','leased'}]
        status='failed' if failures else 'waiting' if unresolved or waits or exhausted else 'replied' if replies else 'idle'
        summary={'status':status,'exit_code':1 if failures else 2 if status=='waiting' else 0,
            'model_runs':len(self.handler.calls),'channel_replies':replies,'unanswered':unresolved,
            'waiting':waits,'failures':failures,'bounded_steps_exhausted':exhausted,
            'reconciliation':observations,'evidence':str(directory),'acceptance':'manual_demo_not_gate'}
        save(directory/'records.json',snapshot(self.app,list(self.families.values())))
        save(directory/'channel-ledger.json',channel);save(directory/'summary.json',summary)
        sanitized_database(self.app,directory/'database.redacted.sqlite')
        manifest.update(state='finished',model_runs=summary['model_runs'],status=status,exit_code=summary['exit_code'])
        save(directory/'manifest.json',manifest)
        return summary
