"""One native Hermes process/profile per run; credentials remain server-local."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from uuid import uuid4

from .domain import BusinessError, require


def _stop_process(proc):
    """Only the new session created by this host is signalled, never shared services."""
    def send(sig):
        try:
            if os.name == 'posix':
                os.killpg(proc.pid, sig)
            elif proc.poll() is None:
                proc.terminate() if sig == signal.SIGTERM else proc.kill()
        except ProcessLookupError:
            pass
    send(signal.SIGTERM)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # Root may have exited while an inherited installer ignored SIGTERM.
    send(signal.SIGKILL if os.name == 'posix' else signal.SIGTERM)
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


class HermesHost:
    def __init__(self, project, runtime_root, budget_seconds=180, *, approved_materials=None, test_hooks=None, context_mode=None):
        self.project = Path(project).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.budget_seconds = budget_seconds
        self.approved_materials = approved_materials
        self.last_result = None
        self.test_hooks = test_hooks or {}
        from .context_transport import MODES
        self.context_mode=context_mode or os.environ.get('DIGITAL_TUTOR_CONTEXT_MODE','rebuild_compact')
        require(self.context_mode in MODES,'CONTEXT_MODE_INVALID','上下文模式无效')
        from .efficient_context import configured_features
        require(not configured_features() or self.context_mode in {'rebuild_compact','daily_native'},
                'CONTEXT_FEATURE_MODE_INVALID','当前精简功能仅支持rebuild_compact')

    def run(self, business, prompt, *, system=None, options=None):
        require(business.app.store.path != ':memory:', 'RUNTIME_DATABASE_REQUIRED', '进程运行需要持久化数据库')
        from .provider_routes import authenticated_role,resolve_connection
        from .prompt_profile import load
        profile_config=(load(self.project,authenticated_role(business.app,business.token))
                        if (self.project/'agent/config/prompt-profile.json').exists() else None)
        if profile_config:
            options={**(options or {}),'prompt_profile':{k:v for k,v in profile_config.items() if k!='soul'},
                     'tool_surface':profile_config['tool_surface']}
        business.app.context_mode=self.context_mode
        if self.context_mode in {'native_resume','daily_native'} and authenticated_role(business.app,business.token)=='service':
            from .native_session import scope_key,session_lock,reconcile_interrupted_turn
            family,binding=business.app.auth(business.token,{'service'})
            provider=resolve_connection(self.project,'service')
            provider={k:v for k,v in provider.items() if k!='api_key'}
            if profile_config:provider['prompt_profile_fingerprint']=profile_config['fingerprint']
            if self.context_mode=='daily_native':
                from .business_time import business_day
                day=business_day(business.app.clock.now())
                options={**(options or {}),'business_day':day}
                provider.update(context_mode=self.context_mode,business_day=day)
                from .daily_memory import service_view
                with business.app.store.transaction():
                    provider['memory_validity']=service_view(business.app,family)['validity_signature']
            from .context_transport import fingerprint
            provider['adapter_revision']=fingerprint({name:(Path(__file__).parent/name).read_text(encoding='utf-8')
                for name in ('hermes_host.py','hermes_runtime.py','hermes_tools.py','native_session.py','context_transport.py',
                             'context_quality.py','focused_context.py','request_metrics.py','tool_result_reader.py',
                             'daily_context.py','context_budget.py','business_time.py','resource_tools.py','daily_memory.py','family_memory.py',
                             'prompt_profile.py','prompt_language.py','tool_discovery.py','outcome_recovery.py','human_help.py','delivery_accounting.py')})
            from .hermes_runtime import SYSTEM
            scope=scope_key(business.app.store.path,family,system or SYSTEM,business.describe(),provider,
                            (options or {}).get('approved_materials'))
            root=self.runtime_root/'sessions'/scope
            family_root=self.runtime_root/'family-sessions'/fingerprint({'database':str(Path(business.app.store.path).resolve()),'family':family,'role':'service'})
            with session_lock(family_root),session_lock(root):
                active=family_root/'active-scope.json'
                try:
                    if active.exists():
                        old_scope=json.loads(active.read_text())['scope']
                        require(isinstance(old_scope,str) and len(old_scope)==64 and all(c in '0123456789abcdef' for c in old_scope), 'NATIVE_SCOPE_INVALID','旧会话标识无效')
                        if old_scope!=scope:reconcile_interrupted_turn(business.app,family,self.runtime_root/'sessions'/old_scope)

                    reconcile_interrupted_turn(business.app,family,root)
                except BusinessError as exc:
                    return {'completed':False,'error':exc.code,'run_id':binding.get('run_id') or uuid4().hex,
                            'context_mode':self.context_mode,'api_calls':0}
                temporary=active.with_suffix('.tmp');temporary.write_text(json.dumps({'scope':scope}),encoding='utf-8');temporary.replace(active)
                return self._run(business,prompt,system=system,options={**(options or {}),
                    'native_session_root':str(root),'native_session_scope':scope})
        return self._run(business,prompt,system=system,options=options)

    def _run(self, business, prompt, *, system=None, options=None):
        """Host-only entry. Capability and provider path go through stdin, never argv/logs."""
        require(business.app.store.path != ':memory:', 'RUNTIME_DATABASE_REQUIRED',
                '独立Hermes进程需要持久数据库路径')
        family, binding = business.app.auth(business.token, {'service','planner_daily','planner_progress','memory_daily'})
        run_id = binding.get('run_id') or uuid4().hex
        with business.app.store.transaction() as db:
            business.app.store.put('run_start',run_id,family,{'job_id':binding.get('job_id'),
                'role':binding.get('role'),'started_at_ns':time.time_ns()})
            run_sequence=db.execute("SELECT rowid FROM records WHERE kind='run_start' AND id=? AND family_id=?",(run_id,family)).fetchone()[0]
        work = self.runtime_root / run_id
        work.mkdir(parents=True, mode=0o700)
        options = dict(options or {})
        options['context_mode']=self.context_mode
        options['steering_enabled']=getattr(business.app,'steering_enabled',False)
        options['workflow_enabled']=getattr(business.app,'workflow_enabled',False)
        approved = options.get('approved_materials')
        agent_root = self.project/'agent'
        if approved:
            from .runtime_assembly import material_snapshot
            agent_root = material_snapshot(self.project, work, approved)
            options.update(agent_root=str(agent_root), exclude_materials=False, knowledge_binding=approved['knowledge_binding'])
        profile=Path(options['native_session_root'])/'profile' if options.get('native_session_root') else work/'profile'
        profile.mkdir(mode=0o700,exist_ok=True)
        (profile / 'config.yaml').write_text(
            ('model:\n  context_length: 128000\n' if self.context_mode=='daily_native' else '')
            + 'memory:\n  memory_enabled: false\n  user_profile_enabled: false\n'
            'skills:\n  inline_shell: false\n  external_dirs: '
            + json.dumps([] if (options or {}).get('exclude_materials', True) else [str(agent_root / 'skills')]) + '\n'
            'security:\n  allow_lazy_installs: false\n'
            'tools:\n  tool_search:\n    enabled: "off"\n'
            'toolsets:\n  - digital_tutor\n', encoding='utf-8')
        if options.get('prompt_profile'):
            from .prompt_profile import load,write_native_profile
            profile_config=load(self.project,binding['role'])
            require(profile_config['fingerprint']==options['prompt_profile']['fingerprint'],
                    'PROMPT_PROFILE_CHANGED','提示词配置在运行准备时发生变化')
            write_native_profile(profile,profile_config)
        env = {k:v for k,v in os.environ.items()
               if not k.startswith(('HERMES_', 'OPENAI_', 'OPENROUTER_', 'ANTHROPIC_'))}
        env.update(HERMES_HOME=str(profile), PYTHONPATH=str(self.project / 'backend/src'),
                   PYTHONUNBUFFERED='1', HERMES_DISABLE_LAZY_INSTALLS='1')
        request = {'project':str(self.project), 'database':business.app.store.path,
                   'run_started_at_ns':time.time_ns(),
                   'run_sequence':run_sequence,
                   'token':business.token, 'prompt':prompt, 'system':system,
                   'run_id':run_id, 'work':str(work), 'budget_seconds':self.budget_seconds,
                   'profile':str(profile),
                   'clock':business.app.clock.fixed.isoformat() if business.app.clock.fixed else None,
                   'service_start':business.app.service_start, 'service_end':business.app.service_end,
                   'clock_shared_path':str(business.app.clock.shared_path) if getattr(business.app.clock,'shared_path',None) else None,
                   'mode':getattr(business.app,'mode','full'), 'strict_runtime':getattr(business.app,'strict_runtime',False),
                   'mock_send_outcome':getattr(business.app,'mock_send_outcome','sent'),
                   'options':options or {}}
        from .hermes_tools import redacted
        (work/'assembly.json').write_text(json.dumps(redacted({k:v for k,v in request.items() if k!='token'}),ensure_ascii=False,indent=2),encoding='utf-8')
        python = self.project / 'vendor/hermes/venv/bin/python'
        # Native logs stay in the private run directory. They are not returned as user errors.
        with (work / 'native.log').open('w', encoding='utf-8') as log:
            proc = subprocess.Popen([str(python), '-m', 'digital_tutor.hermes_runtime'],
                                    cwd=work, env=env, stdin=subprocess.PIPE,
                                    stdout=log, stderr=log, text=True,
                                    start_new_session=os.name == 'posix')
            cancellation = None
            try:
                proc.stdin.write(json.dumps(request, ensure_ascii=False)); proc.stdin.close()
                deadline = time.monotonic() + self.budget_seconds + 45
                cancel_deadline = None
                while proc.poll() is None:
                    try:
                        business.app.auth(business.token, {'service','planner_daily','planner_progress','memory_daily'})
                    except BusinessError as exc:
                        cancellation = cancellation or exc.code
                    if time.monotonic() > deadline:
                        cancellation = cancellation or 'HOST_TIMEOUT'
                    if cancellation:
                        if cancel_deadline is None:
                            business.app.revoke_session(business.token)
                        (work / 'cancel').touch()
                        cancel_deadline = cancel_deadline or time.monotonic() + 5
                        if time.monotonic() >= cancel_deadline:
                            break
                    time.sleep(0.1)
            finally:
                if not proc.stdin.closed:
                    proc.stdin.close()
                _stop_process(proc)
        result_path = work / 'result.json'
        if not result_path.exists():
            from .usage_accounting import account
            result=account({'completed':False, 'error':cancellation or 'NATIVE_PROCESS_FAILED', 'run_id':run_id},work)
            result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            return result
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if cancellation:
            result.update(completed=False, error=cancellation)
            result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result


    def observe_sources(self, app):
        if self.approved_materials:
            from .material_recovery import observe,probe_approved
            observe(app,lambda wait:probe_approved(self,wait,as_of=app.clock.iso()))

    def __call__(self, job, business):
        business.app.context_mode=self.context_mode
        if job['kind']=='memory':
            from .daily_memory import run_job
            return run_job(self,job,business)
        from .runtime_assembly import assemble
        context, system = assemble(self.project, business.app, business.token, job)
        options = {'exclude_materials':self.approved_materials is None, 'test_hooks':self.test_hooks}
        if self.approved_materials:
            options['approved_materials'] = self.approved_materials
        result = self.run(business, json.dumps(context,ensure_ascii=False), system=system, options=options)
        self.last_result = result
        if not result.get('completed'):
            return {'retryable_error':result.get('error') or 'MODEL_INCOMPLETE','run_id':result['run_id']}
        family,binding = business.app.auth(business.token, {'service','planner_daily','planner_progress','memory_daily'})
        if context['recipe']=='service':
            rows = business.app.store.list('run_outcome',family)
            outcome = next((r for r in rows if r['id']==binding.get('run_id')),None)
            if not outcome:
                return {'retryable_error':'OUTCOME_MISSING','run_id':result['run_id']}
            return {'wait':outcome['outcome'] in {'waiting','needs_input'},
                    'no_contact':outcome['outcome']=='no_contact', 'outcome_id':outcome['id'],
                    'reason':outcome['reason'],'run_id':result['run_id']}
        return {'run_id':result['run_id']}
