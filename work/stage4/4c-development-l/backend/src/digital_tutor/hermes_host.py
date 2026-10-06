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
    def __init__(self, project, runtime_root, budget_seconds=180, *, approved_materials=None, test_hooks=None):
        self.project = Path(project).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.budget_seconds = budget_seconds
        self.approved_materials = approved_materials
        self.last_result = None
        self.test_hooks = test_hooks or {}

    def run(self, business, prompt, *, system=None, options=None):
        """Host-only entry. Capability and provider path go through stdin, never argv/logs."""
        require(business.app.store.path != ':memory:', 'RUNTIME_DATABASE_REQUIRED',
                '独立Hermes进程需要持久数据库路径')
        _, binding = business.app.auth(business.token, {'service','planner_daily','planner_progress'})
        run_id = binding.get('run_id') or uuid4().hex
        work = self.runtime_root / run_id
        work.mkdir(parents=True, mode=0o700)
        options = dict(options or {})
        approved = options.get('approved_materials')
        agent_root = self.project/'agent'
        if approved:
            from .runtime_assembly import material_snapshot
            agent_root = material_snapshot(self.project, work, approved)
            options.update(agent_root=str(agent_root), exclude_materials=False, knowledge_binding=approved['knowledge_binding'])
        profile = work / 'profile'
        profile.mkdir(mode=0o700)
        (profile / 'config.yaml').write_text(
            'memory:\n  memory_enabled: false\n  user_profile_enabled: false\n'
            'skills:\n  inline_shell: false\n  external_dirs: '
            + json.dumps([] if (options or {}).get('exclude_materials', True) else [str(agent_root / 'skills')]) + '\n'
            'security:\n  allow_lazy_installs: false\n'
            'tools:\n  tool_search:\n    enabled: "off"\n'
            'toolsets:\n  - digital_tutor\n', encoding='utf-8')
        env = {k:v for k,v in os.environ.items()
               if not k.startswith(('HERMES_', 'OPENAI_', 'OPENROUTER_', 'ANTHROPIC_'))}
        env.update(HERMES_HOME=str(profile), PYTHONPATH=str(self.project / 'backend/src'),
                   PYTHONUNBUFFERED='1', HERMES_DISABLE_LAZY_INSTALLS='1')
        request = {'project':str(self.project), 'database':business.app.store.path,
                   'token':business.token, 'prompt':prompt, 'system':system,
                   'run_id':run_id, 'work':str(work), 'budget_seconds':self.budget_seconds,
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
                        business.app.auth(business.token, {'service','planner_daily','planner_progress'})
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
            return {'completed':False, 'error':cancellation or 'NATIVE_PROCESS_FAILED', 'run_id':run_id}
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if cancellation:
            result.update(completed=False, error=cancellation)
        return result


    def observe_sources(self, app):
        if self.approved_materials:
            from .material_recovery import observe,probe_approved
            observe(app,lambda wait:probe_approved(self,wait))

    def __call__(self, job, business):
        from .runtime_assembly import assemble
        context, system = assemble(self.project, business.app, business.token, job)
        options = {'exclude_materials':self.approved_materials is None, 'test_hooks':self.test_hooks}
        if self.approved_materials:
            options['approved_materials'] = self.approved_materials
        result = self.run(business, json.dumps(context,ensure_ascii=False), system=system, options=options)
        self.last_result = result
        if not result.get('completed'):
            return {'retryable_error':result.get('error') or 'MODEL_INCOMPLETE','run_id':result['run_id']}
        family,binding = business.app.auth(business.token, {'service','planner_daily','planner_progress'})
        if context['recipe']=='service':
            rows = business.app.store.list('run_outcome',family)
            outcome = next((r for r in rows if r['id']==binding.get('run_id')),None)
            if not outcome:
                return {'retryable_error':'OUTCOME_MISSING','run_id':result['run_id']}
            return {'wait':outcome['outcome'] in {'waiting','needs_input'},
                    'no_contact':outcome['outcome']=='no_contact', 'outcome_id':outcome['id'],
                    'reason':outcome['reason'],'run_id':result['run_id']}
        return {'run_id':result['run_id']}
