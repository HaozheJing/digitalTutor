"""JSON-callable registry. Host binds a capability, never model-controlled roles."""
from pydantic import ValidationError

from .domain import BusinessError, Query, MessageRead, MessageReadAll, MessageReadStream, Send, Inbound, Interpret, ControlCommand, PendingInput, Report, DailyCommit, AdmissionCommit, ProgressCommit, OperationRead, Receipt
from .learning import query_learning, PUBLIC_SCHEMA, SCHEMA_NOTES
from .wechat import read_messages, read_all_messages, read_message_stream, send_message, mock_receipt
from .family_memory import SPECS as MEMORY_SPECS, HANDLERS as MEMORY_HANDLERS
from .daily_memory import SPECS as DAILY_SPECS, HANDLERS as DAILY_HANDLERS
from .learning_summary import SPECS as SUMMARY_SPECS, HANDLERS as SUMMARY_HANDLERS
from .context import SPECS as CONTEXT_SPECS, HANDLERS as CONTEXT_HANDLERS
from .knowledge import SPECS as KNOWLEDGE_SPECS
from .skill_loader import SPECS as SKILL_SPECS


SPECS={
    'state.read': ({'service','planner_daily','planner_progress','admin'},None,'读取当前家庭事项、控制、报告和版本'),
    'learning.schema': ({'service','planner_daily','planner_progress','admin'},None,'返回当前家庭SQL表、字段及说明；learning_batches含可查询课程绑定、导入及覆盖元数据'),
    'learning.query_sql': ({'service','planner_daily','planner_progress','admin'},Query,'在当前家庭uid隔离只读数据集查询原始学情与learning_batches课程绑定/覆盖元数据。动态筛选值使用 :命名参数 与parameters映射，不将用户或来源值拼入SQL。source_index仅定位及提供有界JSON对象顶层字段名，不含值；内容需实际查询'),
    'wechat.read_all_messages': ({'service','planner_daily','planner_progress','admin'},MessageReadAll,'全量读取当前家庭已到达的聊天快照；包括较早未答原文，不会标记已答。超过10000条明确拒绝而非截断'),
    'wechat.read_message_stream': ({'service','planner_daily','planner_progress','admin'},MessageReadStream,'按after_seq分页读取已到达消息。工作流模式默认scope=current_day，优先当天消息；旧聊天用resource.read按主题检索，必要时显式scope=all_history遍历。has_more时继续next_cursor，不把未读历史当作已核对，不标记已答'),
    'wechat.send_message': ({'service'},Send,'模拟发送，二选一：答疑或已定安排确认用reply+reply_to，省略event_id/part_id/step_id；实际主动交付用event_id+step_id，省略reply_to。安排确认不等于交付完成。顶层没有message_id/body_note。核对ok=true及操作stage才可声称送达；ok=false先纠正，不先结束结果。expected_family_version用最新家庭版本'),
    'wechat.receive_message': ({'channel','admin'},Inbound,'仅测试渠道接收入站消息'),
    'wechat.mock_receipt': ({'channel','admin'},Receipt,'注入原模拟操作回执'),
    'wechat.get_operation': ({'service','planner_daily','planner_progress','admin'},OperationRead,'按原操作号核对结果；Service/admin查询渠道并合并原回执，Planner读取本地快照；从不重新发送'),
    'message.interpret': ({'service'},Interpret,'一次只解释一条消息，首次登记本条全部意图。遗漏的独立意图可在此来源尚无业务效果时用append_new_intents=true、只传新intent_id追加，原意图不改。多意图逐个引用本条source_excerpt。未来独立交付用future_delivery及原授权；解释不代表已答'),
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
from .reschedule_response import SPECS as CHANGE_SPECS, HANDLERS as CHANGE_HANDLERS
from .resource_tools import SPECS as RESOURCE_SPECS, HANDLERS as RESOURCE_HANDLERS
from .action_completion import SPECS as ACTION_SPECS, HANDLERS as ACTION_HANDLERS
from .human_help import SPECS as HUMAN_SPECS, HANDLERS as HUMAN_HANDLERS
from .delivery_accounting import SPECS as ACCOUNT_SPECS, HANDLERS as ACCOUNT_HANDLERS
from .family_profile import SPECS as PROFILE_SPECS, HANDLERS as PROFILE_HANDLERS
SPECS.update(PROFILE_SPECS)
SPECS.update(ACCOUNT_SPECS)
SPECS.update(HUMAN_SPECS)
SPECS.update(ACTION_SPECS)
SPECS.update(RESOURCE_SPECS)
SPECS.update(CHANGE_SPECS)
SPECS.update(REQUEST_SPECS)
SPECS.update(CANDIDATE_SPECS)
SPECS.update(OUTCOME_SPECS)
SPECS.update(MEMORY_SPECS)
SPECS.update(DAILY_SPECS)
SPECS.update(SUMMARY_SPECS)
SPECS.update(CONTEXT_SPECS)
SPECS.update(KNOWLEDGE_SPECS)
SPECS.update(SKILL_SPECS)
from .media_generation import SPECS as MEDIA_SPECS, HANDLERS as MEDIA_HANDLERS
SPECS.update(MEDIA_SPECS)


class ToolRegistry:
    def __init__(self, app, token):
        self.app,self.token=app,token

    def describe(self):
        result=[]
        for name,(roles,model,description) in SPECS.items():
            if name in RESOURCE_SPECS and getattr(self.app,'context_mode',None)!='daily_native':continue
            if name=='wechat.read_all_messages' and getattr(self.app,'context_mode',None)=='daily_native':continue
            try:
                self.app.auth(self.token,roles)
            except BusinessError:
                continue
            if name in {'planner.commit_daily','planner.commit_admission'}:
                _, binding = self.app.auth(self.token, roles)
                admission = binding.get('planning_reason') == 'admission'
                if (name == 'planner.commit_admission') != admission: continue
            schema=model.model_json_schema() if model else {'type':'object','additionalProperties':False,'properties':{}}
            if name=='memory.save' and getattr(self.app,'context_mode',None)=='daily_native':
                schema['required']=list(dict.fromkeys([*schema.get('required',[]),'memory_id','expected_version']))
                description='仅依据新原文即时更正已有记忆，必须提供memory_id及expected_version。普通新偏好由日终统一整理，当天原文已保留，不调用此工具创建。更正成功会重建当前任务上下文。'
            result.append({'name':name,'description':description,'input_schema':schema})
        return result

    def call(self,name,arguments):
        try:
            if not isinstance(arguments, dict):
                raise BusinessError('INVALID_SCHEMA','工具参数必须是JSON对象')
            if getattr(self.app,'outcome_repair_only',False):
                from .outcome_recovery import READ_TOOLS
                if name not in READ_TOOLS and name!='service.record_outcome':
                    raise BusinessError('OUTCOME_REPAIR_ONLY','回执补交阶段仅允许只读核对与真实结束回执；禁止重复业务写入')
            # Python-call compatibility for frozen fixtures; deliberately not a
            # third model tool/schema. Native Hermes sees only the two new tools.
            if name == 'wechat.read_messages':
                self.app.auth(self.token, {'service','planner_daily','planner_progress','admin'})
                return {'ok': True, 'result': read_messages(self.app, self.token, arguments)}
            if name not in SPECS:
                raise BusinessError('TOOL_NOT_FOUND','工具不存在')
            roles,_,_=SPECS[name]
            family,binding=self.app.auth(self.token,roles,allow_inactive=True)
            if name=='memory.save' and getattr(self.app,'context_mode',None)=='daily_native' and not arguments.get('memory_id'):
                raise BusinessError('MEMORY_DAILY_REQUIRED','普通新记忆由日终整理；当天原文已完整保留，请继续回答。此工具仅即时更正已有记忆。')
            readonly={'family.get_profile','state.read','learning.schema','learning.query_sql','wechat.read_all_messages','wechat.read_message_stream','wechat.get_operation',
                      'context.read','memory.read','knowledge.list','knowledge.read','skills.list','skills.read','service.record_outcome','resource.read','human.get_requests'}
            if name not in readonly and binding.get('run_id'):
                finalized=self.app.store.connection.execute("SELECT 1 FROM records WHERE kind='run_outcome' AND family_id=? AND id=?",(family,binding['run_id'])).fetchone()
                if finalized:
                    raise BusinessError('OUTCOME_FINALIZED','已提交内部结果，不能继续业务写入')
            if name in PROFILE_HANDLERS:
                return {'ok':True,'result':PROFILE_HANDLERS[name](self.app,self.token,arguments)}
            if name in MEDIA_HANDLERS:
                return {'ok':True,'result':MEDIA_HANDLERS[name](self.app,self.token,arguments)}
            if name in ACTION_HANDLERS:
                return {'ok':True,'result':ACTION_HANDLERS[name](self.app,self.token,arguments)}
            if name in HUMAN_HANDLERS:
                return {'ok':True,'result':HUMAN_HANDLERS[name](self.app,self.token,arguments)}
            if name in ACCOUNT_HANDLERS:
                return {'ok':True,'result':ACCOUNT_HANDLERS[name](self.app,self.token,arguments)}
            if name in RESOURCE_HANDLERS:
                self.app.auth(self.token,roles)
                require_mode=getattr(self.app,'context_mode',None)=='daily_native'
                if not require_mode:raise BusinessError('TOOL_NOT_FOUND','当前模式未启用此工具')
                return {'ok':True,'result':RESOURCE_HANDLERS[name](self.app,self.token,arguments)}
            if name in SUMMARY_HANDLERS or name in DAILY_HANDLERS or name in MEMORY_HANDLERS or name in CONTEXT_HANDLERS or name in OUTCOME_HANDLERS or name in CANDIDATE_HANDLERS or name in REQUEST_HANDLERS or name in CHANGE_HANDLERS:
                value={**SUMMARY_HANDLERS, **DAILY_HANDLERS, **MEMORY_HANDLERS, **CONTEXT_HANDLERS, **OUTCOME_HANDLERS, **CANDIDATE_HANDLERS, **REQUEST_HANDLERS, **CHANGE_HANDLERS}[name](self.app,self.token,arguments)
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
            elif name=='wechat.read_all_messages':
                value=read_all_messages(self.app,self.token,arguments)
            elif name=='wechat.read_message_stream':
                value=read_message_stream(self.app,self.token,arguments)
            elif name=='wechat.send_message':
                value=send_message(self.app,self.token,arguments)
            elif name=='wechat.mock_receipt':
                value=mock_receipt(self.app,self.token,arguments)
            elif name=='wechat.get_operation':
                cmd=OperationRead.model_validate(arguments)
                with self.app.store.transaction():
                    family,binding=self.app.auth(self.token,roles)
                    value=self.app.store.get('operation',cmd.operation_id,family)
                # Query only this original operation outside the business transaction.
                # A read can reconcile an observed receipt, never dispatch or retry.
                from .outbox import reconcile
                if binding.get('role') in {'service','admin'}:
                    value=reconcile(self.app,family,cmd.operation_id)
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
                {'loc':list(e['loc']),'type':e['type'],'message':e['msg']} for e in exc.errors(include_input=False,include_url=False)],
                'input_schema':SPECS[name][1].model_json_schema() if name in SPECS and SPECS[name][1] else None}}
        except BusinessError as exc:
            error={'code':exc.code,'message':exc.message}
            if exc.code in {'SOURCE_EXCERPT_INCOMPLETE','ALREADY_INTERPRETED','INTERPRETATION_SOURCE_LOCKED'} and isinstance(arguments,dict):
                from .tool_recovery import source_hint
                hint=source_hint(self.app,self.token,arguments)
                if hint:error['recovery']=hint
            if name=='wechat.send_message' and exc.code=='INVALID_TARGET':
                error['recovery']={'send_was_accepted':False,'automatic_retry':False,
                    'contract':'本次目标校验拒绝，未进入发送。来信答复/安排确认用action_kind=reply、reply_to，省略event_id/part_id/step_id；主动交付才使用event_id/step_id/part_id并省略reply_to。先核对本轮权限和来源再自行纠正；未成功不能说已确认。service.record_outcome留到所有业务动作之后。'}
            if exc.code in {'STALE_INPUT','STALE_EVENT','EVIDENCE_SUPERSEDED','OUTCOME_INCOMPLETE','RECORD_NOT_FOUND'} and isinstance(arguments,dict):
                from .tool_recovery import version_hint
                hint=version_hint(self.app,self.token,arguments)
                if hint:error['recovery']=hint
            return {'ok':False,'error':error}
