"""Versioned action routing, dependencies and explicit runtime integration gaps.

Catalog readiness means instructions can be loaded, never that a customer
message was sent. Actual eligibility and receipts remain in business tools.
"""
import hashlib

SOURCE_SHA256 = '58e2905867936426357c148f98e2fddf2ed17f2320aae8cf9df01da49fcfc16b'
FAMILIES = {'S': 'supervision', 'I': 'interaction', 'H': 'highlights', 'Q': 'qa'}
WEEKLY_INTERACTION_IDS = ('I01', 'I02', 'I03', 'I04')
WEEKLY_INTERACTION_SKILL = 'invite-reading-interaction'
from legacy.skill_aliases import SKILL_ALIASES, KNOWLEDGE_SKILL_ROUTES
from legacy.retired_materials import DISABLED_REFERENCES


def canonical_skill(name):
    return SKILL_ALIASES.get(name, name)


# ID, title, Skill, business trigger, connected path and outstanding requirements.
ROWS = [
 ('S01','阅读邀请','remind-reading','每日06:30，发送接口复核当日是否仍需邀请','宿主实时上下文 / reply-style / send_message','实时装载学情及正文；正文缺失时仅发简单开课邀请'),
 ('S02','首周完课表扬','celebrate-lesson-completion','首周逐课完成后30分钟；首课合并H01','统一完课事件 / 课后检查 / send_message',''),
 ('S03','历史缺课补读','remind-historical-makeup','新课完成且历史缺课，完成后30分钟；每周一次','统一完课事件 / 最新欠课复核 / send_message','真实补课链接或二维码尚未接通，当前仅文字建议'),
 ('S04','连续三天鼓励','celebrate-learning-streak','连续三个自然日完课后30分钟；每周一次','统一完课事件 / 连续自然日核验 / send_message',''),
 ('S05','零完课询问与规划','support-zero-completion','连续两个应学日零完课，19:00；每周一次','每日规划 / 实时学情装配 / send_message','仅初次关怀；48小时复约核验及真人交接尚未接通'),
 ('S06','防跳课','prevent-lesson-skipping','前后已完、中间缺课；最近完课30分钟内','统一完课事件 / 课程序列缺口核验 / send_message',''),
 ('S07','习惯拉升','rebuild-reading-rhythm','周三19:00检查；上周少于4节且本周0完课','周期检查 / 按需查询家庭、周学情与课程 / send_message',''),
 ('S08','本周最后一课','close-week-reading-gap','周五18:00唤醒；智能体检查本周仅今日一课未完成','周期检查 / 实时本周学情与今日课程装配 / send_message',''),
 ('S09','周五补读邀请','plan-weekend-makeup','周五20:00，本周未完课至少1节或有应学课的零完课','实时待补课核对 / 家长周末安排口信 / send_message','建议课次与数量对应实际缺课；仅文字'),
 ('S10','本周总结','weekly-learning-review','周六09:00，每周全量；全完/部分/零完课','实时本周学情分支 / send_message + 固定PDF','零完课邀请提供人工沟通时间；未确认不称预约成功'),
 ('S11','恢复阅读欢迎','welcome-back-to-reading','恢复前缺课至少5节，真实完课30分钟后；同一恢复事项一次','统一完课事件 / 宿主排期 / send_message','有依据的完成肯定；补课安排和教学表现不由案例推断'),
 ('I01','阅读感受互动',WEEKLY_INTERACTION_SKILL,'周五19:00周互动，四个角度自主选择','周期检查 / 统一周互动候选 / 宿主发前核验 / send_message','对应课程正文、孩子实际回应'),
 ('I02','知识问答互动',WEEKLY_INTERACTION_SKILL,'同一周互动的可选角度，不独立排期','共用I01周互动候选与回执','对应知识点与答案依据'),
 ('I03','阅读方法练习',WEEKLY_INTERACTION_SKILL,'同一周互动的可选角度，不独立排期','共用I01周互动候选与回执','方法原文、需要时使用已有句式卡'),
 ('I04','联系现实生活',WEEKLY_INTERACTION_SKILL,'同一周互动的可选角度，不独立排期','共用I01周互动候选与回执','课程依据'),
 ('I05','生日节日考试关怀','celebrate-important-day','生日或家庭适用节点；生日与H10合并','生日候选及现有图片；四节日由send-festival-wishes生成图文，care-calendar-day处理其它节点','考试需家庭适用事实'),
 ('H01','首日纪念','highlight-course-start','开课首日第一课完成','highlight_lookup / 宿主发前核验 / send_message',''),
 ('H02','首次全对','highlight-first-perfect-answer','业务确认范围内首次题组全对；不因换课换班重置','通用规则；首月素材可能与三日高光合并','独立首月候选、完整首次历史范围与题组事实'),
 ('H03','七日五课','highlight-seven-day-completion','七个自然日窗口五个课程日全部完成','highlight_lookup / 宿主发前核验 / send_message',''),
 ('H04','第一本书','highlight-book-completion','全书逐课完成且书架点亮','highlight_lookup / 宿主发前核验 / send_message',''),
 ('H05','第二周全勤','highlight-second-week-attendance','班期第二周应上课程全部完成','通用规则已接；首月适配未接','第二周完整课表及逐课事实'),
 ('H06','21天里程碑','highlight-reading-milestone','连续21个自然日达标','通用规则已接；首月适配未接','完整日历证据；勋章发放独立回执'),
 ('H07','答题周榜第一','highlight-quiz-ranking','正式发布的当周正确率榜第一','highlight_lookup / 宿主发前核验 / send_message',''),
 ('H08','每周精彩表达','highlight-expressive-answer','审核通过的孩子原话，每周一次','highlight_lookup / 宿主发前核验 / send_message','原话归属、完整语境与审核状态'),
 ('H09','榜单晋档','highlight-ranking-progress','同口径前后榜单晋档','highlight_lookup / 宿主发前核验 / send_message',''),
 ('H10','阅读生日信','celebrate-important-day','生日；与I05同年同生日只交付一次','highlight_lookup / 宿主发前核验 / send_message','真实阅读足迹与现有生日图；不得另建第二份触达'),
 ('H11','月末成长报告','review-learning-progress','自然月末，明确入班后有效统计范围','highlight_lookup / 宿主发前核验 / send_message','表达内容须有原话与审核证据'),
 ('H12','决策期成长信','prepare-growth-review-package','已确认决策节点，决策期1至2次','通用规则已接；首月适配未接','确认节点及历史对比证据；不得推断续费意向'),
 ('H13','成长视频','highlight-growth-video','明确高价值或决策期，家长确认；每学期一次','视频生成与投递未接','真实素材、授权、视频不超过60秒及交付回执'),
 ('Q01','常见问题答疑','answer-faq','收到课程或操作常见问题；目标10分钟','knowledge_search / knowledge_read / send_message','FAQ导航尚非已批准政策答案；回答按返回审核状态'),
 ('Q02','学习内容答疑','explain-reading-content','收到阅读内容问题；首响5分钟、目标24小时闭环','knowledge_read / send_message','对应完整课文；尚非25课全文全部入库'),
 ('Q03','学习效果疑虑','address-learning-concern','收到学习效果疑虑；48小时复查','learning_query / conversation_read / send_message','正式复约、48小时跟进及真人支持接口'),
 ('Q04','退款与投诉','handle-refund-complaint','明确退款、投诉或高风险诉求','即时对话可回复；真人工单未接','正式政策、真人转交受理回执；不承诺退款完成'),
 ('Q05','未匹配问题升级','escalate-unanswered-question','知识库不能可靠回答；目标30分钟内先回应','knowledge_search / send_message；工单未接','真实升级接口、接单人及受理回执'),
 ('Q06','孩子分享与求助','respond-child-sharing','孩子本人分享或求助；目标10分钟','conversation_read / 宿主语音理解 / send_message','微信语音投递未接；高风险需真实人工接续'),
 ('Q07','答疑归档与闭环','archive-question-outcome','每次答疑后；知识候选经审批、每周统计','事件与工具审计已留存','专用问题单、复访统计及知识审批写回未接'),
]
ACTION_SKILLS = {r[2] for r in ROWS}
SUPPLEMENTAL_ROWS = [
 ('I05-festival','节日图文祝福','send-festival-wishes','元旦、除夕、中秋节、端午节09:30',
  '周期检查 / generate_image / 宿主成图检查 / send_message',
  '节日日历已核验2025及2026年；仅完成图片和文字均送达才算完成'),
]


def action_dependencies(code):
    if code in ('S02','S03','S04','S05','S06','S07','S11'):
        return {'skills':['reply-style'],'references':[],'related_actions':[],
                'knowledge':['knowledge_browse','knowledge_search','knowledge_read'],'tier':'not-required'}
    if code=='I05-festival':
        return {'skills':['reply-style'],'references':[],'related_actions':[],
                'knowledge':['knowledge_browse','knowledge_search','knowledge_read'],'tier':'not-required'}
    if code in WEEKLY_INTERACTION_IDS:
        return {'skills':['reply-style'],'references':[], 'related_actions':[],
                'knowledge':['knowledge_browse','knowledge_search','knowledge_read'],
                'tier':'not-required'}
    if code=='S10':
        return {'skills':['reply-style'],'references':[],
                'related_actions':[], 'knowledge':['course_schedule','knowledge_browse','knowledge_search','knowledge_read'],
                'tier':'weekly-completion-count-not-SABCD'}
    family = FAMILIES[code[0]]
    families=['interaction','highlights'] if code in ('I05','H10') else [family]
    skills=['service-policy','tutor-main','reply-style']
    if not code.startswith('Q') and code not in ('S01','S07','S08','S09'):skills.append('proactive-service')
    if code=='S10':skills.append('student-service-tier')
    if code=='I05':skills.append('care-calendar-day')
    related={'S02':['H01'], 'S04':['H06'], 'I05':['H10'], 'H10':['I05'],
             'H08':['I01','I02','I03','I04'],
             'H11':['H08','S10','I01','I02','I03','I04'],
             'H12':['H08','H11'], 'H13':['H08','H11','H12'],
             'Q07':['Q01','Q02','Q03','Q04','Q05','Q06']}
    if code.startswith('Q') and code!='Q07':related[code]=['Q07']
    return {'skills':skills,
            'references': [{'name':'service-policy','path':'references/'+f+'.md'} for f in families
                           if 'references/'+f+'.md' not in DISABLED_REFERENCES.get('service-policy', set())],
            'related_actions':related.get(code,[]),
            'knowledge': (['knowledge_search:faq', 'knowledge_read:reviewed-policy'] if code.startswith('Q')
                          else ['knowledge_read:matching-course-when-content-is-mentioned']),
            'tier': 'host-provided-official-only-if-a-tier-rule-applies'}


def execution_dependencies(code):
    """Describe host independent loading; do not change legacy dependency consumers."""
    if code in {f'S{i:02d}' for i in range(1,12)} or code in WEEKLY_INTERACTION_IDS or code=='I05-festival':
        return {'skills':['reply-style'],'references':[]}
    value=action_dependencies(code)
    return {'skills':value['skills'],'references':value['references']}


def catalog(action_id=None):
    import service_content as content
    available = ROWS + SUPPLEMENTAL_ROWS
    if action_id is not None and action_id not in {r[0] for r in available}:
        raise ValueError('Unknown service action ID')
    actions=[]
    for code,title,name,trigger,path,gaps in (ROWS if action_id is None else available):
        if action_id is not None and action_id != code: continue
        try:
            file=content.skill_file(name)
            raw=file.read_text(encoding='utf-8')
            mounted=True
            digest=hashlib.sha256(raw.encode()).hexdigest()
            references=[str(p.relative_to(file.parent)).replace('\\','/') for p in content.reference_files(name)]
        except ValueError:
            mounted=False;digest=None;references=[]
        actions.append({'id':code,'title':title,'skill':name,'family':FAMILIES[code[0]],
                        'mounted':mounted,'sha256':digest,'trigger':trigger,
                        'dependencies':action_dependencies(code),'execution_dependencies':execution_dependencies(code),'references':references,
                        'runtime_path':path,'requirements_or_gaps':gaps,
                        'shared_with':([other for other in WEEKLY_INTERACTION_IDS if other!=code] if code in WEEKLY_INTERACTION_IDS
                                       else ['I05' if code=='H10' else 'H10'] if code in ('I05','H10') else []),
                        **({'execution_group':WEEKLY_INTERACTION_SKILL,'selection':'agent_choice','independent_task':False}
                           if code in WEEKLY_INTERACTION_IDS else {}),
                        'source_case_policy':'human-marked-supervision-cases' if code.startswith('S') else 'no-reference-cases' if code=='I05-festival' else 'table-examples-only-no-dedicated-human-case'})
    return {'revision':content.REVISION,'source':{'title':'最新数字员工服务动作打样.md','sha256':SOURCE_SHA256},
            'knowledge_skill_routes':KNOWLEDGE_SKILL_ROUTES,
            'supplemental_actions':[{'id':'I05-festival','title':'节日图文祝福','skill':'send-festival-wishes',
                                    'dependencies':action_dependencies('I05-festival'),'calendar_years':[2025,2026]}],
            'total':len(actions),'mounted':sum(a['mounted'] for a in actions),'actions':actions,
            'dependency_semantics':'业务阅读依赖不是自动调用边；实际装载由宿主任务决定，工具权限由当前配置、runtime-capabilities与当前上下文共同限制。',
            'notice':'挂载只证明能读取Skill；执行前仍须实际候选、事实、素材和交付回执。SABCD未知不猜。缺失接口不可用话术冒充。'}
