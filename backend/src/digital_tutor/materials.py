"""Host wiring for approved knowledge and native read-only Skills."""
from pathlib import Path
from .domain import BusinessError, require
from .knowledge import KnowledgeCatalog, KnowledgeBinding, OpenVikingCLI
from .skill_loader import SkillCatalog


MATERIAL_EVIDENCE_BOUNDARY = (
    '宿主来源边界：本次读取的是通用资料，不是本家庭的活动或学情记录。'
    '资料中的方法、示例和预期效果，不能证明孩子做过或没做某活动，也不能证明其理解、能力、心理状态或行为原因。'
    '家庭情况只引用家长明确陈述或核验过的学情、作品；没有提到某活动表示未知，不表示尚未进行。'
    '完整读取只表示该文档已读全，不表示整本书或后续课节已读。书内事实只以本次成功读取的正文为依据；书名引出的既有知识、相邻课和历史AI回复不能补足未读内容，不续写后续情节或安排。'
    '保留每条事实原本的主体与范围，不把部分成员的特点概括成全体共性。正文提到某些人物或事物，不代表它们之间的关系、意图或事件已经得到支持；问句及聊天转场隐含的前提也必须有正文依据。'
    '教学设计不是已发生的课堂记录，方法目的不是必然效果。亲切回应不能替未经核对的评价背书，也不能补造原因来安慰家长。'
)


MATERIAL_FAILURE_BOUNDARY = (
    '本次读取失败，未取得所需资料，不得声称已读或据此作答。'
    '在成功取得回答所需正文和必读规范之前，对尚缺依据的部分只说明实际缺口并保留原未答责任；'
    '不继续讲解、提供教学建议、举例或设计陪聊问题，不借通用或假设标签引入内容。'
    '其他具有独立可靠依据的意图照常处理；读取恢复不等于原问题已回答，后续仍须实际发送和回执。'
)

def configure_materials(app, project, bindings=None, *, approved=None, agent_root=None):
    root = Path(project).resolve()
    paths = [root/'build/openviking/courses/source-c764d8b325cc-nosplit-v2/access-map.json']
    if approved is None:
        paths += sorted((root/'build/openviking/products').glob('*/*/access-map.json'))
    app.knowledge = KnowledgeCatalog([p for p in paths if p.is_file()], OpenVikingCLI(
        root/'runtime/openviking/bin/ov', root/'runtime/openviking/enterprise-ovcli.conf'))
    app.skills = SkillCatalog(agent_root or root/'agent', allowed_skill_ids=approved['skills'] if approved else None)
    app.knowledge_bindings = bindings or {}


def dispatch_material(app, token, name, args):
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
        value=app.knowledge.dispatch(name,args,role,app.knowledge_bindings.get(family))
    if name=='knowledge.list' and app.strict_runtime:
        # Navigation is a locator, not a substitute for a successful body read.
        value={**value,'documents':[{k:v for k,v in document.items() if k!='navigation_summary'}
                                   for document in value['documents']]}
    if app.strict_runtime and name in {'knowledge.read','skills.read'}:
        # Host interpretation guidance; never rewrite approved body, hashes, or guides.
        value={**value,'evidence_boundary':MATERIAL_EVIDENCE_BOUNDARY}
    return value
