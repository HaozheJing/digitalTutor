"""Reuse the existing adapters and add only memory / planning engineering tools."""
import base_mcp
from sql_tools import memory_remember, memory_recall, save_plan, review_context, family_profile, conversation_read, memory_review, mock_reply, finish_event

original_call = base_mcp.call
string = {'type': 'string'}
schema = base_mcp.schema
base_mcp.TOOLS += [
 ('service_catalog','读取35个服务动作与Skill、共享约束、知识和案例的路由，以及真实执行接口缺口；挂载不代表已执行',schema({'action_id':string})),
 ('knowledge_search','实时检索OpenViking：faq/product查业务知识，emoji按当前沟通场景检索已审核表情及正反例；表情可不选，最多一张，仅本轮检索命中可发送',schema({'query':string,'scope':{'type':'string','enum':['faq','product','emoji']},'limit':{'type':'integer','minimum':1,'maximum':5}},['query'])),
 ('student_tier','读取绑定孩子的S/A/B/C/D服务层级及来源时效；未配置、失效或无法核实时返回unknown，不从完课率推算',schema()),
 ('service_reference','分页读取指定服务Skill的references资料，案例事实与政策须另核验；先service_skill查看可用路径',schema({'name':string,'path':string,'start_line':{'type':'integer','minimum':1},'max_lines':{'type':'integer','minimum':1,'maximum':120}},['name','path'])),
    ('family_profile', '读取独立档案接口的儿童昵称、监护人身份及字段来源；聊天称呼不等于儿童昵称', schema()),
    ('conversation_read', '按source_id读取SQL完整原话及来源，不受最近60条窗口限制', schema({'source_id': string}, ['source_id'])),
    ('memory_review', '完成本事件记忆审查后记录回执；无可记忆事实时说明原因', schema({'event_id': string, 'note': string}, ['event_id','note'])),
    ('mock_reply', '针对新对话事件模拟回复，按事件ID去重；不接真实发送器', schema({'event_id': string, 'body': string}, ['event_id','body'])),
    ('finish_event', '核验模拟回复、记忆审查和随后保存的当日计划，完成持久化事件', schema({'event_id': string,'day': string}, ['event_id','day'])),
    ('memory_remember', '把真实SQL原话提炼为有来源的自报称呼记忆；不会写OpenViking', schema({'key': string, 'value': string, 'source_message_id': string, 'quote': string}, ['key', 'value', 'source_message_id', 'quote'])),
    ('memory_recall', '读取当前家庭SQL长期记忆及原话、时间、哈希', schema()),
    ('save_plan', '记录每日计划和原生排期ID；空计划有效，不能编造候选', schema({'day': string, 'reason': string, 'items': {'type': 'array', 'items': schema({'candidate_id': string, 'schedule_id': string}, ['candidate_id', 'schedule_id'])}}, ['day', 'reason', 'items'])),
    ('review_context', '执行SQL候选中的内部资料复查，不产生对客消息', schema({'candidate_id': string}, ['candidate_id']))
]


def call(name, args):
    extra = {'family_profile': family_profile, 'conversation_read': conversation_read, 'memory_review': memory_review, 'mock_reply': mock_reply, 'finish_event': finish_event}
    if name in extra:
        return extra[name](**args)
    if name == 'memory_remember':
        return memory_remember(**args)
    if name == 'memory_recall':
        return memory_recall()
    if name == 'save_plan':
        return save_plan(**args)
    if name == 'review_context':
        return review_context(**args)
    return original_call(name, args)


import business
import proactive
import test_mode
import agent_plan
business.initialize()
agent_plan.initialize()
legacy_call = call
aliases = {'reply_to_message': 'mock_reply', 'deliver_message': 'send_mock'}
descriptions = {'reply_to_message': '向当前对话交付回复，按事件引用防止重复', 'deliver_message': '交付今日课程提醒，复核条件与联系状态', 'finish_event': '核验本任务对应的交付并完成事件；普通回复无需重做计划', 'memory_remember': '保存有精确原话依据的说话者自报称呼', 'memory_recall': '读取长期记忆及证据', 'daily_context': '读取当前家庭、课程全文、相关对话、安排和状态', 'family_profile': '读取儿童昵称、监护人身份和字段观测时间', 'memory_review': '记录内部资料整理的审查结果', 'conversation_read': '按消息引用读取完整原话和时间', 'save_plan': '核验原生排期回执并保存当日计划', 'review_context': '执行内部资料复查，无需联系用户', 'check_s01': '检查今日课程是否完成，不检查请假', 'knowledge_read': '只读获取已登记的课程、FAQ导航或产品知识正文并核验版本，返回审核状态', 'perceive_image': '理解已登记图片或授权图片地址', 'transcribe_audio': '转写已登记音频或授权音频地址'}
base_mcp.TOOLS = [(next((new for new,old in aliases.items() if old==n),n),d,sc) for n,d,sc in base_mcp.TOOLS if n not in ('api_inventory','check_s01')]
base_mcp.TOOLS = [(n,descriptions.get(n,d),sc) for n,d,sc in base_mcp.TOOLS]
base_mcp.TOOLS = [(n,'核验service_schedule排期回执并保存当日计划，空计划也须说明依据' if n=='save_plan' else d,sc) for n,d,sc in base_mcp.TOOLS]
base_mcp.TOOLS.append(('action_candidates','检查已启用的非周期动作适用条件，提供候选、目的和时间窗口',schema({'day':string},['day'])))
base_mcp.TOOLS.append(('pause_contact','按用户明确原话暂停当天主动联系，仍可回答用户新消息',schema({'day':string,'source_message_id':string,'quote':string},['day','source_message_id','quote'])))
base_mcp.TOOLS.append(('check_proactive','执行主动事项条件代码，返回最新证据、时间窗口和是否可执行；unknown不等于符合',schema({'candidate_id':string},['candidate_id'])))
base_mcp.TOOLS.append(('deliver_proactive','交付1至3个相关主动事项的合并文字消息；事务内复核条件、当前对话、暂停、去重和联系额度。deferred由宿主恢复',schema({'candidate_ids':{'type':'array','items':string,'minItems':1,'maxItems':3},'body':string},['candidate_ids','body'])))
text_body={'type':'string','description':'每行一个完整气泡，每条最多50字。简单一问用一条答完。家长提意见时只谈那一点，不解释教学价值，不加练习或追问；多个明确问题分别答。一次提交，超长需重写。'}
base_mcp.TOOLS=[(n,d,{**sc,'properties':{**sc['properties'],'body':text_body}} if n in ('deliver_proactive','deliver_message','reply_to_message') else sc) for n,d,sc in base_mcp.TOOLS]
base_mcp.TOOLS.append(('inspect_festival_image','核对本任务已生成图片的真实画面和可读文字；生成后发送前必用',schema({'asset_id':string},['asset_id'])))
base_mcp.TOOLS.append(('deliver_festival','提交一次节日图文祝福；必须引用本任务已生成并检查的图片ID，图和文字分别核验回执',schema({'candidate_id':string,'body':text_body,'asset_id':string},['candidate_id','body','asset_id'])))
base_mcp.TOOLS.append(('learning_query','查询当前可见学情；day选择课程表日期',schema({'day':string})))
base_mcp.TOOLS.append(('learning_context','每次实时读取家庭称呼、学情、近期约定和对应课程正文；S02任务绑定刚完成的课（可为补读）；S03任务读取今日已完成课和历史待补课，并装载一节待补课正文；S04任务读取绑定连续段的逐日完成事实，课程正文按需另读；S05任务读取核实的两个应学日和建议补读课正文；S06任务读取同一序列前后完课、中间缺课与一节建议补读正文；其他场景读取当前业务日的今日课表。主动任务已装载时直接使用，需刷新再查；被动对话按需选family_profile、learning_query、course_schedule、course_read等独立接口。按任务限定统计范围',schema({'day':string})))
base_mcp.TOOLS.append(('course_schedule','按日期、班型、期次查询当天课次；date为YYYY-MM-DD，例如2025-10-13、L2、61；无课不递增',schema({'date':string,'class_type':string,'cohort':string},['date','class_type','cohort'])))
base_mcp.TOOLS.append(('course_knowledge','按班型和正整数课次准确读取OpenViking对应书籍课程正文；保留来源及审核状态，无匹配不借用其他课',schema({'class_type':string,'lesson_no':{'type':'integer','minimum':1}},['class_type','lesson_no'])))
base_mcp.TOOLS += [
 ('course_browse','浏览已登记课程目录和下级资料，返回知识地址；不自动加载正文。uri省略时从已接通书目根目录开始',schema({'uri':string,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':30}})),
 ('course_search','在已登记课程目录内按问题检索相关材料，只返回定位线索和摘要；自主选择是否继续阅读',schema({'query':string,'scope_uri':string,'limit':{'type':'integer','minimum':1,'maximum':10}},['query'])),
 ('course_read','按需读取课程资料：abstract摘要、overview概览、full正文；课节或单份材料的目录地址也能直接展开其正文，书目/周目录需先选材料。具体情节和练习查正文，保留来源版本',schema({'uri':string,'detail':{'type':'string','enum':['abstract','overview','full']}},['uri'])),
]
base_mcp.TOOLS.append(('highlight_lookup','读取当前可用高光和已有图片地址',schema({'highlight_id':string})))
base_mcp.TOOLS.append(('reply_with_highlights','回复当前消息并直接发送1至3张当前可用高光图片；先highlight_lookup取得素材ID，不发送文字链接，不生图',schema({'event_id':string,'body':string,'highlight_ids':{'type':'array','items':string,'minItems':1,'maxItems':3}},['event_id','body','highlight_ids'])))
base_mcp.TOOLS += [
 ('completion_plan_context','读取当前完课事件、逐课事实、持续事项和排期，以及并发令牌；只读事实',schema()),
 ('completion_plan_commit','一次提交S02/S03/S04/S06四项判断与排期；request_id稳定重试，全部原子提交',schema({'request_id':string,'context_token':string,'decisions':{'type':'array','items':schema({'code':string,'decision':string,'reason':string,'evidence':{'type':'array','items':string},'run_at':string,'task_id':string},['code','decision','reason','evidence'])}},['request_id','context_token','decisions'])),
 ('service_case_list','只读当前轮次持续事项与目标、反馈、交付和内部检查',schema()),
 ('service_case_review','记录内部检查结果，不创建对客任务，不发送',schema({'reason':string},['reason'])),
 ('service_case_feedback','引用当前家庭消息原话记录暂停/停止/恢复/意向/关闭；意向不等于完课；revision防并发',schema({'case_id':string,'revision':{'type':'integer'},'request_id':string,'source_message_id':string,'quote':string,'action':{'type':'string','enum':['pause','stop','resume','intent','close']}},['case_id','revision','request_id','source_message_id','quote','action'])),
 ('service_skill','读取实际服务Skill正文与版本，遵循其规划/执行要求；同轮同版本可以沿用',schema({'name':string},['name'])),
 ('service_schedule_list','读取当前任务、固定检查和已落实的排期',schema()),
 ('service_schedule','为符合条件的候选落实服务排期，核验时间窗口与重复安排；run_at为带时区的ISO时间',schema({'candidate_id':string,'run_at':string,'reason':string,'check_conditions':string},['candidate_id','run_at','reason','check_conditions'])),
 ('service_schedule_cancel','有新依据时取消本轮尚未执行的非周期安排；不删除既有执行证据',schema({'task_id':string,'reason':string},['task_id','reason'])),
 ('service_task_finish','核验本服务任务的Skill、最新查询、计划或交付回执后记录结果；不代替finish_event处理微信被动消息',schema({'task_id':string,'outcome':{'type':'string','enum':['completed','no_contact','blocked','deferred']},'reason':string},['task_id','outcome','reason'])),
]
base_mcp.TOOLS += [
 ('workflow_recheck','当前行动任务事实变化后重新装配最新资料与执行令牌',schema()),
 ('workflow_highlight','仅交付本任务绑定高光的接口原文和原图',schema()),
 ('workflow_context','读取最新授权服务、事实、计划版本和本轮消息，取得原子提交令牌',schema()),
 ('workflow_commit','原子提交规划或局部修订；对话修改需本轮来源原话，固定时点不放宽。明确暂停可附contact_constraint={scope:{kind:task|skill|all,task_id或skill},starts_at,ends_at}，不得扩大原话范围',schema({'request_id':string,'context_token':string,'decisions':{'type':'array','items':schema({'skill':string,'decision':{'type':'string','enum':['create','keep','reschedule','cancel','skip','unknown','no_contact','blocked']},'reason':string,'opportunity_id':{**string,'description':'decision=create时必填，原样使用本次workflow_context.catalog中该Skill的opportunities[].id'},'run_at':{**string,'description':'decision=create或reschedule时必填，使用合法ISO时点；固定时点沿用catalog.timing.at'},'task_id':string,'revision':{'type':'integer'}},['skill','decision','reason'])},'evidence':{'type':'array','description':'仅用于本轮家庭对话原话；内部规划不传此字段，事实依据写在各项reason','items':schema({'source_message_id':string,'quote':string},['source_message_id','quote'])},'contact_constraint':{'type':'object'}},['request_id','context_token','decisions'])),
 ('workflow_fulfill','当前回复完整履行既有任务时，发送前原子关联并使旧唤醒失效；最终sent才完成。高光需原文和绑定highlight_ids',schema({'request_id':string,'context_token':string,'task_id':string,'revision':{'type':'integer'},'body':text_body,'evidence':string,'highlight_ids':{'type':'array','items':string}},['request_id','context_token','task_id','revision','body','evidence'])),
]
base_mcp.TOOLS.append(('request_human_confirmation','当前消息或服务任务资料不足、超出权限或需要人工判断时提交异常提醒；event_id和task_id二选一。只转交不发送，当前事项暂停，工作台由人工编辑并确认回复；转交后不得继续向家庭发送',schema({
     'reason':string,'proposed_reply':string,'event_id':string,'task_id':string,
     'category':{'type':'string','enum':['needs_confirmation','missing_information','unsupported_request','delivery_problem','other']}},['reason'])))

base_mcp.TOOLS=[(n,d,{**sc,'properties':{**sc['properties'],'execution_token':string}} if n in ('deliver_message','deliver_proactive','deliver_festival','workflow_highlight','service_task_finish') else sc) for n,d,sc in base_mcp.TOOLS]

import message_hooks
base_mcp.TOOLS=[(n,d,{**sc,'properties':{**sc['properties'],'generation_token':string}} if n in message_hooks.TOOLS else sc) for n,d,sc in base_mcp.TOOLS]
EMOJI_TOOLS = {'reply_to_message','reply_with_highlights','deliver_message','deliver_proactive','deliver_festival','workflow_fulfill'}
emoji_refs = {'type':'array','maxItems':1,'items':schema({'asset_id':string,'version':string},['asset_id','version'])}
base_mcp.TOOLS=[(n,d,{**sc,'properties':{**sc['properties'],'emoji_refs':emoji_refs}} if n in EMOJI_TOOLS else sc) for n,d,sc in base_mcp.TOOLS]

import preference_memory
base_mcp.TOOLS += __import__('memory_protocol').tools(preference_memory.KEYS, immediate_only=True)

base_mcp.TOOLS.append(('material_read','分页复用本家庭本轮已读取的课程或语音资料；历史版本不是实时学情，不授予操作权限',schema({'material_id':string,'offset':{'type':'integer','minimum':0}},['material_id'])))
base_mcp.TOOLS.append(('context_page','按页读取本家庭本轮的历史材料快照；不是当前事实或执行授权。沿next_offset继续读取',schema({'context_id':string,'section':string,'path':{'type':'array','maxItems':12,'items':{'anyOf':[{'type':'string'},{'type':'integer','minimum':0}]}},'offset':{'type':'integer','minimum':0}},['context_id','section'])))
base_mcp.TOOLS.append(('conversation_window_read','继续读取daily_context未装完的真实对话；保持snapshot，按next_offset分页，不生成摘要。',schema({'business_day':string,'snapshot':string,'offset':{'type':'integer','minimum':0}},['business_day','snapshot','offset'])))

def business_call(name,args):
    if name=='conversation_read':name,args=__import__('query_contract').conversation_args(args)
    experiment=__import__('experiment_runtime').verify()
    if preference_memory.internal() and name not in {'memory_consolidation_context','memory_consolidation_prepare','memory_consolidation_commit','memory_evidence_read'}:
        raise agent_plan.ServiceValidationError('memory_consolidation_has_no_business_or_send_permission')
    if preference_memory.enabled() and name=='memory_remember':
        raise agent_plan.ServiceValidationError('ordinary_memory_is_nightly_only')
    if name=='conversation_window_read':
        try:return business.project(__import__('dialogue_context').window(args.get('business_day') or test_mode.clock().date().isoformat(),args.get('offset',0),args.get('snapshot')))
        except ValueError as error:raise agent_plan.ServiceValidationError(str(error)) from error
    memory_calls={'preference_context':preference_memory.context,'preference_commit':preference_memory.commit,
                  'memory_consolidation_prepare':preference_memory.consolidation_prepare,'memory_evidence_read':preference_memory.evidence_read,
                  'memory_consolidation_context':preference_memory.consolidation_context,'memory_consolidation_commit':preference_memory.consolidation_commit}
    if name in memory_calls:
        try:
            args=dict(args)
            if name=='preference_commit' and any(x.get('action')!='forget' and x.get('key') not in ('media.images','media.emoji') for x in args.get('changes',[])):
                raise ValueError('ordinary_memory_is_nightly_only')
            if name=='preference_commit':
                import re
                for change in args.get('changes',[]):
                    if change.get('action')!='set':continue
                    quote=change.get('quote','');value=change.get('value')
                    noun='(?:图片|图文|照片)' if change.get('key')=='media.images' else '(?:表情|表情包)'
                    verb='(?:允许|可以|同意)' if value=='allowed' else '(?:不要|禁止|别|不允许)'
                    if change.get('subject','parent')!='parent' or (value=='allowed' and re.search(r'不要|禁止|不允许|别发',quote)) or not re.search(verb+r'.{0,12}'+noun,quote):
                        raise ValueError('explicit_media_control_required')
            if name=='preference_commit':
                args['changes']=[{**x,'source_message_id':business.original(x.get('source_message_id')),
                    **({'replaces_memory_ids':[business.original(v) for v in x['replaces_memory_ids']]} if 'replaces_memory_ids' in x else {})} for x in args.get('changes',[])]
            if 'event_id' in args:args['event_id']=business.original(args['event_id'])
            if name=='preference_commit':
                current=business.s.read("SELECT payload FROM events WHERE kind='passive' AND status='dispatched'")
                if not current:raise ValueError('explicit_preference_requires_current_dialogue')
                import json
                sources=set()
                for row in current:
                    payload=json.loads(row['payload']);sources.update(payload.get('source_message_ids',[]) or [payload.get('source_message_id')])
                if any(x['source_message_id'] not in sources for x in args['changes']):raise ValueError('explicit_preference_requires_current_batch_evidence')
            return memory_calls[name](**args)
        except ValueError as error:raise agent_plan.ServiceValidationError(str(error)) from error
    a=dict(args)
    refs=a.pop('emoji_refs',[])
    if refs and name not in EMOJI_TOOLS:raise ValueError('emoji_not_supported_for_tool')
    try:
        with __import__('emoji_assets').selection(refs):
            result=_generation_call(name,a)
            if experiment and name in message_hooks.TOOLS and isinstance(result,dict):
                result={**result,'simulation':True,'experiment_run_id':experiment['experiment_run_id']}
            return result
    except __import__('emoji_policy').PolicyViolation as error:
        raise agent_plan.ServiceValidationError(str(error)) from error


def _generation_call(name,args):
    a=dict(args)
    if name in message_hooks.TOOLS:
        try:__import__('execution_contract').validate_body(a.get('body'),[a.get('generation_token'),a.get('execution_token')])
        except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    token=a.pop('generation_token',None)
    if message_hooks.enabled() and name in message_hooks.TOOLS:
        try:
            with message_hooks.authority(token):
                result=_business_call(name,a)
                stopped=business.s.read('SELECT 1 FROM message_hook_deliveries WHERE token=? AND stopped=1',(token,))
                return message_hooks.interrupt(token) if stopped else result
        except message_hooks.StaleGeneration:return message_hooks.interrupt(token)
    return _business_call(name,a)

def _business_call(name,args):
    a=dict(args)
    try:
        __import__('operator_alerts').guard_model_delivery(name,a)
    except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    permission_name=('course_browse' if name=='knowledge_browse' else 'course_read' if name=='knowledge_read' else 'course_search') if name in ('knowledge_browse','knowledge_search','knowledge_read') and a.get('scope','curriculum' if name!='knowledge_search' else 'faq')=='curriculum' else name
    if name=='knowledge_read':
        ident=a.get('document_id') or (a.get('target') or {}).get('document_id')
        if ident and any(x['id']==ident for x in __import__('knowledge_search').catalog()['documents']):permission_name='knowledge_read'
    import service_workflow as wf
    if wf.enabled():
        current=agent_plan.active()
        if name in ('deliver_message','deliver_proactive','deliver_festival','workflow_highlight') or (name=='service_task_finish' and current and current['kind'] not in wf.INTERNAL):
            try:wf.execution_authority(a.pop('execution_token',None))
            except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
        else:a.pop('execution_token',None)
        if current and current['kind']=='plan' and permission_name not in {'workflow_context','workflow_commit','service_task_finish','service_skill','service_reference','family_profile','learning_query','daily_context','action_candidates','service_schedule_list','course_schedule','conversation_read','context_page','service_case_list','student_tier'}:raise agent_plan.ServiceValidationError('内部规划不能外发或调用旧排期工具')
        if name.startswith('workflow_'):
            try:
                return getattr(wf, name.removeprefix('workflow_'))(**a)
            except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    import completion_planner as cp
    if cp.internal() and permission_name not in {'service_skill','service_reference','completion_plan_context','completion_plan_commit','workflow_recheck','service_case_list','service_case_review','service_task_finish','daily_context','learning_query','family_profile','conversation_read','service_schedule_list','action_candidates','course_schedule','course_knowledge','course_browse','course_search','course_read','student_tier','highlight_lookup','memory_recall','context_page'}:
        raise agent_plan.ServiceValidationError('内部规划与检查不可联系家庭、修改家庭反馈或使用普通排期工具')
    try:
        if name=='completion_plan_context':return cp.context()
        if name=='completion_plan_commit':return cp.commit(**a)
        if name=='service_case_review':return cp.review(**a)
        if name=='material_read':return __import__('material_cache').read(**a)
        if name=='context_page':return __import__('working_context').page(**a)
        if name=='service_case_list':
            result=__import__('working_context').model({'ledger':__import__('service_ledger').view()})
            return {**result['ledger'],**({'context_archive':result['context_archive']} if 'context_archive' in result else {})}
        if name=='service_case_feedback':return __import__('service_ledger').feedback(**a)
    except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    import operator_alerts
    try:
        delivery_args=dict(a)
        if 'event_id' in delivery_args:delivery_args['event_id']=business.original(delivery_args['event_id'])
        operator_alerts.guard_model_delivery(name,delivery_args)
    except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    if name=='service_reference':
        excluded=agent_plan.excluded_s10_shared_skill(a.get('name'))
        if excluded:return excluded
    if name=='check_s01':return {'status':'retired','reason':'先按课表查学情，已完课结束；未完课读知识并组织消息，提交deliver_message统一校验'}
    if name in ('course_schedule','course_knowledge'):
        if name=='course_schedule' and __import__('tool_surface').enabled('schedule'):
            return __import__('schedule_query').read(**a)
        import course_service
        return __import__('passive_context').query(name,a,lambda:getattr(course_service,name)(**a))
    if name in ('course_browse','course_search','course_read'):
        if name=='course_read' and __import__('tool_surface').enabled('knowledge'):
            normalized=__import__('data_readers').course_args(**a)
            if normalized is None:return {'status':'no_match','class_type':a.get('class_type'),'lesson_no':a.get('lesson_no'),'reason':'该班型课次尚无登记课程资料'}
            a=normalized
        import course_navigation
        try:return __import__('passive_context').query(name,a,lambda:getattr(course_navigation,name)(**a))
        except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    if name in ('knowledge_browse','knowledge_search','knowledge_read'):
        return __import__('query_contract').live_call(name,a)
    if name=='service_catalog':
        import service_catalog
        try:return service_catalog.catalog(**a)
        except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    if name=='knowledge_search':
        import knowledge_search
        try:
            result=knowledge_search.search(**a)
            return __import__('data_readers').knowledge_result(result) if __import__('tool_surface').enabled('knowledge') and a.get('scope')!='emoji' else result
        except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    if name=='knowledge_read':
        import knowledge_search
        if any(x['id']==a.get('document_id') for x in knowledge_search.catalog()['documents']):
            return knowledge_search.read_document(a['document_id'])
    if name in ('student_tier','service_reference'):
        import service_content
        try:return service_content.student_tier() if name=='student_tier' else service_content.reference(**a)
        except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    if name=='learning_context':return __import__('passive_context').query(name,a,lambda:__import__('learning_context').read(a.get('day')))
    if name=='learning_query':return __import__('passive_context').query(name,a,lambda:test_mode.snapshot(a.get('day')))
    if name=='highlight_lookup':return test_mode.highlight_lookup(a.get('highlight_id'))
    if name=='generate_image' and test_mode.state() and agent_plan.festival_task():
        import festival_service
        return __import__('host_tool_steps').inspected_image(festival_service.generate_image(**a))
    if name in ('inspect_festival_image','deliver_festival'):
        import festival_service
        if name=='deliver_festival':a['candidate_id']=business.original(a['candidate_id'])
        return business.project(getattr(festival_service,name)(**a))
    if name=='generate_image' and test_mode.state():
        return {'status':'disabled','reason':'首月高光使用highlight_lookup的已有图片'}
    for key in ['event_id','source_id','source_message_id','candidate_id','document_id','asset_id','task_id']:
        if key in a:a[key]=business.original(a[key])
    try:
        if name=='request_human_confirmation':return operator_alerts.request_alert(**a)
    except ValueError as error:raise agent_plan.ServiceValidationError(str(error))
    if name=='service_skill':return agent_plan.load_skill(**a)
    if name=='service_schedule_list':return agent_plan.schedules()
    if name=='service_schedule':return agent_plan.schedule(**a)
    if name=='service_schedule_cancel':return agent_plan.cancel(**a)
    if name=='service_task_finish':return agent_plan.finish(**a)
    if name in ('reply_to_message','reply_with_highlights') and agent_plan.enabled():
        task=agent_plan.active()
        if task and task['skill']=='prevent-lesson-skipping' and task['kind'] in ('signal','action'):
            raise agent_plan.ServiceValidationError('当前为S06阅读衔接任务，请用deliver_proactive和本任务候选、执行令牌提交邀请。')
        if agent_plan.zero_task(task):
            raise agent_plan.ServiceValidationError('当前为S05主动关怀任务，请用deliver_proactive和本任务候选、执行令牌提交关怀正文。')
        if task and task['skill']=='celebrate-learning-streak' and task['kind'] in ('signal','action'):
            raise agent_plan.ServiceValidationError('当前为S04主动任务，所需Skill与事实已装载。请用deliver_proactive(candidate_ids=[task.candidate_id], body=鼓励正文, execution_token=task.execution_token)提交本次鼓励。')
        agent_plan.require_skills(*business.passive_reply_skills())
        event=business.s.read('SELECT kind FROM events WHERE id=?',(a.get('event_id'),))
        if not event or event[0]['kind']!='passive':
            raise agent_plan.ServiceValidationError('event_id 应使用当前任务引用；原话的 source_id 只能用于读取或引用资料。请核对后再调用，不要重复发送。')
    if name in ('reply_to_message','reply_with_highlights'):
        __import__('voice_understanding').guard_reply(a.get('event_id'),a.get('body'))
        __import__('image_inputs').guard_reply(a.get('event_id'))
    if name=='reply_with_highlights':
        import wechat_test
        a['highlight_ids']=[business.original(x) for x in a['highlight_ids']]
        return business.project(wechat_test.reply_with_highlights(**a))
    if name=='daily_context':return __import__('task_context').daily_context(a['day'])
    if name=='action_candidates':return __import__('task_context').candidates(a['day'])
    if name=='pause_contact':return business.pause_contact(**a)
    if name=='deliver_message' and business.s.read('SELECT 1 FROM contact_constraints WHERE day=?',(a['day'],)):return {'status':'skipped','reason':'用户要求暂停当日主动联系'}
    if name=='finish_event':return business.finish(**a)
    if name=='save_plan':return business.save_plan(**a)
    if name=='check_proactive':return business.project(proactive.check(**a))
    if name=='deliver_proactive':
        ids=[business.original(x) for x in a['candidate_ids']]
        if agent_plan.enabled():agent_plan.guard_delivery(ids)
        result=proactive.deliver(ids,a['body'])
        if agent_plan.enabled() and result.get('action_key'):
            import wechat_test
            wechat_test.flush()
            actual=business.s.read('SELECT status FROM deliveries WHERE action_key=?',(result['action_key'],))
            if actual:result['status']=actual[0]['status']
        if agent_plan.enabled():agent_plan.link_delivery(result)
        return business.project(result)
    return business.project(legacy_call(aliases.get(name,name),a))
def audited_call(name,args):
    __import__('skill_capabilities').guard(name,args)
    import observability
    from agent_view import project
    value=observability.invoke(name,args,lambda:business_call(name,args),origin='model')
    if test_mode.state():value=__import__('material_cache').capture(name,args,value)
    if name=='course_schedule':
        import json
        from pathlib import Path
        catalog=json.loads((Path(__file__).resolve().parents[1]/'knowledge/curriculum/L2/catalog.json').read_text())
        value=__import__('query_contract').schedule_coordinates(value,args.get('class_type'),catalog['lessons'])
    if name in ('conversation_read','conversation_window_read','evidence_read','learning_query','course_schedule') and isinstance(value,dict):
        value=__import__('query_contract').provenance(value, name, name in ('conversation_read','conversation_window_read','evidence_read'))
    return project(value)
base_mcp.call = audited_call
__import__('tool_surface').install(__import__('sys').modules[__name__])
__import__('capability_surface').install(__import__('sys').modules[__name__])
__import__('query_contract').install(__import__('sys').modules[__name__])
if __name__ == '__main__':
    base_mcp.main()
