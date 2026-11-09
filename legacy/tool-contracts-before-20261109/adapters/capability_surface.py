"""Model capability contracts; delegate to unchanged guarded domain methods."""
import json,sys
import tool_surface as surface

READ_GROUPS={
 'skill_read':{'service_catalog','service_skill','service_reference'},
 'workflow_read':{'service_schedule_list','service_case_list','workflow_context','workflow_recheck'},
 'memory_read':{'memory_recall','preference_context'},
}

def enabled():
    return surface.enabled('capabilities_read') and __import__('service_workflow').enabled()

def retired():
    if not enabled():return set()
    names=set().union(*READ_GROUPS.values())
    if __import__('delivery_capability').enabled():names.update(__import__('delivery_capability').REPLACED)
    return names

def native_retired():
    return ['skill'] if enabled() else []

def guidance():
    if not enabled():return ''
    delivery=('当前任务允许交付时使用send_message。家长回复只传body及需要的generation_token/emoji_refs/highlight_ids；主动服务沿用本任务candidate_ids、execution_token和generation_token，节日同时传asset_id。day、event_id、接收家庭由宿主绑定，不传这些字段。' if __import__('delivery_capability').enabled() else '')
    return delivery+('公共读取入口：skill_read()查看目录，skill_read(name=名称)装载实际正文并登记原业务版本回执，'
            'skill_read(name=名称,path=references/文件,start_line,max_lines)分页读取附属材料。'
            'workflow_read(view=context|tasks|cases|refresh)分别取得当前变更上下文、排期、事项或刷新当前任务资料。context可选purpose=inspect|amend|fulfill，默认inspect；fulfill必须传真实task_id。'
            'memory_read()读取当前有效的分主体记忆与遗忘规则；明确遗忘或媒体许可变更前memory_read(for_update=true)取得原并发令牌。'
            '普通记忆仍由夜间整理；旧库不删除，但不把旧未治理记录重新当作当前有效偏好。')

def route(name,args):
    if not isinstance(args,dict):raise ValueError('object_arguments_required')
    a=dict(args)
    if name=='skill_read':
        if set(a)-{'name','path','start_line','max_lines'}:raise ValueError('unknown_skill_read_argument')
        if not a.get('name'):
            if a:raise ValueError('skill_name_required')
            return 'service_catalog',{}
        if not a.get('path'):
            if set(a)-{'name'}:raise ValueError('reference_path_required')
            return 'service_skill',a
        return 'service_reference',a
    if name=='workflow_read':
        if set(a)-{'view','purpose','task_id'}:raise ValueError('unknown_workflow_read_argument')
        if a.get('view','context')!='context' and set(a)&{'purpose','task_id'}:raise ValueError('workflow_purpose_requires_context')
        purpose=a.get('purpose','inspect')
        if purpose not in ('inspect','amend','fulfill'):raise ValueError('invalid_workflow_purpose')
        if purpose=='fulfill':
            if not isinstance(a.get('task_id'),str) or not a['task_id'].strip():raise ValueError('workflow_fulfillment_task_required')
        elif 'task_id' in a:raise ValueError('workflow_task_only_for_fulfillment')
        views={'context':'workflow_context','tasks':'service_schedule_list','cases':'service_case_list','refresh':'workflow_recheck'}
        if a.get('view','context') not in views:raise ValueError('invalid_workflow_view')
        return views[a.get('view','context')],{}
    if name=='memory_read':
        if set(a)-{'for_update'} or type(a.get('for_update',False)) is not bool:raise ValueError('invalid_memory_read_argument')
        return ('preference_context',{}) if a.get('for_update') else ('memory_recall',{})
    raise ValueError('unknown_capability')

def workflow_contract(value,args=None):
    if not isinstance(value,dict) or not value.get('context_token'):return value
    dialogue=value.get('owner',{}).get('type')=='dialogue'
    contract={'mode':'dialogue_revision' if dialogue else 'daily_planning',
              'refresh':'workflow_read(view=context,purpose=amend)',
              'success_evidence':'Only a successful workflow_commit receipt proves a change. After errors, never promise the requested change is effective.'}
    purpose=(args or {}).get('purpose','inspect')
    if dialogue and purpose!='amend':
        contract.update(mode='dialogue_'+purpose,refresh='workflow_read(view=context,purpose='+purpose+')',
            success_evidence='Reading changes nothing. Only actual sent receipts prove delivery; unknown must not be retried blindly.', completion_scope='service_delivery_only', delivery_path='workflow_fulfill owns the existing task and sends its reply; never call send_message for the same fulfillment.')
        if purpose=='fulfill':contract.update(target_task_id=(args or {}).get('task_id'),decisions='Only workflow_fulfill for this exact task_id, revision and context_token. Read its business Skill first; preserve original content/media and receipt requirements. No separate send_message for the same delivery. Refresh with the same task_id.')
        else:contract['decisions']='Inspect only; no workflow write capability is opened.'
    elif dialogue:
        contract.update(decisions='Only amend actual tasks below using their exact task_id and integer revision. Do not enumerate the Skill catalog; do not create, skip, unknown, or amend internal planning tasks.',
          contact_constraint='Use only the exact scope authorized by this message. A skill scope must come from pause_targets and does not require an existing task. For today use period=today; the host sets Beijing-time bounds. A selective pause is never scope=all.',
          empty_tasks='No existing task can be amended, but a category pause MUST be saved independently: workflow_commit(decisions=[], contact_constraint={scope:{kind:skill,skill:one pause_targets key},period:today}, evidence=current exact quote, request_id, context_token). Confirm only after committed plus contact_constraint receipt; never fabricate tasks.',
          refresh_limit='view=refresh only rebuilds an active action task; it does not refresh dialogue transaction context.')
    else:contract['decisions']='Evaluate only this context catalog; create requires the exact opportunity_id and run_at. Do not submit conversation evidence for internal planning.'
    return {**value,'operation_contract':contract}

def install(server):
    if not enabled():return
    definitions={n:(d,p) for n,d,p in server.base_mcp.TOOLS};schema=server.schema;string={'type':'string'}
    server.base_mcp.TOOLS=[x for x in server.base_mcp.TOOLS if x[0] not in retired()]
    server.base_mcp.TOOLS=[__import__('handoff_capability').definition(schema) if x[0]=='request_human_confirmation' else x for x in server.base_mcp.TOOLS]
    server.base_mcp.TOOLS += [
      ('skill_read','按需读取服务Skill目录、已发布正文或references分页。省略参数列目录；name读正文；name+path读附属材料。家长查看、调整或要求本轮完成已有安排时，先读name=manage-service-arrangements；完整履行已有事项走workflow_fulfill，普通回复不完成原事项。沿用原业务版本审计。',schema({'name':string,'path':string,'start_line':{'type':'integer','minimum':1},'max_lines':{'type':'integer','minimum':1,'maximum':120}})),
      ('workflow_read','读取当前家庭工作流。context返回事务上下文及并发令牌：purpose默认inspect只查看；amend才开放修改，fulfill需task_id且只开放履行该实际事项。tasks排期，cases持续事项，refresh重新装载当前任务事实。读取本身不发送。',schema({'view':{'type':'string','enum':['context','tasks','cases','refresh']},'purpose':{'type':'string','enum':['inspect','amend','fulfill']},'task_id':{'type':'string','description':'仅context且purpose=fulfill时必填；沿用本轮实际排期中的ID。'}})),
      ('memory_read','读取当前家庭有效记忆、主体与遗忘规则。for_update=true仅在明确遗忘或媒体许可变更时读取原话证据和提交令牌；普通偏好夜间整理。',schema({'for_update':{'type':'boolean'}})),
    ]
    if __import__('delivery_capability').enabled():server.base_mcp.TOOLS.append(__import__('delivery_capability').definition(schema,definitions))
    # Update technical tool descriptions without rewriting teacher Skill prose.
    def documentation(value):
        if isinstance(value,str):return __import__('tool_surface').current_instructions(value)
        if isinstance(value,dict):return {k:documentation(v) for k,v in value.items()}
        if isinstance(value,list):return [documentation(v) for v in value]
        return value
    server.base_mcp.TOOLS=[(n,documentation(d),documentation(p)) for n,d,p in server.base_mcp.TOOLS]
    server.base_mcp.TOOLS=[(n,('记录当前主动任务的实际结果；不代替交付。普通家长对话由宿主依据持久化回执收尾，不调用本入口。' if n=='service_task_finish' else d),p) for n,d,p in server.base_mcp.TOOLS]
    for n,d,p in server.base_mcp.TOOLS:
        if n=='workflow_commit':
            p['properties']['contact_constraint']={'type':'object','description':'独立暂停约束，不要求已有事项。某类服务从pause_targets选择；今天暂停传period=today，由宿主确定北京时间起止，decisions可为空。','properties':{'scope':{'oneOf':[{'type':'object','properties':{'kind':{'const':'skill'},'skill':{'type':'string'}},'required':['kind','skill'],'additionalProperties':False},{'type':'object','properties':{'kind':{'const':'task'},'task_id':{'type':'string'}},'required':['kind','task_id'],'additionalProperties':False},{'type':'object','properties':{'kind':{'const':'all'}},'required':['kind'],'additionalProperties':False}]},'period':{'type':'string','enum':['today']},'starts_at':{'type':'string'},'ends_at':{'type':'string'}},'required':['scope'],'additionalProperties':False}
            item=p['properties']['decisions']['items']['properties']
            for key in ('task_id','revision'):
                item[key]['description']='keep/reschedule/cancel/no_contact/blocked require the actual task_id AND integer revision from workflow_read(view=context). Empty tasks means no task amendment; never invent one.'
    original=server.business_call
    def call(name,args):
        if name in retired():raise server.agent_plan.ServiceValidationError('tool_entry_consolidated:'+name)
        if name=='request_human_confirmation':
            return original(name,__import__('handoff_capability').route(args))
        if name=='send_message' and __import__('delivery_capability').enabled():
            import observability as audit
            target,params=__import__('delivery_capability').route(args)
            value=audit.invoke(target,params,lambda:original(target,params),origin='adapter')
            return {**value,'delivery_route':target} if isinstance(value,dict) else value
        if name not in READ_GROUPS:return original(name,args)
        target,params=route(name,args)
        import observability as audit
        def operation():
            if target=='memory_recall' and __import__('preference_memory').enabled():
                __import__('experiment_runtime').verify()
                if __import__('preference_memory').internal():raise server.agent_plan.ServiceValidationError('memory_consolidation_has_no_business_permission')
                return __import__('preference_memory').recall()
            return original(target,params)
        value=audit.invoke(target,params,operation,origin='adapter')
        if name=='skill_read' and target=='service_skill':value=__import__('skill_capabilities').loaded(params['name'],value)
        elif name=='skill_read' and target=='service_catalog' and isinstance(value,dict) and __import__('skill_capabilities').enabled():value={**value,'capability_skills':__import__('skill_capabilities').PUBLIC_CAPABILITY_SKILLS}
        if name=='workflow_read' and target=='workflow_context':value=workflow_contract(value,args)
        return __import__('skill_capabilities').read_complete(name,args,value)
    server.business_call=call

if __name__=='__main__':
    assert sys.argv[1]=='retired'
    print(json.dumps({'native':native_retired(),'tools':['mcp__tutor__'+n for n in sorted(retired()|__import__('skill_capabilities').hidden())]}))
