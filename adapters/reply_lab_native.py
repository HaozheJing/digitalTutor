"""A-owned consumer of B's native experiment API. No model client or sender."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from reply_lab import require, digest, encoded, Fault
from reply_lab_context import passive_fixture


def delivery_parts(snapshot):
    # Preserve actual content/type so continuation can use sent text only.
    return [{'part_id':p.get('id'),'status':p['status'],'simulation':True,
             'message_type':p.get('message_type'),'body':p.get('body')}
            for p in snapshot.get('parts',[])]


def pending_handoff(snapshot, database):
    """Read only: a native turn may finish with an intentionally pending alert."""
    if not snapshot.get('native_evidence', {}).get('completed_turns'):
        return False
    if not any(t.get('name') == 'request_human_confirmation' and t.get('status') == 'ok'
               for t in snapshot.get('tool_trace', [])):
        return False
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_alerts'").fetchone():
            return False
        return bool(db.execute("SELECT 1 FROM operator_alerts WHERE run_id=? AND uid=? AND status='pending' LIMIT 1",
                               (snapshot['run_id'], snapshot['family_uid'])).fetchone())


def execute_when_idle(call, task_id):
    """Retry only the explicit pre-mutation busy rejection, never an unknown outcome."""
    for attempt in range(30):
        try:
            return {**call('execute', {'task_id': task_id}), 'startup_busy_retries':attempt}
        except HTTPError as exc:
            body = exc.read(8192)
            try: reason = json.loads(body).get('error')
            except (ValueError, AttributeError): reason = None
            if exc.code != 409 or reason != 'agent_busy':
                raise Fault('原生执行接口未确认（HTTP '+str(exc.code)+'，'+str(reason or 'unknown')+'），未自动重试。',409) from None
            if attempt == 29:
                raise Fault('原生服务仍在初始化或忙碌，尚未提交主动任务。',409) from None
            time.sleep(1)


def native_mode(mode, target=None):
    """The planning case wakes the existing internal host, never a family message."""
    if mode=='proactive_expression' and target in {'celebrate-lesson-completion','remind-historical-makeup','celebrate-learning-streak','prevent-lesson-skipping','welcome-back-to-reading'}:return 'completion_action'
    if mode=='planning' and target=='plan-after-lesson-completion':return 'completion'
    if mode=='planning' and target=='review-service-case':return 'case_review'
    return {'passive':'passive', 'proactive_expression':'execution', 'planning':'planning'}[mode]


def selected_passive_skill(mode, target, skills):
    """An explicitly selected passive trial preloads only its frozen Skill."""
    if mode!='passive' or target in (None,''):return None
    require(isinstance(target,str) and target in skills, '被动试测指定的 Skill 不在冻结配置中。', 409)
    return target


def planning_result(database, snapshot, target='plan-service-day'):
    """Report the actual internal task separately from any outbound delivery."""
    completion=target=='plan-after-lesson-completion'
    review=target=='review-service-case'
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory=sqlite3.Row
        tasks=[dict(row) for row in db.execute("SELECT id,skill,status,result FROM service_tasks WHERE kind=? ORDER BY created_at",('case_review' if review else 'completion_plan' if completion else 'plan',))]
    trace=snapshot.get('tool_trace',[])
    def committed(row):
        if row.get('name')!=('service_case_review' if review else 'completion_plan_commit' if completion else 'workflow_commit') or row.get('status')!='ok':return False
        try:return json.loads(row.get('output','{}')).get('status')==('reviewed' if review else 'committed')
        except (TypeError,ValueError):return False
    return {'tasks':tasks,
            'plan_skill_loaded':any(x.get('name')=='service_skill' and x.get('status')=='ok' and target in x.get('input','') for x in trace),
            'plan_commit_observed':any(committed(x) for x in trace),
            'external_message_count':len(snapshot.get('parts',[])), 'simulation':True}


def s07_delivery_pending(snapshot):
    """Keep the existing receiver alive until S07/S08's accepted send is settled."""
    return (any(x.get('status') in ('queued','sending','accepted') for x in snapshot.get('deliveries', [])) or
            any(x.get('skill') in ('remind-reading','rebuild-reading-rhythm','close-week-reading-gap','plan-weekend-makeup','weekly-learning-review','welcome-back-to-reading') and x.get('status') == 'awaiting_delivery'
                for x in snapshot.get('tasks', [])))


class NativeRunner:
    def __init__(self, root, worker):
        self.root, self.worker = Path(root).resolve(), Path(worker).resolve()
        require(self.worker.is_relative_to(self.root / 'runtime'), 'worker 配置必须在隔离运行根。')

    def run(self, e):
        import fcntl
        with (self.root/'runtime/emoji-real-window.lock').open('a+') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Fault('原生端口正在用于真实渠道联调，请等待本次测试结束。', 429) from None
            return self._run(e)

    def _run(self, e):
        config, s = e['config'], e['scenario']
        require(not config['examples'], '当前原生接口不支持示例包，请使用空包。')
        from model_parameters import validate
        validate(config['model'],config['parameters'])
        from reply_phases import pipeline
        phases=pipeline(config['model'],config.get('reply_pipeline'))
        require(s['mode'] in {'passive', 'proactive_expression', 'planning'}, '当前试聊支持被动回复、主动 Skill 及内部规划。')
        require(s['mode']!='planning' or s.get('trigger_skill') in {'plan-service-day','plan-after-lesson-completion','review-service-case'}, '内部规划需要指定可触发的规划 Skill。')
        folder = Path(e['runtime_root'])
        require(folder.resolve().is_relative_to(self.root / 'runtime'), '运行根不属于 A 隔离环境。')
        from reply_lab_emojis import package, selected_parts
        emojis = package(self.root, config, folder/'assets')
        source = folder / 'source'
        subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(self.root), str(source)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(source), 'checkout', '--detach', e['code_commit']], check=True, capture_output=True)
        history = e['history'] or s['history']
        native_scenario = {'scenario_version': e['scenario_version'], 'family_uid': s['family_group'], 'as_of': s['as_of'],
                           'emoji_history':e.get('emoji_history',[]),
                           'mode': native_mode(s['mode'],s.get('trigger_skill')),
                           'fixture': s.get('native_fixture') or passive_fixture(s),
                           'initial_tasks': s.get('initial_tasks', []),
                           'history': [{'source_id': 'history-'+str(i), 'speaker': m['role'], 'body': m['content'],
                                        'created_at': m.get('at', s['as_of'])} for i, m in enumerate(history)]}
        selected=selected_passive_skill(s['mode'],s.get('trigger_skill'),config['skills'])
        if selected:native_scenario['preload_skill']=selected
        request = {'skill_source': str(self.root), 'config': {'config_version': e['config_version'], 'persona': config['persona'],
                    'model': config['model'], 'parameters':config['parameters'], 'skills': config['skills'], 'emoji_assets': emojis, 'transport_outcome': 'sent'}, 'scenario': native_scenario}
        request_path = folder / 'native-request.json'
        if 'preference_memory' in config:
            request['config']['preference_memory'] = config['preference_memory']
        if phases: request['config']['reply_pipeline']=phases
        request_path.write_text(encoded(request), encoding='utf8')
        with sqlite3.connect(folder / 'teacher.sqlite') as db:
            db.execute('CREATE TABLE frozen_versions(kind TEXT PRIMARY KEY, version TEXT, payload TEXT)')
            db.executemany('INSERT INTO frozen_versions VALUES(?,?,?)', [
                ('config', e['config_version'], encoded(config)), ('scenario', e['scenario_version'], encoded(s))])
        script = source / 'adapters/native_experiments.py'
        prep = subprocess.run([sys.executable, '-B', str(script), 'prepare', str(request_path)], capture_output=True, text=True, timeout=90)
        if prep.returncode:
            (folder / 'prepare-error.log').write_text(prep.stderr, encoding='utf8')
            raise Fault('背景装配失败，隔离目录已保留诊断记录。', 409)
        prepared = json.loads(prep.stdout)
        native_run = Path(prepared['run_root'])
        workspace = native_run / 'workspace'
        operator = json.loads(self.worker.read_text())
        token = json.loads((workspace / 'config/workbench.json').read_text())['token']
        scope = {'experiment_run_id': prepared['experiment_run_id'], 'run_id': prepared['business_run_id'], 'family_uid': s['family_group']}
        def call(endpoint, data=None):
            body = None if data is None else encoded({**scope, **data}).encode()
            req = Request('http://127.0.0.1:'+str(operator['workbench_port'])+'/experiment/v1/'+endpoint,
                          data=body, headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
            with urlopen(req, timeout=30) as response:
                value = json.load(response)
            require(value.get('status') not in {'rejected', 'failed'}, '原生操作被业务规则拒绝。', 409)
            return value
        with (folder / 'supervisor.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-B', str(script), 'serve', str(native_run), str(self.worker)], stdout=log, stderr=log)
            try:
                deadline = time.monotonic()+90
                while True:
                    require(process.poll() is None, '原生服务启动失败，请查看运行记录。', 503)
                    try:
                        snapshot = call('context')
                        break
                    except (URLError, TimeoutError):
                        require(time.monotonic()<deadline, '原生服务启动超时。', 504)
                        time.sleep(1)
                if s['mode'] == 'proactive_expression':
                    targets = [t for t in snapshot['tasks'] if t['skill'] == s['trigger_skill'] and t['status'] == 'planned']
                    require(len(targets) == 1, '此日期没有唯一可触发任务，请更换预设。', 409)
                    execution_receipt=execute_when_idle(call, targets[0]['id'])
                    (folder/'native-execute-receipt.json').write_text(encoded(execution_receipt),encoding='utf8')
                elif s['mode']=='planning' and s.get('trigger_skill') in {'plan-after-lesson-completion','review-service-case'}:
                    # Internal tasks do not appear in the workflow-task projection.
                    # Authorize the one real fixture-derived task using the same
                    # scoped manual endpoint; never turn on automatic sends.
                    with closing(sqlite3.connect((workspace/'runtime/live.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
                        targets=db.execute("SELECT id FROM service_tasks WHERE kind=? AND skill=? AND status='planned'",('case_review' if s['trigger_skill']=='review-service-case' else 'completion_plan',s['trigger_skill'])).fetchall()
                    require(len(targets)==1, '测试事实没有产生唯一的内部任务。', 409)
                    execution_receipt=execute_when_idle(call,targets[0][0])
                    (folder/'native-execute-receipt.json').write_text(encoded(execution_receipt),encoding='utf8')
                elif s['mode'] == 'passive':
                    messages = e['messages'] or [m['content'] for m in s['messages']]
                    for i, message in enumerate(messages):
                        data={'request_id':e['experiment_run_id']+'-'+str(i),'body':message}
                        if i==0 and e.get('audio'):
                            import base64
                            audio_path=folder/e['audio']['file']
                            require(audio_path.resolve().is_relative_to(folder.resolve()) and not audio_path.is_symlink(),'语音文件路径无效。',409)
                            raw=audio_path.read_bytes()
                            require(hashlib.sha256(raw).hexdigest()==e['audio']['sha256'],'语音文件哈希变更。',409)
                            data['audio']={'format':e['audio']['format'],'file_base64':base64.b64encode(raw).decode()}
                        if i==0 and e.get('image'):
                            import base64
                            image_path=folder/e['image']['file']
                            require(image_path.resolve().is_relative_to(folder.resolve()) and not image_path.is_symlink(),'图片文件路径无效。',409)
                            raw=image_path.read_bytes()
                            require(hashlib.sha256(raw).hexdigest()==e['image']['sha256'],'图片文件哈希变更。',409)
                            data['image']={'file_base64':base64.b64encode(raw).decode()}
                        call('messages',data)
                # In planning mode the existing host sync wakes its 06:30 plan.
                # No synthetic family message or business task is injected.
                deadline = time.monotonic()+420
                while True:
                    require(process.poll() is None, '原生进程意外退出。', 503)
                    require(not (workspace/'runtime/reply-phase.error.json').exists(),'表达阶段请求超出一次上限，已停止实验，未重发。',409)
                    snapshot = call('context')
                    evidence = snapshot.get('native_evidence', {})
                    handed_off = pending_handoff(snapshot, workspace/'runtime/live.sqlite')
                    if handed_off:
                        break
                    if evidence.get('completed_turns', 0) and not snapshot.get('batch') and snapshot['owner']['type'] == 'idle':
                        if s['mode']!='planning':
                            if s.get('trigger_skill') not in ('remind-reading','rebuild-reading-rhythm','close-week-reading-gap','plan-weekend-makeup','weekly-learning-review','welcome-back-to-reading') or not s07_delivery_pending(snapshot):break
                        else:
                            state=planning_result(workspace/'runtime/live.sqlite',snapshot,s.get('trigger_skill') or 'plan-service-day')
                            if state['plan_skill_loaded'] and any(t['status'] in ('done','blocked','no_contact') for t in state['tasks']):break
                    require(time.monotonic()<deadline, '本次模型运行超时，未自动重发；请查看运行记录。', 504)
                    time.sleep(2)
                (folder / 'native-context.json').write_text(encoded(snapshot), encoding='utf8')
                phase_path=workspace/'runtime/reply-phase.json'
                phase_receipt=json.loads(phase_path.read_text()) if phase_path.exists() else {}
                require(phase_receipt.get('receipt_status')!='failed','两阶段交付被原业务校验拒绝；已保留诊断，未自动重发。',409)
                require(evidence.get('persona_observed') is True, '未观察到所选 Persona 实际装入原生会话。', 409)
                manifest = json.loads((workspace / 'config/experiment.json').read_text())
                audit_path=native_run/'model-parameter-audit.jsonl'
                parameter_audit=[json.loads(line) for line in audit_path.read_text().splitlines()] if audit_path.exists() else []
                require(parameter_audit and all(row['requested']==(phases[row['phase']] if phases else config['parameters']) for row in parameter_audit), '未观察到冻结参数进入模型请求。',409)
                phase_state=json.loads((workspace/'runtime/reply-phase.json').read_text()) if (workspace/'runtime/reply-phase.json').exists() else None
                if phases:
                    require(sum(x['phase']=='compose' for x in parameter_audit)<=1,'表达阶段发生了重复模型请求。',409)
                    if not any(x['phase']=='compose' for x in parameter_audit):
                        require(any(x.get('name') in {'service_task_finish','request_human_confirmation'} and x.get('status')=='ok' for x in snapshot.get('tool_trace',[])),
                                '准备阶段结束但没有交接回复，也没有明确的不联系或人工转交回执。',409)
                require(json.loads((workspace/'config/emoji-assets.json').read_text()) == emojis, '实际表情包与配置版本不符。', 409)
                loaded, loaded_contents = {}, {}
                for skill, files in config['skills'].items():
                    actual = {p.relative_to(workspace/'skills'/skill).as_posix(): p.read_text(encoding='utf8') for p in (workspace/'skills'/skill).rglob('*.md')}
                    require(actual == files, 'Skill 实际内容与选择版本不符。', 409)
                    loaded[skill] = digest(actual)
                    loaded_contents[skill] = actual
                profile = json.loads((workspace/'runtime/dsh-home/profiles/web/cordis.patch.yml').read_text())
                persona = next(x for x in profile if x['id']=='preset-standard')['config']['plugins'][0]['config']['prefix']
                require(persona == config['persona'] and manifest['model'] == config['model'] and manifest['code_commit'] == e['code_commit'], '原生装配校验失败。', 409)
                output = []
                for part in snapshot.get('parts', []):
                    body = part.get('body')
                    try: body = json.loads(body) if isinstance(body, str) else body
                    except ValueError: pass
                    if part.get('message_type') == 7 and isinstance(body, str): output.append(body)
                    if isinstance(body, dict):
                        text = body.get('payload', {}).get('text') or body.get('text')
                        if text: output.append(text)
                if not output:
                    for delivery in snapshot.get('deliveries', []):
                        body = delivery.get('body', '')
                        try: body = json.loads(body)
                        except (ValueError, TypeError): pass
                        if isinstance(body, str) and body: output.append(body)
                        elif isinstance(body, dict):
                            text = body.get('text') or body.get('reply') or body.get('message')
                            if text: output.append(text)
                if not output:
                    results = [t.get('result') for t in snapshot.get('tasks', []) if t.get('skill') == s.get('trigger_skill')]
                    output = ['已创建待人工确认事项，尚无人工受理或解决回执；本轮未向家庭发送消息。' if handed_off else '本次未产生对外回复。'+('任务复核结果：'+encoded(results) if results else '请展开运行记录查看工具结果。')]
                internal_plan = planning_result(workspace/'runtime/live.sqlite',snapshot,s.get('trigger_skill') or 'plan-service-day') if s['mode']=='planning' else None
                if internal_plan:
                    require(internal_plan['external_message_count']==0, '内部规划产生了对外消息；隔离实验已阻止验收。', 409)
                paths = {'business_db': workspace/'runtime/live.sqlite', 'teacher_db': folder/'teacher.sqlite',
                         'config': workspace/'config', 'skills': workspace/'skills', 'sessions': workspace/'runtime/dsh-home/sessions',
                         'queue': workspace/'runtime/live.sqlite', 'locks': native_run.parent/'worker.lock', 'logs': native_run/'native.log',
                         'clock': workspace/'runtime/live.sqlite', 'cache': workspace/'runtime/cache', 'assets': folder/'assets'}
                plan_done=not internal_plan or (internal_plan['plan_skill_loaded'] and internal_plan['plan_commit_observed'] and any(t['status']=='done' for t in internal_plan['tasks']))
                return {'output': '\n\n'.join(output), 'outcome': 'human_confirmation_required' if handed_off else 'completed' if plan_done else 'not_completed', 'emojis': selected_parts(snapshot, emojis), 'simulation': True, 'session_id': snapshot['session_id'],
                        'request_id': e['experiment_run_id'], 'harness_version': manifest['harness_version'],
                        'assembly': {'config_digest': e['config_digest'], 'code_commit': manifest['code_commit'], 'runtime_root': str(folder),
                                     'component_digests': {**config['component_digests'], 'persona': digest(persona), 'skills': digest(loaded_contents)},
                                     'loaded_skills': loaded, 'model': manifest['model'], 'parameters': config['parameters'],
                                     'parameter_requests':parameter_audit,
                                     'reply_pipeline':phases,'reply_phase_state':phase_state,
                                     'writable_paths': {k: str(v) for k,v in paths.items()}, 'native_evidence': evidence,
                                     'emoji_package': {'version': emojis['version'], 'sha256': digest(emojis), 'count': len(emojis['assets'])},
                                     'inactive_components': [] if config['assets'] else ['asset_rule'],
                                     'note': '表情选择规则已冻结进 reply-style。' if config['assets'] else '未装配素材，素材选择规则不参与本次生成。'},
                        'trace': snapshot.get('tool_trace', []), 'parts': delivery_parts(snapshot),
                        'internal_plan': internal_plan,
                        'voice_inputs':__import__('voice_understanding').evidence(workspace/'runtime/live.sqlite'),
                        'image_inputs':__import__('image_inputs').evidence(workspace/'runtime/live.sqlite'),
                        'usage': evidence.get('usage')}
            finally:
                process.terminate()
                try: process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    # B's supervisor owns and reaps its child. Do not leave a
                    # new run queued onto occupied ports if cleanup fails.
                    raise Fault('原生进程尚未退出，请先检查隔离运行。', 503)
