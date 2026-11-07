"""One explicit tool contract for memory v3; semantic policy lives in Markdown."""

def obj(properties, required=None):
    return {'type':'object','properties':properties,'required':required or list(properties),'additionalProperties':False}

def text(minimum=1, maximum=600):
    return {'type':'string','minLength':minimum,'maxLength':maximum}

def enum(*values):return {'type':'string','enum':list(values)}

def array(item, maximum, minimum=0):
    return {'type':'array','items':item,'maxItems':maximum,'minItems':minimum}

def tools(keys, immediate_only=False):
    subject={**enum('parent','child'),'description':'实际描述对象是家长或孩子；家长说的第三方信息不归parent。'}
    refs=array({'type':'integer','minimum':1},20,1)
    change=obj({'key':enum(*keys),'subject':subject,'action':{**enum('set','forget'),'description':'普通偏好夜间处理；即时set仅限家长本轮明确允许或禁止图片/表情的media.images/media.emoji操作控制。forget仅在原话明确要求删除时使用。'},
        'value':text(1,300),'source_message_id':text(),'quote':text(1,1500),
        'expires_at':text(),'basis':enum('explicit'),'applicability':enum('persistent','temporary'),
        'reason':text(6),'replaces_memory_ids':array(text(),20)},
        ['key','subject','action','source_message_id','quote','basis','applicability','reason'])
    note=obj({'note':text(6),'subject':subject,'evidence':refs})
    update=obj({'action':enum('add','update','retract','expire'),'memory_id':text(),
        'key':enum(*keys),'subject':subject,'note':text(6),'evidence':refs,
        'basis':{**enum('reported','observed','inferred'),'description':'reported=明确要求/偏好/纠正/家庭安排；observed=描述行为；inferred=由行为推断倾向。家长转述行为仍属observed。'},'applicability':enum('persistent','temporary','uncertain'),
        'scope_note':text(6),'reason':text(6),'expires_at':text()},
        ['action','key','subject','note','evidence','basis','applicability','scope_note','reason'])
    return [
        ('preference_context','读取当前家庭有效偏好、已有陈述性记忆、原话和版本令牌。',obj({})),
        ('preference_commit','即时遗忘与明确媒体控制入口；forget用于明确删除，set仅用于家长原话允许或禁止media.images/media.emoji。普通偏好统一夜间处理。quote须精确原话；reason说明主体和时间依据。',obj({
            'request_id':text(),'context_token':text(),'changes':array(change,8,1)})),
        ('memory_consolidation_context','内部夜间整理：读取冻结新日志、旧记忆、有效偏好和遗忘约束；分页使用next_sequence。',obj({
            'event_id':text(),'after_sequence':{'type':'integer','minimum':0,'description':'首次读取省略或填0；后续页传上次返回的next_sequence。0仅表示本轮窗口首页，不读取旧窗口。'}},['event_id'])),
        ('memory_evidence_read','内部夜间整理：按seq追查本家庭旧原始证据，不读取未来窗口或已遗忘内容。',obj({
            'event_id':text(),'sequences':refs})),
        ('memory_consolidation_prepare','校验完整批次但不应用、不完成任务、不推进水位。先读完context；错误按字段修正；成功返回prepared_token。',obj({
            'event_id':text(),'expected_revision':{'type':'integer','minimum':0},
            'reviewed_through_sequence':{'type':'integer','minimum':0},
            'entries':array(note,20),'changes':array(update,20),'no_change_reason':text(0)},
            ['event_id','expected_revision','reviewed_through_sequence','entries','changes','no_change_reason'])),
        ('memory_consolidation_commit','原子应用已校验批次并完成当前内部任务。仅传prepare实际返回的prepared_token；同token可幂等重试。',obj({
            'event_id':text(),'prepared_token':text()})),
    ]

def validate(value, schema, path='input'):
    """Small exact contract validator shared by direct calls and MCP calls."""
    kind=schema['type']
    ok={'object':type(value) is dict,'array':type(value) is list,'string':isinstance(value,str),
        'integer':type(value) is int}.get(kind,False)
    if not ok:raise ValueError(path+': expected '+kind)
    if kind=='object':
        for key in schema.get('required',[]):
            if key not in value:raise ValueError(path+'.'+key+': required')
        for key,item in value.items():
            if key not in schema['properties']:raise ValueError(path+'.'+key+': unknown field')
            validate(item,schema['properties'][key],path+'.'+key)
    if kind in ('array','string'):
        lo,hi=('minItems','maxItems') if kind=='array' else ('minLength','maxLength')
        if len(value)<schema.get(lo,0) or len(value)>schema.get(hi,float('inf')):raise ValueError(path+': invalid length')
    if kind=='array':
        for i,item in enumerate(value):validate(item,schema['items'],f'{path}[{i}]')
    if kind=='integer' and value<schema.get('minimum',value):raise ValueError(path+': below minimum')
    if 'enum' in schema and value not in schema['enum']:raise ValueError(path+': expected one of '+','.join(schema['enum']))
