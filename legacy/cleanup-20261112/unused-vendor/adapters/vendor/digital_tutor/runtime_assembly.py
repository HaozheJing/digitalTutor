"""Pinned runtime prompts and minimal approved material snapshots for native Hermes."""
import hashlib
import json
from pathlib import Path
import shutil
from .domain import require
from .skill_loader import _frontmatter

APPROVED_SKILLS = ('explain-lesson', 'plan-proactive-service')
APPROVED_LESSON = 'LESSON-L2-FOX-D01'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preload_method_context(app, token):
    """Use the same native verified reader before generation; bodies remain unchanged."""
    from .domain import BusinessError
    family,binding=app.auth(token,{'service','planner_daily','planner_progress','memory_daily'})
    role=binding['role']
    if role=='memory_daily':return {'status':'not_applicable','skills':[]}, ''
    skill_id='explain-lesson' if role=='service' else 'plan-proactive-service'
    if not app.strict_runtime or not getattr(app,'material_approval',None) or not hasattr(app,'skills'):
        return {'status':'not_configured','skills':[]}, ''
    from .workflow import enabled
    if role=='service' and enabled(app):
        return {'status':'indexed','skills':[], 'read_route':'skills.read', 'skill_id':skill_id}, (
            '\n专业讲解前先用skills.read读取explain-lesson及必读规范；普通沟通不必读取。索引不是阅读凭据。')
    try:
        from .materials import dispatch_material
        value=dispatch_material(app,token,'skills.read',{'skill_id':skill_id})
    except BusinessError as exc:
        return {'status':'unavailable','skill_id':skill_id,'error_code':exc.code}, (
            '\n宿主未成功载入专业方法及必读规范：'+exc.code+
            '。该讲解意图仍缺方法依据；保留原未答责任，待skills.read成功再讲解。独立有据的非专业意图仍可处理。')
    sections=[value['content']]+[g['content'] for g in value['required_guides']]
    audit={'status':'loaded','skills':[{'skill_id':value['canonical_skill_id'],
        'sha256':value['sha256'],'path':str(app.skills.root/'skills'/skill_id/'SKILL.md'),
        'required_guides':[{'guide_id':g['guide_id'],'sha256':g['sha256'],
            'path':str(app.skills.root/'guides'/(g['guide_id']+'.md'))} for g in value['required_guides']]}],
        'read_route':'host_preload_via_verified_Hermes_skill_view',
        'body_context_sha256':hashlib.sha256('\n\n'.join(sections).encode()).hexdigest(),
        'changes_permissions':False}
    return audit, '\n\n宿主已实际读取以下批准的专业方法与必读规范；它们不扩大工具权限。\n\n'+'\n\n'.join(sections)


def attach_method_context(prompt, audit):
    """Update only material availability, never the trusted role/reply envelope."""
    try:
        context=json.loads(prompt)
    except (TypeError,json.JSONDecodeError):
        return prompt
    if not isinstance(context,dict):return prompt
    context['skills']={'status':audit['status'],'read_route':audit.get('read_route'),
                       'loaded_skills':audit.get('skills',[]),'error_code':audit.get('error_code')}
    context['runtime_material_context']=audit
    return json.dumps(context,ensure_ascii=False)

def _build_approval(project):
    root = Path(project)
    files = {}
    for name in APPROVED_SKILLS:
        path = root/'agent/skills'/name/'SKILL.md'
        _, _, guides = _frontmatter(path.read_text(encoding='utf-8'))
        files[str(path.relative_to(root))] = sha(path)
        for guide in guides:
            p = root/'agent/guides'/(guide.strip()+'.md')
            files[str(p.relative_to(root))] = sha(p)
    manifest_path = root/'build/openviking/courses/source-c764d8b325cc-nosplit-v2/access-map.json'
    manifest = json.loads(manifest_path.read_text())
    unit = next(u for u in manifest['units'] if u['unit_id']==APPROVED_LESSON)
    return {'chain':'R', 'skills':list(APPROVED_SKILLS), 'files':files,
            'manifest_path':str(manifest_path.relative_to(root)), 'manifest_sha256':sha(manifest_path),
            'knowledge_binding':{'revision':manifest['revision'], 'allowed_unit_ids':[APPROVED_LESSON], 'simulation':True},
            'knowledge_sha256':unit['file_sha256'], 'destination':'maas.qianwenaiapi.com'}

def material_snapshot(project, work, approved):
    root, work = Path(project), Path(work)
    allowed=set(APPROVED_SKILLS)
    if approved.get('approval_kind')=='service-actions-v8':
        require(service_action_approval(root)==approved,'MATERIAL_REVISION_CHANGED','服务动作材料与固定pin不一致')
        from .action_completion import RULES
        allowed.update(r['skill_id'] for r in RULES.values());allowed.add('close-service-case')
    require(approved['chain']=='R' and set(approved['skills'])<=allowed, 'MATERIAL_SCOPE', '材料范围未批准')
    require(sha(root/approved['manifest_path'])==approved['manifest_sha256'], 'MATERIAL_REVISION_CHANGED', '知识清单变更')
    if approved.get('approval_kind')=='teacher-agent-replay':
        require(replay_approval(root)==approved,'MATERIAL_REVISION_CHANGED','回放材料与独立批准pin不同')
    for relative, expected in approved['files'].items():
        source = (root/relative).resolve()
        require(source.is_relative_to((root/'agent').resolve()) and sha(source)==expected, 'MATERIAL_REVISION_CHANGED', '材料变更或越界')
        target = work/'approved-agent'/source.relative_to(root/'agent')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return work/'approved-agent'

def initial_service_context(project,app,token):
    """Refresh arrival scope, then let assembly own its snapshot transaction."""
    from .steering import refresh_binding
    family,binding=app.auth(token,{'service'})
    if binding.get('steerable'):refresh_binding(app,token)
    family,binding=app.auth(token,{'service'})
    job=app.store.get('job',binding['job_id'],family) if binding.get('job_id') else {}
    context,_=assemble(project,app,token,job)
    return family,binding,context


def assemble(project, app, token, job):
    from .context import build_context, _role
    family, binding, role = _role(app, token)
    prompts = [Path(project)/'agent/prompts'/name for name in ('common.md',role+'.md')]
    from .approved_prompt import load as load_approved
    if load_approved(project,role) is not None:
        prompts=[Path(project)/'agent/prompts/service-approved.md']
    context = build_context(app, token, job)
    context['run_contract'] = {k:binding.get(k) for k in
        ('run_id','job_id','job_kind','mode','reply_scope','output_mode','recovery_only','report_id','revision',
         'planning_reason','pending_id','decision_revision','plan_revision','event_id',
         'current_step_key','bound_step_key','bound_event_version','commitment_id','schedule_revision','learning_evidence_id','learning_evidence_version',
         'correction_evidence_id','correction_evidence_version','report_review_key','report_review_id',
         'report_review_revision','report_review_event_ids','report_review_fingerprint','report_review_evidence_refs',
         'commitment_review_id','commitment_review_revision','commitment_review_step_key','commitment_review_evidence_ref')}
    context['run_contract'].update(attempt=job.get('attempt'), output_route='internal',
        prompt_sha256={p.name:sha(p) for p in prompts})
    from .hermes_tools import redacted
    return redacted(context), '\n\n'.join(p.read_text(encoding='utf-8') for p in prompts)

def approval(project):
    root=Path(project)
    frozen=json.loads((root/'agent/runtime-material-approval.json').read_text())
    require(_build_approval(root)==frozen,'MATERIAL_REVISION_CHANGED','材料与批准固定版本不符')
    return frozen


def build_replay_approval(project, unit_ids, available_from='2025-10-14T00:00:00+08:00', *, reference_paths=()):
    """Host candidate pin, separate from the preserved M5 D01 approval."""
    root=Path(project)
    value=_build_approval(root)
    manifest=json.loads((root/value['manifest_path']).read_text(encoding='utf-8'))
    units={u['unit_id']:u for u in manifest['units']}
    require(unit_ids and set(unit_ids)<=set(units),'MATERIAL_SCOPE','回放课节不在课程清单中')
    value.pop('knowledge_sha256')
    value.update(approval_kind='teacher-agent-replay',
        knowledge_sha256_by_unit={uid:units[uid]['file_sha256'] for uid in sorted(unit_ids)},
        historical_availability='reconstructed_replay_scope_not_verified_cohort_mapping')
    value['knowledge_binding'].update(allowed_unit_ids=sorted(unit_ids),
        available_at={uid:available_from for uid in sorted(unit_ids)})
    if reference_paths:
        revisions={uid:manifest['revision'] for uid in unit_ids};pins=[]
        for relative in reference_paths:
            path=(root/relative).resolve()
            require(path.is_relative_to((root/'build/openviking').resolve()),'MATERIAL_SCOPE','参考清单越界')
            extra=json.loads(path.read_text(encoding='utf-8'))
            require(extra.get('usage_status')=='technical_pilot_business_unverified'
                    or (extra.get('production_enabled') is False and extra.get('approved_business_answers')==0),
                    'MATERIAL_SCOPE','仅绑定明确标识为试点参考的产品/FAQ资料')
            ids=[u['unit_id'] for u in extra['units']]
            require(len(ids)==len(set(ids)) and not set(ids)&set(revisions),'MATERIAL_SCOPE','跨清单资料编号冲突')
            revisions.update({uid:extra['revision'] for uid in ids})
            value['knowledge_sha256_by_unit'].update({u['unit_id']:u['file_sha256'] for u in extra['units']})
            pins.append({'path':Path(relative).as_posix(),'sha256':sha(path)})
        value['reference_manifests']=pins
        value['knowledge_binding'].update(allowed_unit_ids=sorted(revisions),unit_revisions=revisions,
            available_at={uid:available_from for uid in revisions},allow_reference=False)
        value['reference_availability']='simulation_only_not_verified_historical_publication'
    return value


def replay_approval(project):
    root=Path(project)
    frozen=json.loads((root/'agent/replay-material-approval.json').read_text(encoding='utf-8'))
    dates=set(frozen['knowledge_binding']['available_at'].values())
    require(len(dates)==1,'MATERIAL_SCOPE','回放pin需同一明确起始范围')
    revisions=frozen['knowledge_binding'].get('unit_revisions')
    course_ids=[uid for uid in frozen['knowledge_binding']['allowed_unit_ids']
                if revisions is None or revisions[uid]==frozen['knowledge_binding']['revision']]
    require(build_replay_approval(root,course_ids,next(iter(dates)),
                reference_paths=[p['path'] for p in frozen.get('reference_manifests',[])])==frozen,
            'MATERIAL_REVISION_CHANGED','回放正文/规范/清单与pin不符')
    return frozen


def build_service_action_approval(project):
    """Separate opt-in pin: 28 action skills + original explain/planning skills."""
    from .action_completion import RULES
    root=Path(project);value=replay_approval(root)
    value={**value,'approval_kind':'service-actions-v8','skills':sorted(set(value['skills'])|{r['skill_id'] for r in RULES.values()}|{'close-service-case'}),'files':dict(value['files'])}
    for name in value['skills']:
        p=root/'agent/skills'/name/'SKILL.md'
        _,_,guides=_frontmatter(p.read_text(encoding='utf-8'));value['files'][str(p.relative_to(root))]=sha(p)
        for guide in guides:
            p=root/'agent/guides'/(guide.strip()+'.md');value['files'][str(p.relative_to(root))]=sha(p)
    p=root/'agent/config/action-completion-rules.json';value['files'][str(p.relative_to(root))]=sha(p)
    p=root/'agent/config/service.yaml';value['files'][str(p.relative_to(root))]=sha(p)
    return value


def service_action_approval(project):
    root=Path(project);frozen=json.loads((root/'agent/action-material-approval.json').read_text(encoding='utf-8'))
    require(build_service_action_approval(root)==frozen,'MATERIAL_REVISION_CHANGED','服务动作skill/规范发生变化')
    return frozen
