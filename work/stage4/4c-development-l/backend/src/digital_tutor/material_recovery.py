"""Observe only failed approved reads; resume the original pending service job once."""
from datetime import timedelta
from pydantic import ValidationError
from .domain import BusinessError, timestamp
from .runtime_config import lease_clock, reply_scope
from .storage import digest

RECOVERABLE = {'KNOWLEDGE_UNAVAILABLE','KNOWLEDGE_GAP','SKILL_MISSING'}


def record_read(app, token, name, args, result):
    if not app.strict_runtime or name not in {'knowledge.read','skills.read'}:
        return
    family, binding = app.auth(token, {'service','planner_daily','planner_progress'})
    if binding.get('job_kind')!='service' or not binding.get('job_id'):
        return
    from .knowledge import KnowledgeRead
    from .skill_loader import SkillRead
    try:
        (KnowledgeRead if name=='knowledge.read' else SkillRead).model_validate(args)
    except ValidationError:
        return
    key='material:'+digest([family,binding['job_id'],name,args])
    with app.store.transaction() as db:
        row=db.execute("SELECT data FROM records WHERE kind='runtime_material_wait' AND id=?",(key,)).fetchone()
        if result.get('ok'):
            if row:
                old=app.store.get('runtime_material_wait',key,family)
                if old['status']!='resolved':
                    app.store.put('runtime_material_wait',key,family,{**old,'status':'resolved'},old['version'])
            return
        if result.get('error',{}).get('code') not in RECOVERABLE:
            return
        old=app.store.get('runtime_material_wait',key,family) if row else None
        app.store.put('runtime_material_wait',key,family,{
            'job_id':binding['job_id'],'run_id':binding['run_id'],'tool':name,'arguments':args,
            'status':'unavailable','next_probe_at':(lease_clock(app).now()+timedelta(seconds=30)).isoformat(),
            'resume_count':old.get('resume_count',0) if old else 0,
            'approval_fingerprint':digest(app.material_approval) if getattr(app,'material_approval',None) else None,
            'error':result['error']['code']},old['version'] if old else None)


def ready(app, family, job):
    if job.get('source_recovery_attempt',0)>=3:
        return False
    waits=[w for w in app.store.list('runtime_material_wait',family) if w['job_id']==job['id']]
    return bool(waits) and any(w['status']=='available' for w in waits) and all(
        w['status'] in {'available','resolved'} for w in waits)


def consume(app, family, job):
    # Caller owns the claim transaction. No family business bump and no new job.
    if ready(app,family,job):
        job['source_recovery_attempt']=job.get('source_recovery_attempt',0)+1
    for wait in app.store.list('runtime_material_wait',family):
        if wait['job_id']==job['id'] and wait['status']=='available':
            app.store.put('runtime_material_wait',wait['id'],family,
                          {**wait,'status':'resolved','resume_count':wait.get('resume_count',0)+1},wait['version'])


def observe(app, probe):
    if not app.strict_runtime:
        return
    now=lease_clock(app).now()
    rows=app.store.connection.execute("SELECT family_id,id FROM records WHERE kind='runtime_material_wait'").fetchall()
    for row in rows:
        family=row['family_id']; wait=app.store.get('runtime_material_wait',row['id'],family)
        if wait['status']!='unavailable' or wait.get('resume_count',0)>=3 or timestamp(wait['next_probe_at'])>now:
            continue
        job=app.store.get('job',wait['job_id'],family)
        if job['status']!='waiting' or job.get('source_recovery_attempt',0)>=3 or job.get('message_id') not in reply_scope(app,family):
            continue
        try:
            fingerprint=probe(wait)
            error=None
        except (BusinessError,OSError) as exc:
            fingerprint=None; error=exc.code if isinstance(exc,BusinessError) else type(exc).__name__
        with app.store.transaction():
            current=app.store.get('runtime_material_wait',wait['id'],family)
            if current['version']!=wait['version']:
                continue
            app.store.put('runtime_material_wait',wait['id'],family,{**wait,
                'status':'available' if fingerprint else 'unavailable',
                'source_fingerprint':fingerprint,'error':error,
                'next_probe_at':(now+timedelta(seconds=30)).isoformat()},wait['version'])
        break  # Idle scan has one bounded source probe, never a batch of network waits.


def probe_approved(host, wait):
    """Read-only availability check. The resumed model still must read and answer itself."""
    from pathlib import Path
    from .runtime_assembly import approval
    from .domain import require
    approved=approval(host.project)
    require(approved==host.approved_materials,'MATERIAL_SCOPE','恢复仅使用原批准材料')
    require(wait.get('approval_fingerprint')==digest(approved),'MATERIAL_SCOPE','批准pin与原失败读取不一致')
    hook=host.test_hooks.get('before:'+wait['tool'])
    if isinstance(hook,dict) and (not hook.get('active_file') or Path(hook['active_file']).exists()):
        raise BusinessError('KNOWLEDGE_UNAVAILABLE','来源故障仍存在')
    if wait['tool']=='knowledge.read':
        from .knowledge import KnowledgeCatalog,KnowledgeBinding,OpenVikingCLI
        catalog=KnowledgeCatalog([host.project/approved['manifest_path']],OpenVikingCLI(
            host.project/'runtime/openviking/bin/ov',host.project/'runtime/openviking/enterprise-ovcli.conf',timeout=5))
        result=catalog.dispatch(wait['tool'],wait['arguments'],'service',KnowledgeBinding(**approved['knowledge_binding']))
        return result['content_sha256']
    from .skill_loader import SkillRead
    skill=SkillRead.model_validate(wait['arguments']).skill_id
    require(skill in approved['skills'],'SKILL_SCOPE_FORBIDDEN','恢复Skill不在批准范围')
    # approval verifies the exact Skill and all required guide bytes. Native skill_view
    # is only executed by the resumed native process, never replaced with this probe.
    return digest(approved['files'])
