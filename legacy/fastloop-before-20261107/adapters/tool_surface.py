"""Versioned experiment tool contract. Host APIs remain callable for rollback."""
import json
import sql_tools as s

def enabled(component=None):
    path=s.R/'config/tool-surface.json'
    if not path.is_file():return False
    if not (s.R/'config/experiment.json').is_file() and not (__import__('manual_workbench').verify() or __import__('family_lab_bridge').verify()):return False
    value=json.loads(path.read_text())
    return value.get('version')==2 and (any(value.get(k) is True for k in ('identity','schedule','knowledge','context','delivery')) if component is None else value.get(component) is True)

def retired():
    names={'family_profile','student_tier'} if enabled('identity') else set()
    if enabled('knowledge'):names.update(('course_knowledge','material_read','context_page'))
    if enabled('context'):names.update(('daily_context','learning_context'))
    if enabled('delivery'):names.update(('reply_with_highlights','deliver_festival'))
    if enabled('retire_legacy'):
        if __import__('service_workflow').enabled():names.update(('service_schedule','service_schedule_cancel','save_plan','pause_contact','service_case_feedback'))
        if __import__('preference_memory').enabled():names.add('memory_remember')
    if enabled('memory_scope'):names.update(('memory_consolidation_context','memory_evidence_read','memory_consolidation_prepare','memory_consolidation_commit'))
    if __import__('host_tool_steps').enabled():names.update(('action_candidates','check_proactive','completion_plan_context','finish_event','memory_review','review_context','understand_audio','transcribe_audio','inspect_festival_image','workflow_highlight'))
    return names

def current_instructions(text):
    """Translate host-authored technical prose only, never business Skill bodies or data keys."""
    if not __import__('capability_surface').enabled():return text
    if __import__('host_tool_steps').enabled():
        text=text.replace('generate_image生成后inspect_festival_image核对，check_proactive通过后deliver_festival一次提交，不用deliver_proactive只发文字。','generate_image生成并返回宿主实际检查结果；检查通过后send_message一次提交图片与配文。需要重做时重新生成，不只发文字。')
        text=text.replace('发送前check_proactive，再deliver_proactive。','使用send_message交付，宿主在发送前核验条件。')
        text=text.replace('或调用check_proactive','或重复执行宿主发前检查')
        text=text.replace('inspect_festival_image','宿主成图检查').replace('finish_event','宿主事件收尾')
    for old,new in [('workflow_context/commit','workflow_read(view=context,purpose=amend)与workflow_commit'),('workflow_context','workflow_read(view=context)'),('workflow_recheck','workflow_read(view=refresh)'),('preference_context','memory_read(for_update=true)'),('service_skill','skill_read'),('service_reference','skill_read(name=名称,path=references/文件)'),('service_catalog','skill_read()')]:text=text.replace(old,new)
    if __import__('delivery_capability').enabled():
        for old in ('reply_to_message','deliver_message','deliver_proactive','deliver_festival','send_image'):text=text.replace(old,'send_message')
    text=text.replace('不创建新的service_schedule、不调用save_plan','不创建新的排期').replace('不创建新的service_schedule，不调用save_plan','不创建新的排期')
    return text

def guidance():
    if not enabled():return ''
    if __import__('capability_surface').enabled() and __import__('host_tool_steps').enabled():
        return ('家庭身份与报名绑定当前session，由宿主提供；联系人不等于本条消息说话者。层级未知时中性服务，不猜测。'
          'course_schedule必填class_type/cohort；无日期返回班期开课自然年，date单日，start_date/end_date区间；has_lesson=null为未知。'
          '课程精确读取使用course_read(class_type,lesson_no)或uri，同版本已装载正文不重读。较长材料用evidence_read按引用和分页读取，旧快照不代表当前事实或授权。'
          '候选、发前核验、完课规划材料、语音理解、节日成图检查和事件收尾由宿主执行并审计；主动资料变化用workflow_read(view=refresh)取得新事实与执行令牌。'
          '被动对话按需learning_query/course_read，历史原话用conversation_window_read。generate_image返回宿主实际检查结果，模型判断是否重做；检查未完成不能发送。'
          '普通assistant文字不代表交付；以实际sent回执为准，unknown不得盲目重试。普通记忆由独立夜间agent整理。'
          +__import__('capability_surface').guidance()+__import__('skill_capabilities').guidance())
    more=('知识工具：course_knowledge已合并到course_read，可传class_type/lesson_no精确定位，或uri读取；不要再调用旧名。'
          '材料分页统一evidence_read(ref={kind:material|context,id:引用},section/path/offset)，保留材料引用和版本；历史快照不授权。' if enabled('knowledge') else '')
    more+=('daily_context和learning_context是宿主装配器，已撤出工具。原Skill里的此类固定读取由已装载宿主资料满足，不再重复调用。'
           '主动资料变化使用workflow_recheck重新装配并取得新执行令牌，再重新判断；被动按需learning_query/course_read，旧对话用conversation_window_read，事项变化用workflow_context。' if enabled('context') else '')
    more+=('回复文字统一reply_to_message，可附highlight_ids发送highlight_lookup核实的1至3张现有高光图；不重新生高光图。节日Skill仍按generate_image、inspect_festival_image、check_proactive、send_image顺序；send_image沿用原deliver_festival的candidate_id/body/asset_id和权限令牌，不能发送外部任意图片。' if enabled('delivery') else '')
    more+=('工具适配v2：家庭身份与报名绑定在当前session，由宿主提供，不能切换家庭；家庭联系人不等于本条消息说话者。'
            'student_tier当前停用，按未知层级提供中性服务，不猜层级。'
            '旧Skill提及family_profile/student_tier时，分别使用会话绑定档案/未知层级，不调用已撤下入口。' if enabled('identity') else '')
    more+=('course_schedule必填class_type/cohort；省略日期返回班期开课自然年，date返回单日，start_date/end_date返回区间；has_lesson=null为未知。' if enabled('schedule') else '')
    if enabled('retire_legacy'):
        if __import__('service_workflow').enabled():more+='旧排期与反馈入口已退役；规划、改期、取消、暂停统一使用workflow_context/workflow_commit，只有committed回执才代表变更成功。'
        if __import__('preference_memory').enabled():more+='普通长期记忆由独立夜间流程整理；明确遗忘或媒体许可仍走preference_context/preference_commit。'
    if enabled('memory_scope'):more+='夜间记忆整理由独立agent执行，本家庭服务任务不调用memory_consolidation或memory_evidence工具。'
    if __import__('host_tool_steps').enabled():more+='固定工具步骤已归宿主：候选、发前条件、完课规划材料、入站语音理解、节日成图检查和事件收尾无需模型调用。主动资料用workflow_recheck刷新，包含新的候选与条件；内部完课规划也可用workflow_recheck刷新。generate_image会返回实际检查结果，模型仍判断是否重做；检查未完成不能发送。语音识别材料由宿主提供，长资料按material_ref使用evidence_read读取；原始语音占位标记不是未处理证明。交付后宿主依据持久化回执收尾，普通assistant文字不代表已经发送。'
    more+=__import__('capability_surface').guidance()
    more+=__import__('skill_capabilities').guidance()
    return more

def install(server):
    if not enabled():return
    schema=server.schema;string={'type':'string'}
    result=[]
    originals={n:(d,p) for n,d,p in server.base_mcp.TOOLS}
    for name,description,parameters in server.base_mcp.TOOLS:
        if name in retired():continue
        if name=='course_schedule' and enabled('schedule'):
            description='查询指定班型和期次的模拟课表。必填class_type/cohort；无日期返回班期开课所在自然年，date单日或start_date/end_date区间互斥。仅返回日期、是否有课和课次；未知日期has_lesson=null，不能推算。'
            parameters=schema({k:string for k in ('class_type','cohort','date','start_date','end_date')},['class_type','cohort'])
        if name=='course_read' and enabled('knowledge'):
            description='读取已登记课程摘要/概览/正文。uri或class_type+lesson_no二选一；按班型课次精确定位默认full，uri默认overview；不以搜索代替精确课次。已提供同版本正文不必重读。'
            parameters=schema({'uri':string,'class_type':string,'lesson_no':{'type':'integer','minimum':1},'detail':{'type':'string','enum':['abstract','overview','full']}})
        if name=='conversation_window_read' and enabled('context'):
            description='按需读取当前家庭历史原话和交付。省略参数从当前业务日首分页开始；续页使用返回的snapshot和next_offset，游标变化时重新读首页。'
            parameters=schema({'business_day':string,'snapshot':string,'offset':{'type':'integer','minimum':0}})
        if name=='reply_to_message' and enabled('delivery'):
            description='回复本轮家长消息；可选highlight_ids发送已由highlight_lookup读取的1至3张高光原图，不生成高光图。文字与图片均检查本轮权限和逐项回执。'
            parameters={**parameters,'properties':{**parameters['properties'],'highlight_ids':originals['reply_with_highlights'][1]['properties']['highlight_ids']}}
        result.append((name,description,parameters))
    if enabled('knowledge'):
        result.append(('evidence_read','读取本家庭本轮已有材料或上下文快照。ref.kind为material/context；context必填section，可附path。offset默认0，沿next_offset续页；旧快照不代表当前学情或执行许可。',schema({'ref':schema({'kind':{'type':'string','enum':['material','context']},'id':string},['kind','id']),'section':string,'path':{'type':'array','maxItems':12,'items':{'anyOf':[string,{'type':'integer','minimum':0}]}},'offset':{'type':'integer','minimum':0}},['ref'])))
    if enabled('delivery'):
        result.append(('send_image','发送当前已授权节日任务的生成图片及配文；仅接受本任务generate_image生成并inspect_festival_image核验的asset_id。保留candidate_id、执行令牌与生成令牌；逐片回执，不接受任意URL。',originals['deliver_festival'][1]))
    server.base_mcp.TOOLS=result
    original=server.business_call
    def call(name,args):
        if name in retired():raise server.agent_plan.ServiceValidationError('tool_entry_retired_use_host_context:'+name)
        if name=='evidence_read' and enabled('knowledge'):
            target,params=__import__('data_readers').evidence_target(**args)
            return original(target,params)
        if enabled('delivery'):
            if name=='send_image':return original('deliver_festival',args)
            if name=='reply_to_message' and 'highlight_ids' in args:return original('reply_with_highlights',args)
        return original(name,args)
    server.business_call=call
