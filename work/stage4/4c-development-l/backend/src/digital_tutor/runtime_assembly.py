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
    require(approved['chain']=='R' and set(approved['skills'])<=set(APPROVED_SKILLS), 'MATERIAL_SCOPE', '材料范围未批准')
    require(sha(root/approved['manifest_path'])==approved['manifest_sha256'], 'MATERIAL_REVISION_CHANGED', '知识清单变更')
    for relative, expected in approved['files'].items():
        source = (root/relative).resolve()
        require(source.is_relative_to((root/'agent').resolve()) and sha(source)==expected, 'MATERIAL_REVISION_CHANGED', '材料变更或越界')
        target = work/'approved-agent'/source.relative_to(root/'agent')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return work/'approved-agent'

def assemble(project, app, token, job):
    from .context import build_context, _role
    family, binding, role = _role(app, token)
    prompts = [Path(project)/'agent/prompts'/name for name in ('common.md',role+'.md')]
    context = build_context(app, token, job)
    context['run_contract'] = {k:binding.get(k) for k in
        ('run_id','job_id','job_kind','mode','reply_scope','output_mode','recovery_only',
         'planning_reason','pending_id','decision_revision','plan_revision','event_id',
         'current_step_key','commitment_id','schedule_revision','learning_evidence_id','learning_evidence_version',
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
