"""Host wiring for approved knowledge and native read-only Skills."""
from pathlib import Path
from .domain import BusinessError, require
from .knowledge import KnowledgeCatalog, KnowledgeBinding, OpenVikingCLI
from .skill_loader import SkillCatalog


MATERIAL_EVIDENCE_BOUNDARY = (
    '宿主来源边界：本次读取的是通用资料，不是本家庭的活动或学情记录。'
    '可读取、完整正文、simulation绑定和本次材料使用批准，只代表当前允许参考该材料；均不构成正式业务审核、正式版本确认或班期课节映射确认。'
    '正文与provenance注明未知或未审核的状态保持未知或未审核。不能用资料标题、source_day、当前正文或临时措辞建立正式映射/权益/承诺；需相应的独立确认记录。'
    '解释已读内容时限定到这份资料即可；当前问题不涉及正式审核/绑定时无须附加内部流程说明，涉及则准确说明仍未确认，不自行指定替代口径。'
    '资料中的方法、示例和预期效果，不能证明孩子做过或没做某活动，也不能证明其理解、能力、心理状态或行为原因。'
    '家庭情况只引用家长明确陈述或核验过的学情、作品；没有提到某活动表示未知，不表示尚未进行。'
    '完整读取只表示该文档已读全，不表示整本书或后续课节已读。书内事实只以本次成功读取的正文为依据；书名引出的既有知识、相邻课和历史AI回复不能补足未读内容，不续写后续情节或安排。'
    '保留每条事实原本的主体与范围，不把部分成员的特点概括成全体共性。正文提到某些人物或事物，不代表它们之间的关系、意图或事件已经得到支持；问句及聊天转场隐含的前提也必须有正文依据。'
    '教学设计不是已发生的课堂记录，方法目的不是必然效果。亲切回应不能替未经核对的评价背书，也不能补造原因来安慰家长。'
)


MATERIAL_FAILURE_BOUNDARY = (
    '可通过human.request_help登记当前任务的人工资料补全请求，保留未答责任；接口仅入队，尚未配置通知渠道，不得声称已通知或有人接手。'
    '本次读取失败，未取得所需资料，不得声称已读或据此作答。'
    '在成功取得回答所需正文和必读规范之前，对尚缺依据的部分只说明实际缺口并保留原未答责任；'
    '不继续讲解、提供教学建议、举例或设计陪聊问题，不借通用或假设标签引入内容。'
    '专业讲解所需的必读规范缺失时，课程正文即使可读也不能替代该规范；改称仅据正文、课程设计或未按Skill执行，不解除同一讲解意图的依赖，也不能结清它。'
    '其他具有独立可靠依据的意图照常处理；读取恢复不等于原问题已回答，后续仍须实际发送和回执。'
)


METHOD_SCOPE_CONTRACT = {
    'authority': 'successfully_read_applicable_body_and_required_guides',
    'requirements_are_in_body': True,
    'family_preference_changes_requirements': False,
    'local_activity_proves_method_or_course_completion': False,
    'meaning': ('材料中列明的方法条件与核验依据须完整保留。家庭偏好可改变表达、参与方式和本次活动长度；'
                '不愿继续时可以结束本次活动，但不能把省略条件后的参与称为已完成原方法或达到原课程目标。'
                '能说出结果、列全栏目或配合某种动作，不证明已按原方法取证；对应该方法步骤须核到实际内容及其依据。'),
}

def catalog_paths(project, approved=None):
    root=Path(project).resolve()
    paths=[root/'build/openviking/courses/source-c764d8b325cc-nosplit-v2/access-map.json']
    if approved is None:
        paths+=sorted((root/'build/openviking/products').glob('*/*/access-map.json'))
    else:
        for pin in approved.get('reference_manifests',[]):
            from .runtime_assembly import sha
            path=(root/pin['path']).resolve()
            require(path.is_relative_to((root/'build/openviking').resolve()) and sha(path)==pin['sha256'],
                    'MATERIAL_REVISION_CHANGED','参考清单已变更或越界')
            paths.append(path)
    return paths

def configure_materials(app, project, bindings=None, *, approved=None, agent_root=None):
    root = Path(project).resolve()
    paths = catalog_paths(root,approved)
    app.knowledge = KnowledgeCatalog([p for p in paths if p.is_file()], OpenVikingCLI(
        root/'runtime/openviking/bin/ov', root/'runtime/openviking/enterprise-ovcli.conf'))
    app.skills = SkillCatalog(agent_root or root/'agent', allowed_skill_ids=approved['skills'] if approved else None)
    app.knowledge_bindings = bindings or {}


def dispatch_material(app, token, name, args):
    if name=='knowledge.list' and getattr(app,'context_mode',None)=='daily_native':
        args={'limit':20,**args}
    for role in ('service','planner_daily','planner_progress','admin'):
        try:
            family, _ = app.auth(token, {role})
            break
        except BusinessError as exc:
            if exc.code != 'ROLE_FORBIDDEN':
                raise
    else:
        raise BusinessError('ROLE_FORBIDDEN', '角色不可读取材料')
    if name.startswith('skills.'):
        require(hasattr(app,'skills'), 'SKILL_RUNTIME_UNAVAILABLE', '宿主尚未配置原生Skill读取')
        value=app.skills.dispatch(name,args,role)
    else:
        require(hasattr(app,'knowledge'), 'KNOWLEDGE_UNAVAILABLE', '宿主尚未配置知识来源')
        binding=app.knowledge_bindings.get(family)
        if binding and binding.available_at is not None:
            from dataclasses import replace
            binding=replace(binding,as_of=app.clock.iso())
        if 'catalog_focus' in getattr(app,'context_features',()):
            value=app.knowledge.dispatch(name,args,role,binding,focus=True)
        else:value=app.knowledge.dispatch(name,args,role,binding)
    if name=='knowledge.list' and app.strict_runtime:
        value={**value,'navigation_contract':'标题、摘要和命中词仅定位课节，不是正文证据。一个课节未提到，不证明后续课节或整本书没有；先定位正确主题，再实际读取所需正文。'}
    if app.strict_runtime and name in {'knowledge.read','skills.read'}:
        # Host interpretation guidance; never rewrite approved body, hashes, or guides.
        value={**value,'evidence_boundary':MATERIAL_EVIDENCE_BOUNDARY,
               'method_scope_contract':dict(METHOD_SCOPE_CONTRACT)}
    return value
