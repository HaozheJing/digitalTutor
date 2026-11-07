"""Trusted-host API for one isolated native experiment worker.

Called by the authenticated workbench backend, never exposed as arbitrary paths,
YAML, plugins or commands to teachers. Immutable revisions create new runs.
"""
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
from contextlib import closing
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def digest(data): return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf8')
    path.chmod(0o600)


def safe_relative(name):
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or not p.parts:
        raise ValueError('invalid_relative_path')
    return p


def model_output_limit(model, requested=None):
    value=requested if requested is not None else (32768 if model=='deepseek-v4-pro' else 8192)
    if type(value) is not int or not 1024<=value<=65536:raise ValueError('invalid_max_output_tokens')
    return value


def unique_seed_contract(catalog, seed):
    """Accept duplicate catalogue views only when they expose the same opportunity."""
    matches=[(i,x) for i,x in enumerate(catalog) if x['skill']==seed['skill']]
    if not matches: raise ValueError('ambiguous_seed_skill')
    matched=[(i,x,[o for o in x['opportunities'] if not seed.get('opportunity_id') or o['id']==seed['opportunity_id']]) for i,x in matches]
    matched=[row for row in matched if row[2]]
    if not matched or any(len(row[2])!=1 for row in matched): raise ValueError('ambiguous_seed_opportunity')
    if len(matched)>1 and any(row[2][0]!=matched[0][2][0] for row in matched[1:]):
        raise ValueError('ambiguous_seed_skill')
    return matched[0]


def prepare(config, scenario, *, source=ROOT, home=None, skill_source=None):
    """Freeze code from a clean Git revision and caller-supplied scenario facts.

    No production database is copied. History is SQL reconstruction, not native
    cloning. Fact fixtures retain their provenance and time filtering.
    """
    source = Path(source).resolve()
    unknown = set(config) - {'config_version','parent_version','persona','model','max_output_tokens','reasoning_effort','skills','emoji_assets','transport_outcome','preference_memory','parameters','reply_pipeline','highlight_events'}
    if unknown: raise ValueError('unsupported_experiment_config_fields:' + ','.join(sorted(unknown)))
    output_limit=model_output_limit(config.get('model'),config.get('max_output_tokens'))
    if config.get('reasoning_effort') not in (None,'low','high','max') or (config.get('reasoning_effort') and config.get('model')!='deepseek-v4-pro'):
        raise ValueError('unsupported_reasoning_effort')
    from model_parameters import validate
    validate(config.get('model'),config.get('parameters',{}))
    from reply_phases import pipeline
    pipeline(config.get('model'),config.get('reply_pipeline'))
    home = Path(home or source / 'runtime/experiments').resolve()
    if not home.is_relative_to(source / 'runtime'):
        raise ValueError('experiment_home_outside_worker')
    if subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise ValueError('commit_code_before_experiment')
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if not isinstance(config.get('persona'), str) or not config['persona'].strip():
        raise ValueError('persona_required')
    if scenario.get('mode') not in ('passive', 'planning', 'execution', 'completion', 'completion_action', 'case_review'):
        raise ValueError('invalid_experiment_mode')
    fixture = scenario['fixture']
    if fixture['uid'] != scenario['family_uid']:
        raise ValueError('fixture_family_mismatch')
    stamp = dt.datetime.fromisoformat(scenario['as_of'])
    if stamp.tzinfo is None: raise ValueError('scenario_timezone_required')
    for row in scenario.get('history', []):
        at = dt.datetime.fromisoformat(row['created_at'])
        if at.tzinfo is None or at > stamp or row['speaker'] not in ('user', 'assistant'):
            raise ValueError('future_or_invalid_history')
    ident = 'exp-' + uuid.uuid4().hex
    run = home / ident
    workspace = run / 'workspace'
    workspace.mkdir(parents=True)
    archive = subprocess.check_output(['git', '-C', str(source), 'archive', commit])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            safe_relative(member.name)
            if not (member.isdir() or member.isfile()): raise ValueError('code_symlinks_not_supported')
        tar.extractall(workspace, filter='data')
    # Runtime bindings belong to the reviewed code revision, not teacher prose.
    runtime_contracts={str(p.relative_to(workspace)):p.read_bytes() for p in (workspace/'skills').glob('*/runtime-capabilities.json')}
    # Freeze the complete effective Skill tree, including nested dependencies.
    origin = Path(skill_source or source).resolve()
    active_path = origin / 'runtime/skill-studio/active.json'
    active = json.loads(active_path.read_text()) if active_path.exists() else {}
    for name, entry in active.items():
        if not re.fullmatch(r'[a-z0-9-]+', name) or not re.fullmatch(r'[a-f0-9]{32}', entry['version']):
            raise ValueError('invalid_published_skill')
        bundle = origin / 'runtime/skill-studio/bundles' / entry['version'] / name
        for file in bundle.rglob('*'):
            if file.is_symlink(): raise ValueError('skill_symlink_forbidden')
        # Export exact current bundle, removing obsolete tracked references only
        # in the newly created run workspace.
        target = workspace / 'skills' / name
        if target.exists(): shutil.rmtree(target)
        shutil.copytree(bundle, target)
    for name, files in config.get('skills', {}).items():
        if not re.fullmatch(r'[a-z0-9-]+', name) or not (workspace / 'skills' / name).is_dir():
            raise ValueError('unknown_skill_override')
        # An override is a complete frozen Markdown set, not a patch.
        if 'SKILL.md' not in files:raise ValueError('skill_body_required')
        for old in (workspace / 'skills' / name).rglob('*.md'):old.unlink()
        for relative, body in files.items():
            p = safe_relative(relative)
            if p.suffix != '.md' or not isinstance(body, str): raise ValueError('markdown_skill_override_required')
            target = workspace / 'skills' / name / p
            target.parent.mkdir(parents=True, exist_ok=True);target.write_text(body, encoding='utf8')
    for relative,body in runtime_contracts.items():
        target=workspace/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
    write_json(workspace / 'config/message-hooks.json', {'enabled': True})
    write_json(workspace / 'config/tool-surface.json', {'version': 2, 'identity': True, 'schedule': True, 'knowledge': True, 'context': True, 'delivery': True, 'retire_legacy': True, 'memory_scope': True, 'host_steps': True, 'capabilities_read': True, 'capabilities_delivery': True, 'skill_scopes': True})
    write_json(workspace / 'config/service-workflow.json', {'enabled': True})
    write_json(workspace / 'config/completion-planning.json', {'enabled': True})
    write_json(workspace / 'config/workbench.json', {'token': secrets.token_urlsafe(32)})
    write_json(workspace / 'config/emoji-assets.json', config.get('emoji_assets', {'version': 'empty', 'assets': []}))
    if config.get('highlight_events'):
        ingress=config['highlight_events']
        if not isinstance(ingress,dict) or set(ingress)!={'source','source_mode','allowed_image_hosts'} or ingress['source_mode']!='external' or not isinstance(ingress['allowed_image_hosts'],list):raise ValueError('invalid_experiment_highlight_ingress')
        write_json(workspace/'config/highlight-events.json',{**ingress,'enabled':True,'token':secrets.token_urlsafe(36)})
    memory = config.get('preference_memory', {})
    if not isinstance(memory,dict) or set(memory)-{'enabled','slow_enabled'} or any(type(v) is not bool for v in memory.values()):
        raise ValueError('invalid_preference_memory_configuration')
    write_json(workspace / 'config/preference-memory.json', {**memory,'family_uid':scenario['family_uid'],'contact_id':'bound-contact'})
    history=scenario.get('emoji_history',[])
    if not isinstance(history,list) or any(not isinstance(x,dict) or type(x.get('has_emoji')) is not bool for x in history):raise ValueError('invalid_emoji_history')
    write_json(workspace / 'config/emoji-history.json',history)
    write_json(workspace / 'runtime/fixtures/mock_uid_00001_first_month.json', fixture)
    if scenario.get('course_schedule'):
        write_json(workspace / 'adapters/course_schedule_config.json', scenario['course_schedule'])
    write_json(workspace / 'config/frozen-experiment-input.json', {'config': config, 'scenario': scenario})
    # Run initialization in a fresh process so module-level roots cannot leak
    # from the workbench process into this run.
    init = '''import sqlite3,json,hashlib
from contextlib import closing
from pathlib import Path
import sql_tools as s,business,wechat_test,test_mode as tm,agent_plan as ap,service_workflow as wf,message_hooks as hooks
from native_experiments import unique_seed_contract
data=json.loads((s.R/'config/frozen-experiment-input.json').read_text())['scenario']
with closing(sqlite3.connect(s.DB)) as c,c:c.executescript((s.R/'adapters/schema.sql').read_text())
business.initialize();wechat_test.initialize();ap.initialize();wf.initialize()
state=tm.activate(data['as_of'],data['mode']=='planning');state['uid']=data['family_uid'];state['agent_planning']=True
with closing(sqlite3.connect(s.DB)) as c,c:
 c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
 hooks.tables(c)
 for m in data.get('history',[]):c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)',(m['source_id'],m['speaker'],m['body'],m['created_at'],'experiment_history_reconstruction',hashlib.sha256(m['body'].encode()).hexdigest()))
if data['mode'] in ('completion','completion_action'):
 # The observed fixture completion creates the real completion-plan task.
 # Suppress only the unrelated automatic daily plan in this test workspace.
 ap.sync()
 with closing(sqlite3.connect(s.DB)) as c,c:
  c.execute("UPDATE service_tasks SET status='done',result=? WHERE kind='plan' AND run_id=? AND status='planned'",(s.encoded({'source':'completion_test_fixture','not_model_planning':True}),state['run_id']))
if data['mode']=='completion_action':
 # Explicit synthetic completion action: use the actual planner and all of its
 # eligibility, timing, ledger and audit checks; never invent a candidate.
 import completion_planner as cp,observability as audit
 if len(data.get('initial_tasks',[]))!=1:raise ValueError('completion_action_requires_one_explicit_seed')
 seed=data['initial_tasks'][0]
 if seed['skill']=='welcome-back-to-reading':
  with closing(sqlite3.connect(s.DB)) as c,c:
   ids=c.execute("SELECT id,due_at FROM service_tasks WHERE skill=? AND run_id=? AND status='planned'",(seed['skill'],state['run_id'])).fetchall()
   if len(ids)!=1 or ids[0][1]!=seed['run_at']:raise ValueError('S11_requires_actual_host_completion_task')
   c.execute("UPDATE service_tasks SET status='done',result=? WHERE kind='completion_plan' AND run_id=? AND status='planned'",(s.encoded({'source':'S11_expression_fixture','not_model_planning':True}),state['run_id']))
 else:
  codes=[code for code,name in cp.RULES.items() if name==seed['skill']]
  if len(codes)!=1:raise ValueError('unknown_completion_action')
  with closing(sqlite3.connect(s.DB)) as c,c:
   ids=c.execute("SELECT id FROM service_tasks WHERE kind='completion_plan' AND run_id=? AND status='planned'",(state['run_id'],)).fetchall()
   if len(ids)!=1:raise ValueError('completion_plan_task_missing_or_ambiguous')
   current=json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
   current['manual_service_task_id']=ids[0][0]
   c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(current),))
  task=ap.claim()
  if not task or task['kind']!='completion_plan':raise ValueError('completion_plan_task_missing')
  audit.invoke('service_skill',{'name':task['skill']},lambda:ap.load_skill(task['skill']),origin='host')
  context=audit.invoke('completion_plan_context',{},cp.context,origin='host')
  options=[x for x in context['candidates'] if x['skill']==seed['skill'] and x['status']=='eligible']
  if len(options)!=1:raise ValueError('completion_action_requires_one_eligible_candidate')
  event_id=context['event']['event_id']
  choices=[{'code':code,'decision':'schedule' if code==codes[0] else 'skip',
            'reason':'explicit isolated completion-action experiment fixture', 'evidence':[event_id],
            **({'run_at':seed['run_at']} if code==codes[0] else {})} for code in cp.RULES]
  args={'request_id':'experiment-completion-seed','context_token':context['context_token'],'decisions':choices}
  audit.invoke('completion_plan_commit',args,lambda:cp.commit(**args),origin='host')
  cp.finish_internal(task,'completed','isolated fixture created one actual eligible action')
if data['mode'] in ('execution','case_review') or (data['mode']=='passive' and data.get('initial_tasks')):
 if not data.get('initial_tasks'):raise ValueError('execution_scenario_requires_explicit_initial_tasks')
 task=ap.claim();context=wf.context()
 choices=[{'skill':x['skill'],'decision':'skip','reason':'explicit expression-test fixture: not selected'} for x in context['catalog']]
 for seed in data['initial_tasks']:
  i,contract,opportunities=unique_seed_contract(context['catalog'],seed)
  choices[i]={'skill':seed['skill'],'decision':'create','reason':'explicit expression-test task fixture; not model planning','opportunity_id':opportunities[0]['id'],'run_at':seed['run_at']}
 wf.commit('experiment-fixture-seed',context['context_token'],choices)
 with closing(sqlite3.connect(s.DB)) as c,c:
  c.execute("UPDATE service_tasks SET status='done',result=? WHERE id=?",(s.encoded({'source':'experiment_task_fixture','not_model_planning':True}),task['id']))
  c.execute("DELETE FROM metadata WHERE key='active_service_task'")
  for day,body in c.execute('SELECT business_date,body FROM daily_plans').fetchall():
   plan=json.loads(body);plan['source']='experiment_task_fixture';c.execute('UPDATE daily_plans SET body=? WHERE business_date=?',(s.encoded(plan),day))
  for ident,body in c.execute('SELECT id,body FROM service_plan_versions').fetchall():
   plan=json.loads(body);plan['source']='experiment_task_fixture';c.execute('UPDATE service_plan_versions SET body=? WHERE id=?',(s.encoded(plan),ident))
if data['mode']=='case_review':
 # Construct an actual isolated matter via workflow.commit above, then create
 # one explicitly requested internal review; no automatic review is enabled.
 import completion_planner
 with closing(sqlite3.connect(s.DB)) as c,c:
  cases=c.execute("SELECT v.case_id FROM service_task_versions v JOIN service_tasks t ON t.id=v.task_id WHERE t.run_id=? AND t.status='planned'",(state['run_id'],)).fetchall()
  if len(cases)!=1:raise ValueError('case_review_requires_one_fixture_case')
  completion_planner.insert(c,state,'experiment-manual-case-review','case_review','review-service-case',state['as_of'],state['as_of'][:10]+'T23:59:59+08:00',cases[0][0],'explicit isolated manual review test')
print(json.dumps(state))
'''
    result = subprocess.check_output([sys.executable, '-B', '-c', init], cwd=workspace / 'adapters', text=True)
    state = json.loads(result)
    frozen = {}
    for folder in ['adapters', 'app', 'skills', 'knowledge', 'assets', 'config', 'runtime/fixtures']:
        for p in (workspace / folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:
                frozen[str(p.relative_to(workspace))] = digest(p.read_bytes())
    value = {'schema_version': 1, 'experiment_run_id': ident, 'business_run_id': state['run_id'],
             'family_uid': state['uid'], 'workspace': str(workspace), 'simulation': True,
             'config_version': config['config_version'], 'scenario_version': scenario['scenario_version'], 'mode': scenario['mode'],
             'code_commit': commit, 'harness_version': '0.2.1-rc.2', 'model': config['model'], 'max_output_tokens':output_limit,'reasoning_effort':config.get('reasoning_effort'),
             'components': {'skills': digest(json.dumps({k: v for k, v in frozen.items() if k.startswith('skills/')}, sort_keys=True).encode()),
                            'persona': digest(config['persona'].encode()), 'scenario': digest(json.dumps(scenario, sort_keys=True).encode())},
             'history_mode': 'sql_reconstruction_no_historical_dispatch',
             'transport_outcome': config.get('transport_outcome', 'sent'), 'frozen_files': frozen}
    value['components']['skill_versions'] = {
        name: {'source_version': active.get(name, {}).get('version', commit),
               'override_version': config['config_version'] if name in config.get('skills', {}) else None,
               'files': {k: v for k, v in frozen.items() if k.startswith('skills/' + name + '/')}}
        for name in sorted(p.name for p in (workspace / 'skills').iterdir() if p.is_dir())}
    value['components']['emoji_package'] = {'version': config.get('emoji_assets', {}).get('version', 'empty'), 'sha256': frozen['config/emoji-assets.json']}
    if value['transport_outcome'] not in ('sent', 'accepted', 'unknown', 'failed'): raise ValueError('invalid_simulation_outcome')
    write_json(workspace / 'config/experiment.json', value)
    write_json(run / 'record.json', {'status': 'prepared', 'manifest_sha256': digest((workspace / 'config/experiment.json').read_bytes()), 'manifest': value})
    return {'status': 'prepared', 'run_root': str(run), **{k: value[k] for k in ['experiment_run_id', 'business_run_id', 'family_uid', 'code_commit']}}


def profile(workspace, persona, model, base_url, max_output_tokens=None, reasoning_effort=None):
    return [
        {'id': x, 'disabled': True} for x in ['session-log-deepseek','plugin-package-inventory-deepseek','session-telemetry-otel','session-title-llm']
    ] + [
        {'id': 'agent-default-model', 'config': {'provider': 'experiment-provider', 'model': model,**({'reasoningEffort':reasoning_effort} if reasoning_effort else {})}},
        {'id': 'llm-pi-ai', 'config': {'providers': {'experiment-provider': {'api': 'openai-completions', 'baseURL': base_url,
            'apiKeyEnv': 'TUTOR_API_KEY', 'models': [{'id': model, 'contextWindow': 131072, 'maxTokens': model_output_limit(model,max_output_tokens),
                **({'reasoningEfforts':{'low':'low','high':'high','max':'max'}} if reasoning_effort else {})}],
            'compat': {'supportsDeveloperRole': False,**({'thinkingFormat':'deepseek'} if reasoning_effort else {})}}}}},
        {'id': 'preset-standard', 'config': {'id': 'standard', 'order': 1, 'plugins': [
            {'id': 'persona', 'name': '@deepseek-ai/dsh-persona', 'config': {'prefix': persona, 'complete': False}},
            {'id': 'time-context', 'name': '@deepseek-ai/dsh-time-context'},
            {'id': 'skill-filesystem', 'name': '@deepseek-ai/dsh-skill-filesystem', 'config': {'includeDefaultRoots': False, 'customSkillDirs': [str(workspace / 'skills')]}},
            {'id': 'tool-skill', 'name': '@deepseek-ai/dsh-tool-skill'},
            {'id': 'tool-schedule', 'name': '@deepseek-ai/dsh-tool-schedule'},
            {'id': 'tutor-mcp', 'name': '@deepseek-ai/dsh-mcp-client', 'config': {'transport': 'stdio', 'serverName': 'tutor', 'command': 'python3', 'args': [str(workspace / 'adapters/experiment_result_mcp.py')], 'cwd': str(workspace / 'adapters'), 'failOnStartupError': True, 'toolCallTimeoutMs': 150000}},
            {'id': 'compaction', 'name': 'cordis:group', 'group': True, 'isolate': {'compaction': True}, 'config': [
                {'id': 'compaction-basic', 'name': '@deepseek-ai/dsh-compaction-basic', 'config': {'headroomTokens': 65536, 'maxTokens': 8192}},
                {'id': 'command-compact', 'name': '@deepseek-ai/dsh-command-compact'}]}]}},
        {'insert': [{'id': 'tutor-lab', 'name': str(workspace / 'app/tutor-plugin.mjs')}]}]


def bind_knowledge(workspace, operator):
    """Private runtime credential, outside the immutable public artifact set."""
    if not operator.get('knowledge_ovcli_config'):return None
    credential=Path(operator['knowledge_ovcli_config']).resolve()
    if not credential.is_file():raise ValueError('knowledge_openviking_config_missing')
    target=Path(workspace)/'runtime/knowledge-ovcli.conf'
    shutil.copy2(credential,target);target.chmod(0o600)
    return target


def serve(run_root, worker_config):
    """Foreground supervisor holds the worker flock until the child is reaped."""
    import fcntl
    run = Path(run_root).resolve(); workspace = run / 'workspace'
    if run.parent != ROOT / 'runtime/experiments': raise ValueError('foreign_worker_run')
    record = json.loads((run / 'record.json').read_text())
    if digest((workspace / 'config/experiment.json').read_bytes()) != record['manifest_sha256']:
        raise ValueError('experiment_manifest_changed')
    value = record['manifest']
    lock = open(run.parent / 'worker.lock', 'a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    operator = json.loads(Path(worker_config).read_text())
    if operator['native_port']==operator['workbench_port']:raise ValueError('distinct_ports_required')
    for port in [operator['native_port'], operator['workbench_port']]:
        if type(port) is not int or port < 1024 or port > 65535: raise ValueError('invalid_port')
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            sock.bind(('127.0.0.1', port))
    node = Path(operator['node']).resolve(); dependencies = Path(operator['node_modules']).resolve()
    dependency_link=workspace/'runtime/dependencies/node_modules'
    dependency_link.parent.mkdir(parents=True,exist_ok=True)
    if dependency_link.exists():
        if dependency_link.resolve()!=dependencies:raise ValueError('experiment_dependencies_changed')
    else:dependency_link.symlink_to(dependencies,target_is_directory=True)
    version = json.loads((dependencies / '@deepseek-ai/dsh/package.json').read_text())['version']
    if version != value['harness_version']: raise ValueError('harness_version_mismatch')
    if value['model'] not in operator['allowed_models']: raise ValueError('model_not_allowed')
    config = json.loads((workspace / 'config/frozen-experiment-input.json').read_text())['config']
    knowledge_credential=bind_knowledge(workspace,operator)
    media_binding=__import__('experiment_media').bind(workspace,operator)
    voice_binding=__import__('voice_understanding').bind(workspace,operator)
    if record.get('voice_binding') and record['voice_binding']!=voice_binding:raise ValueError('voice_capability_changed_on_restart')
    if voice_binding:record['voice_binding']=voice_binding
    if record.get('media_binding') and record['media_binding']!=media_binding:raise ValueError('media_capability_changed_on_restart')
    if media_binding:record['media_binding']=media_binding
    if config.get('emoji_assets',{}).get('retrieval'):
        credential=Path(operator['emoji_ovcli_config']).resolve()
        if not credential.is_file():raise ValueError('emoji_openviking_config_missing')
        shutil.copy2(credential,workspace/'runtime/emoji-ovcli.conf')
        (workspace/'runtime/emoji-ovcli.conf').chmod(0o600)
    home = workspace / 'runtime/dsh-home'
    pr = home / 'profiles/web';pr.mkdir(parents=True, exist_ok=True)
    write_json(pr / 'package.json', {'name': 'dsh-profile-web', 'private': True, 'dependencies': {}, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}})
    (pr / 'cordis.yml').write_text('[]', encoding='utf8')
    # JSON is valid YAML; only fixed server-owned plugins are composed here.
    from experiment_parameter_proxy import ParameterProxy
    proxy=ParameterProxy(operator['base_url'],operator['api_key'],value['model'],config.get('parameters',{}),run/'model-parameter-audit.jsonl',record.get('parameter_proxy_port',0),reply_pipeline=config.get('reply_pipeline'),phase_state=workspace/'runtime/reply-phase.json',image_workspace=workspace)
    record['parameter_proxy_port']=proxy.server.server_port
    assembled = profile(workspace, config['persona'], value['model'], proxy.url,config.get('max_output_tokens'),config.get('reasoning_effort'))
    if config.get('reply_pipeline'):
        preset=next(x for x in assembled if x.get('id')=='preset-standard')
        mcp=next(x for x in preset['config']['plugins'] if x['id']=='tutor-mcp')
        mcp['config']['args']=[str(workspace/'adapters/experiment_phase_mcp.py')]
        assembled.append({'insert':[{'id':'experiment-reply-phase','name':str(workspace/'app/experiment-reply-phase.mjs')}]})
    memory_proxy=None
    if config.get('preference_memory',{}).get('enabled'):
        if 'deepseek-v4-pro' not in operator['allowed_models']:raise ValueError('memory_model_not_allowed')
        memory_proxy=ParameterProxy(operator['base_url'],operator['api_key'],'deepseek-v4-pro',{},run/'memory-model-parameter-audit.jsonl',record.get('memory_parameter_proxy_port',0))
        record['memory_parameter_proxy_port']=memory_proxy.server.server_port
        provider=next(x for x in assembled if x.get('id')=='llm-pi-ai')['config']['providers']
        provider['experiment-memory-provider']={'api':'openai-completions','baseURL':memory_proxy.url,'apiKeyEnv':'TUTOR_MEMORY_API_KEY','models':[{'id':'deepseek-v4-pro','contextWindow':131072,'maxTokens':32768,'reasoningEfforts':{'low':'low','high':'high','max':'max'}}],'compat':{'supportsDeveloperRole':False,'thinkingFormat':'deepseek'}}
    proposed = digest(json.dumps(assembled, ensure_ascii=False, sort_keys=True).encode())
    if record.get('profile_sha256') and record['profile_sha256'] != proposed:
        raise ValueError('experiment_profile_changed_on_restart')
    write_json(pr / 'cordis.patch.yml', assembled)
    if not (pr / 'node_modules').exists(): (pr / 'node_modules').symlink_to(dependencies, target_is_directory=True)
    for name in ['home', 'tmp', 'cache', 'agents-home']:(workspace / 'runtime' / name).mkdir(exist_ok=True)
    env = {'PATH': str(node.parent) + ':/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
           'HOME': str(workspace / 'runtime/home'), 'TMPDIR': str(workspace / 'runtime/tmp'), 'XDG_CACHE_HOME': str(workspace / 'runtime/cache'),
           'DSH_HOME': str(home), 'DSH_AGENTS_HOME': str(workspace / 'runtime/agents-home'), 'DSH_PERMISSION_MODE': 'workspace-write',
           'TUTOR_API_KEY': proxy.token, 'TUTOR_EXPERIMENT_ID': value['experiment_run_id'],
           'TUTOR_EXPERIMENT_MANIFEST_SHA256': record['manifest_sha256'],
           'TUTOR_EXPERIMENT_PROFILE_SHA256': proposed,
           'TUTOR_WORKBENCH_PORT': str(operator['workbench_port'])}
    if media_binding:env['TUTOR_MEDIA_BINDING_SHA256']=media_binding['sha256']
    if voice_binding:env['TUTOR_VOICE_BINDING_SHA256']=voice_binding['sha256']
    if memory_proxy:env['TUTOR_MEMORY_API_KEY']=memory_proxy.token
    if knowledge_credential:env['TUTOR_KNOWLEDGE_CONFIG_FILE']=str(knowledge_credential)
    if config.get('emoji_assets',{}).get('retrieval') or operator.get('knowledge_ovcli_config'):
        # The CLI requires a local display-language choice on a fresh HOME.
        # Initialize this isolated home; never inherit the operator's home.
        from knowledge_search import CLI
        subprocess.run([CLI,'language','en'],env=env,capture_output=True,check=True,timeout=15)
    # Verify every frozen artifact before spawning any model or poller.
    subprocess.run([sys.executable, '-B', str(workspace / 'adapters/experiment_runtime.py'), 'verify'], cwd=workspace, env=env, stdout=subprocess.DEVNULL, check=True)
    record.update(status='running', supervisor_pid=os.getpid(), ports=[operator['native_port'], operator['workbench_port']], profile_sha256=digest((pr / 'cordis.patch.yml').read_bytes()))
    write_json(run / 'record.json', record)
    with open(run / 'native.log', 'ab') as log:
        proxy.start()
        if memory_proxy:memory_proxy.start()
        child = subprocess.Popen([str(node), str(dependencies / '@deepseek-ai/dsh/lib/bin.js'), 'web', '--port', str(operator['native_port']), '--no-open'], cwd=workspace, env=env, stdout=log, stderr=log, start_new_session=True, pass_fds=(lock.fileno(),))
        def stop(*_):
            try: os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        signal.signal(signal.SIGTERM, stop);signal.signal(signal.SIGINT, stop)
        record['native_pid'] = child.pid;write_json(run / 'record.json', record)
        try: code = child.wait()
        finally:
            stop()
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: os.killpg(child.pid, signal.SIGKILL);child.wait()
            record.update(status='stopped', exit_code=child.returncode);write_json(run / 'record.json', record)
            proxy.close()
            if memory_proxy:memory_proxy.close()
    return code


def status(run_root):
    run = Path(run_root).resolve()
    if run.parent != ROOT / 'runtime/experiments': raise ValueError('foreign_worker_run')
    record = json.loads((run / 'record.json').read_text())
    result = {k: record.get(k) for k in ['status','supervisor_pid','native_pid','ports','exit_code','profile_sha256','manifest_sha256']}
    result['experiment_run_id'] = record['manifest']['experiment_run_id']
    session = run / 'workspace/runtime/service-session.json'
    result['native_session'] = json.loads(session.read_text()) if session.exists() else None
    result['simulation'] = True
    return result


def stop(run_root):
    run = Path(run_root).resolve();record = status(run)
    pid = record.get('supervisor_pid')
    command = Path('/proc') / str(pid) / 'cmdline'
    if not command.exists(): return {**record, 'stop_requested': False, 'process_present': False}
    argv = command.read_bytes().split(b'\0')
    if str(run).encode() not in argv or not any(x.endswith(b'native_experiments.py') for x in argv):
        raise ValueError('supervisor_identity_changed')
    os.kill(pid, signal.SIGTERM)
    return {**record, 'stop_requested': True}


if __name__ == '__main__':
    if sys.argv[1] == 'serve': sys.exit(serve(sys.argv[2], sys.argv[3]))
    if sys.argv[1] in ('status','stop'): print(json.dumps(globals()[sys.argv[1]](sys.argv[2])))
    if sys.argv[1] == 'prepare':
        request = json.loads(Path(sys.argv[2]).read_text())
        print(json.dumps(prepare(request['config'], request['scenario'], skill_source=request.get('skill_source'))))
