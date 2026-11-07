"""Opt-in schema discovery; execution keeps the original role-bound bridge.

This changes only the model-facing tool surface. It neither grants capabilities
nor makes discovery a read receipt, and it never invokes ToolRegistry directly.
"""
import json
from copy import deepcopy

from .domain import BusinessError, require
from .storage import digest
from .context_transport import fingerprint


ROLES = {'service','planner_daily','planner_progress','memory_daily'}
PURPOSES = {
    'human.request_help':'资料不足或无法处理时登记人工请求，仅入队，尚未通知',
    'human.get_requests':'查看人工请求状态及需要再核验的补充资料',
    'service.account_delivery_reply':'原事项完成后，将实际已覆盖的原来信细分意图关联到既有交付回执，不重复发送',
    'state.read':'读取家庭当前事项、控制与版本',
    'context.read':'刷新本轮服务上下文和必要原文',
    'learning.schema':'读取家庭学情表结构',
    'learning.query_sql':'按家庭隔离只读查询原始学情',
    'learning.summary':'汇总指定天次的原始完课与作业状态',
    'wechat.read_all_messages':'读取当前家庭已到达的聊天快照',
    'wechat.read_message_stream':'分页读取聊天原文',
    'wechat.send_message':'发送已授权回复或交付并返回操作回执',
    'wechat.get_operation':'查原操作回执，不重新发送',
    'message.interpret':'登记本条来信的全部独立意图',
    'control.apply':'持久化暂停或有新依据的解除',
    'planning.record_pending':'登记独立后续需求，不承诺排期',
    'planning.register_change_response':'记录对已有改期提议的明确答复',
    'progress.submit':'提交原事项的新事实报告',
    'planner.commit_daily':'提交唯一业务日计划',
    'planner.commit_admission':'仅接纳本轮绑定的新需求',
    'planner.commit_progress':'仅应用绑定报告关联的事项更新',
    'pending.propose_makeup':'登记符合依据的补学候选',
    'pending.propose_service':'登记来源明确的服务需求候选',
    'service.record_outcome':'最后提交本轮内部结果，不代替外发',
    'service.link_problem':'关联未结旧问题，申请后续任务',
    'service.check_and_advance':'程序核验动作判准和下次时间',
    'service.record_assessment':'提交有原文引用的局部语义判断',
    'resource.read':'检索旧记录并按需读原文',
    'memory.read':'读取家庭有效记忆',
    'memory.save':'依据本轮原文更正记忆',
    'memory.delete':'按明确要求删除记忆',
    'memory.read_batch':'分页读取冻结的日终记忆来源',
    'memory.read_tasks':'读取日终任务与控制账本',
    'memory.propose_daily':'暂存完整日终记忆候选',
    'knowledge.list':'检索课程知识材料目录',
    'knowledge.read':'读取指定课程材料正文',
    'skills.list':'查看当前角色可用服务方法',
    'skills.read':'读取指定服务方法全文',
}


def dumps(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def original_tool_name(call):
    """Identify real material calls in native history without executing anything."""
    fn=call.get('function',{})
    name=fn.get('name')
    if name=='dt_tools_call':
        args=fn.get('arguments',{})
        if isinstance(args,str):
            try:args=json.loads(args)
            except (ValueError,TypeError):return None
        original=args.get('name') if isinstance(args,dict) else None
        return original if isinstance(original,str) else None
    return {'dt_knowledge_read':'knowledge.read','dt_skills_read':'skills.read'}.get(name)


class BusinessToolFacade:
    def __init__(self,business,trace,on_tool,registry):
        self.business,self.trace,self.on_tool,self.registry=business,trace,on_tool,registry
        self.entries={}

    def add(self,name,schema,handler):
        require(name not in self.entries,'TOOL_SURFACE_INVALID','重复业务工具')
        self.entries[name]={'schema':deepcopy(schema),'handler':handler}

    def available(self):
        # Revocation is explicit rather than silently returning an empty catalog.
        self.business.app.auth(self.business.token,ROLES)
        return {s['name'] for s in self.business.describe()} & self.entries.keys()

    def catalog(self,names):
        return [{'name':name,'purpose':PURPOSES.get(name,self.entries[name]['schema']['description'][:64])}
                for name in sorted(names)]

    def describe(self,args):
        require(isinstance(args,dict) and set(args)<={'names','query'},'INVALID_SCHEMA','只接受names或query')
        require(not ('names' in args and 'query' in args),'INVALID_SCHEMA','names和query只能使用一个')
        names=self.available()
        selected=[]
        if 'names' in args:
            selected=args['names']
            require(isinstance(selected,list) and 1<=len(selected)<=4
                    and all(isinstance(n,str) for n in selected)
                    and len(set(selected))==len(selected),'INVALID_SCHEMA','names须为1到4个不重复的完整业务工具名')
            require(all(n in names for n in selected),'TOOL_NOT_AVAILABLE','工具不在本轮授权目录')
        elif 'query' in args:
            query=args['query']
            require(isinstance(query,str) and 1<=len(query.strip())<=120,'INVALID_SCHEMA','query须为1到120字符')
            terms=query.casefold().split()
            names={n for n in names if any(t in (n+' '+PURPOSES.get(n,'')+' '+self.entries[n]['schema']['description']).casefold() for t in terms)}
            if len(names)<=4:selected=sorted(names)
        value={'catalog':self.catalog(names if not selected else selected),
               'source':'current_capability_bound_business_registry',
               'discovery_is_not_business_read_receipt':True}
        if selected:
            value['tools']=[{'name':n,'description':self.entries[n]['schema']['description'],
                             'input_schema':deepcopy(self.entries[n]['schema']['parameters'])} for n in selected]
            value['call_format']={'name':'业务工具名','arguments':'按input_schema填写JSON对象'}
        else:
            value['next']='用names选择1到4项读取精确参数，再通过dt_tools_call调用；不要凭工具名猜参数。'
        return {'ok':True,'result':value}

    def call(self,args):
        require(isinstance(args,dict) and set(args)=={'name','arguments'},'INVALID_SCHEMA','必须且只提供name与arguments')
        name=args['name']
        require(isinstance(name,str),'INVALID_SCHEMA','name须为业务工具名')
        require(isinstance(args['arguments'],dict),'INVALID_SCHEMA','arguments须为JSON对象')
        require(name in self.available(),'TOOL_NOT_AVAILABLE','工具不在本轮授权目录')
        # The registered handler includes runtime gate, compile_send, original
        # validation, read receipts, trace, result projection, and native hooks.
        return self.entries[name]['handler'](args['arguments'])

    def audited(self,kind,action):
        def invoke(args,**unused):
            try:
                value=action(args)
                if kind=='tools.call':return value  # Original handler already audited.
            except BusinessError as exc:
                value={'ok':False,'error':{'code':exc.code,'message':exc.message}}
            model_result=dumps(value)
            record={'tool':kind,'model_tool':'dt_'+kind.replace('.','_'),
                    'arguments_digest':digest(args),'ok':value['ok'],
                    'result_digest':digest(value),'error':value.get('error',{}).get('code'),
                    'at':self.business.app.clock.iso(),'model_result_chars':len(model_result),
                    'raw_result_chars':len(model_result),'model_result_sha256':fingerprint(model_result),
                    'discovery_is_not_business_read_receipt':True}
            self.trace.append(record)
            if self.on_tool:self.on_tool(record)
            finished=getattr(self.business.app,'on_native_tool_finished',None)
            if finished:finished(record)
            return model_result
        return invoke

    def expose(self):
        # Short catalog remains a discovery result, not a permanent schema enum.
        descriptions={
            'dt_tools_describe':('查找本轮角色可用业务工具。空对象返回短目录；names指定1到4项或query搜索用途，返回精确参数。目录不是读取事实或完成动作的回执。',
                {'type':'object','properties':{'names':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':4,'uniqueItems':True},
                 'query':{'type':'string','minLength':1,'maxLength':120}},'additionalProperties':False},self.audited('tools.describe',self.describe)),
            'dt_tools_call':('调用已查明参数的业务工具。name用原业务名称，arguments严格照发现的input_schema填写。仍由原工具核验家庭、角色、版本、来源和回执；错误先读回包再纠正。',
                {'type':'object','properties':{'name':{'type':'string'},'arguments':{'type':'object'}},
                 'required':['name','arguments'],'additionalProperties':False},self.audited('tools.call',self.call)),
        }
        schemas=[]
        for name,(description,parameters,handler) in descriptions.items():
            schema={'name':name,'description':description,'parameters':parameters}
            schemas.append({'type':'function','function':schema})
            self.registry.register(name=name,toolset='digital_tutor',schema=schema,handler=handler,check_fn=lambda:True)
        self.business.app.tool_surface_metrics['discovery_business_schema_chars']=len(dumps(schemas))
        return sorted(descriptions)
