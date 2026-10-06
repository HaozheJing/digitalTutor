"""JSON-callable registry. Host binds a capability, never model-controlled roles."""
from pydantic import ValidationError

from .domain import BusinessError, Query, MessageRead, Send, Inbound, Interpret, ControlCommand, PendingInput, Report, DailyCommit, AdmissionCommit, ProgressCommit, OperationRead, Receipt
from .learning import query_learning, PUBLIC_SCHEMA, SCHEMA_NOTES
from .wechat import read_messages, send_message, mock_receipt
from .family_memory import SPECS as MEMORY_SPECS, HANDLERS as MEMORY_HANDLERS
from .context import SPECS as CONTEXT_SPECS, HANDLERS as CONTEXT_HANDLERS
from .knowledge import SPECS as KNOWLEDGE_SPECS
from .skill_loader import SPECS as SKILL_SPECS


SPECS={
    'state.read': ({'service','planner_daily','planner_progress','admin'},None,'读取当前家庭事项、控制、报告和版本'),
    'learning.schema': ({'service','planner_daily','planner_progress','admin'},None,'返回当前家庭SQL表、字段及说明；learning_batches含可查询课程绑定、导入及覆盖元数据'),
    'learning.query_sql': ({'service','planner_daily','planner_progress','admin'},Query,'在当前家庭uid隔离只读数据集查询原始学情与learning_batches课程绑定/覆盖元数据。动态筛选值使用 :命名参数 与parameters映射，不将用户或来源值拼入SQL。source_index仅定位及提供有界JSON对象顶层字段名，不含值；内容需实际查询'),
    'wechat.read_messages': ({'service','planner_daily','planner_progress','admin'},MessageRead,'all全量、latest最新N条、since游标增量；不会标记已答'),
    'wechat.send_message': ({'service'},Send,'模拟发送；body须以来源和实际结果为据，待规划需求仅确认登记及未安排状态，不许诺未来动作；窗口、暂停、原义务和版本校验后返回操作号'),
    'wechat.receive_message': ({'channel','admin'},Inbound,'仅测试渠道接收入站消息'),
    'wechat.mock_receipt': ({'channel','admin'},Receipt,'注入原模拟操作回执'),
    'wechat.get_operation': ({'service','planner_daily','planner_progress','admin'},OperationRead,'按原操作号核对结果，不重新发送'),
    'message.interpret': ({'service'},Interpret,'一次保存多意图覆盖；默认reply，家长明确要求未来独立文字交付才标future_delivery并引用原文service_authorization。当前缺资料不改变服务类型，解释不代表已答'),
    'control.apply': ({'service'},ControlCommand,'暂停或有新依据的解除，即时持久化；event_id为null表示全家庭范围的所列action_kinds，不能从消息主题猜测只暂停某事项'),
    'planning.record_pending': ({'service'},PendingInput,'仅登记尚未安排的独立后续需求，不接单、不排期、不承诺履行；原期限为用户期望，成功后只能确认登记和未安排状态'),
    'progress.submit': ({'service'},Report,'报告原事项新事实，持久化并登记待处理任务'),
    'planner.commit_daily': ({'planner_daily'},DailyCommit,'唯一业务日计划提交'),
    'planner.commit_admission': ({'planner_daily'},AdmissionCommit,'仅接纳本次宿主绑定的新需求，保存局部排期修订；不覆盖今日基线，不处理无关事项'),
    'planner.commit_progress': ({'planner_progress'},ProgressCommit,'仅应用绑定报告关联的原事项更新'),
}
from .runtime_outcome import SPECS as OUTCOME_SPECS, HANDLERS as OUTCOME_HANDLERS
from .family_candidates import SPECS as CANDIDATE_SPECS, HANDLERS as CANDIDATE_HANDLERS
from .service_requests import SPECS as REQUEST_SPECS, HANDLERS as REQUEST_HANDLERS
SPECS.update(REQUEST_SPECS)
SPECS.update(CANDIDATE_SPECS)
SPECS.update(OUTCOME_SPECS)
SPECS.update(MEMORY_SPECS)
SPECS.update(CONTEXT_SPECS)
SPECS.update(KNOWLEDGE_SPECS)
SPECS.update(SKILL_SPECS)


class ToolRegistry:
    def __init__(self, app, token):
        self.app,self.token=app,token

    def describe(self):
        result=[]
        for name,(roles,model,description) in SPECS.items():
            try:
                self.app.auth(self.token,roles)
            except BusinessError:
                continue
            if name in {'planner.commit_daily','planner.commit_admission'}:
                _, binding = self.app.auth(self.token, roles)
                admission = binding.get('planning_reason') == 'admission'
                if (name == 'planner.commit_admission') != admission: continue
            schema=model.model_json_schema() if model else {'type':'object','additionalProperties':False,'properties':{}}
            result.append({'name':name,'description':description,'input_schema':schema})
        return result

    def call(self,name,arguments):
        try:
            if not isinstance(arguments, dict):
                raise BusinessError('INVALID_SCHEMA','工具参数必须是JSON对象')
            if name not in SPECS:
                raise BusinessError('TOOL_NOT_FOUND','工具不存在')
            roles,_,_=SPECS[name]
            family,binding=self.app.auth(self.token,roles,allow_inactive=True)
            readonly={'state.read','learning.schema','learning.query_sql','wechat.read_messages','wechat.get_operation',
                      'context.read','memory.read','knowledge.list','knowledge.read','skills.list','skills.read','service.record_outcome'}
            if name not in readonly and binding.get('run_id'):
                finalized=self.app.store.connection.execute("SELECT 1 FROM records WHERE kind='run_outcome' AND family_id=? AND id=?",(family,binding['run_id'])).fetchone()
                if finalized:
                    raise BusinessError('OUTCOME_FINALIZED','已提交内部结果，不能继续业务写入')
            if name in MEMORY_HANDLERS or name in CONTEXT_HANDLERS or name in OUTCOME_HANDLERS or name in CANDIDATE_HANDLERS or name in REQUEST_HANDLERS:
                value={**MEMORY_HANDLERS, **CONTEXT_HANDLERS, **OUTCOME_HANDLERS, **CANDIDATE_HANDLERS, **REQUEST_HANDLERS}[name](self.app,self.token,arguments)
                return {'ok':True,'result':value}
            if name in KNOWLEDGE_SPECS or name in SKILL_SPECS:
                from .materials import dispatch_material
                value=dispatch_material(self.app,self.token,name,arguments)
                return {'ok':True,'result':value}
            simple={'state.read':lambda:self.app.state(self.token),
                    'learning.schema':lambda:{'tables':PUBLIC_SCHEMA,'table_notes':SCHEMA_NOTES,'scope':'bound_family_uid','read_only':True}}
            if name in simple:
                self.app.auth(self.token,roles)
                if arguments:
                    raise BusinessError('INVALID_SCHEMA','该工具不接受参数')
                value=simple[name]()
            elif name=='learning.query_sql':
                value=query_learning(self.app,self.token,arguments)
            elif name=='wechat.read_messages':
                value=read_messages(self.app,self.token,arguments)
            elif name=='wechat.send_message':
                value=send_message(self.app,self.token,arguments)
            elif name=='wechat.mock_receipt':
                value=mock_receipt(self.app,self.token,arguments)
            elif name=='wechat.get_operation':
                cmd=OperationRead.model_validate(arguments)
                with self.app.store.transaction():
                    family,_=self.app.auth(self.token,roles)
                    value=self.app.store.get('operation',cmd.operation_id,family)
            else:
                handler={
                    'wechat.receive_message':self.app.ingest_message,'message.interpret':self.app.interpret,
                    'control.apply':self.app.apply_control,'planning.record_pending':self.app.record_pending,
                    'progress.submit':self.app.submit_report,'planner.commit_daily':self.app.commit_daily,
                    'planner.commit_progress':self.app.commit_progress,'planner.commit_admission':self.app.commit_admission,
                }[name]
                value=handler(self.token,arguments)
            return {'ok':True,'result':value}
        except ValidationError as exc:
            return {'ok':False,'error':{'code':'INVALID_SCHEMA','details':[
                {'loc':list(e['loc']),'type':e['type'],'message':e['msg']} for e in exc.errors(include_input=False,include_url=False)]}}
        except BusinessError as exc:
            return {'ok':False,'error':{'code':exc.code,'message':exc.message}}
