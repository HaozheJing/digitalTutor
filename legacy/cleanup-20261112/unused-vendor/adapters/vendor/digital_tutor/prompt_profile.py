"""Candidate-owned prompt configuration via native Hermes surfaces, never vendor edits."""
import hashlib,json,os
from pathlib import Path
from .domain import require

def load(project,role):
    root=Path(project)
    p=root/'agent/config/prompt-profile.json'
    if not p.exists():return None
    config=json.loads(p.read_text(encoding='utf-8'))
    surface=os.environ.get('DIGITAL_TUTOR_TOOL_SURFACE',config.get('tool_surface','direct'))
    require(surface in {'direct','discovery'},'PROMPT_PROFILE_INVALID','工具展示模式无效')
    require(config.get('version')=='prompt-roles-v10','PROMPT_PROFILE_INVALID','提示词配置版本无效')
    name={'service':'soul-service.md','planner_daily':'soul-planner.md','planner_progress':'soul-planner.md','memory_daily':'soul-memory.md'}[role]
    soul=(root/'agent/prompts'/name).read_text(encoding='utf-8')
    from .approved_prompt import load as load_approved
    approved=load_approved(root,role)
    if approved is not None:soul=approved;name=config['service_system_file']
    guidance=(root/'agent/prompts/runtime-zh.md').read_text(encoding='utf-8') if config.get('native_prompt_language')=='zh-CN-v1' else None
    fingerprint=hashlib.sha256(json.dumps({'config':config,'role':role,'soul':soul,'tool_surface':surface,'native_guidance':guidance},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {'version':config['version'],'soul':soul,'soul_file':name,'fingerprint':fingerprint,'tool_surface':surface,
            'native_prompt_language':config.get('native_prompt_language')}

def write_native_profile(directory,profile):
    directory=Path(directory)
    (directory/'SOUL.md').write_text(profile['soul'],encoding='utf-8')
    # Quoted off is required: YAML 1.1 otherwise treats it as boolean false.
    with (directory/'config.yaml').open('a',encoding='utf-8') as f:
        f.write('agent:\n  coding_context: "off"\n  environment_probe: false\n')
        if profile.get('native_prompt_language')=='zh-CN-v1':
            f.write('  task_completion_guidance: false\n  parallel_tool_call_guidance: false\n')
