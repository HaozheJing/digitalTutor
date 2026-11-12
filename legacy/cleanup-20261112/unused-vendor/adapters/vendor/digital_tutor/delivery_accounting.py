"""Associate an already verified delivery with explicitly covered original intents.

No send, no plan mutation, no inferred blanket settlement. The model supplies
per-intent source/delivery quotations; the host verifies the completed contract,
the original source, real dispatch, evidence versions and current reply scope.
"""
from pydantic import Field
from .domain import Strict,ID,Positive,require
from .storage import digest

class Coverage(Strict):
    intent_id: ID
    source_excerpt: str=Field(min_length=1,description='该细分要求在原来信中的逐字原文')
    operation_id: ID
    delivery_excerpt: str=Field(min_length=1,description='已实际发送正文中满足该要求的逐字片段，不是新草稿')
    reason: str=Field(min_length=1,description='说明该段实际交付如何满足此意图；无证据的意图不要传入')

class AccountDelivery(Strict):
    request_id: ID
    event_id: ID
    expected_event_version: Positive
    message_id: ID
    expected_message_version: Positive
    report_key: ID | None=Field(default=None,description='通常省略：宿主查验已应用的真实完成报告。显式填写须用report记录id而非report_id，并同时提供当前expected_report_version')
    expected_report_version: Positive | None=None
    coverage: list[Coverage]=Field(min_length=1,max_length=20)

def account_delivery(app,token,args):
    cmd=AccountDelivery.model_validate(args)
    def apply(family,binding):
        require(binding.get('run_id') and binding.get('job_id') and binding.get('lease'),'RUNTIME_BINDING_REQUIRED','核销须绑定原Service任务')
        event=app.event(family,cmd.event_id);message=app.store.get('message',cmd.message_id,family)
        require(event['version']==cmd.expected_event_version and message['version']==cmd.expected_message_version,'EVIDENCE_SUPERSEDED','核销须引用当前消息与事项版本')
        require((cmd.report_key is None)==(cmd.expected_report_version is None),'INVALID_SCHEMA','报告编号和版本同时提供或同时省略，由宿主核验真实完成报告')
        require(event['status']=='done','DELIVERY_NOT_VERIFIED','须先由Planner按真实交付报告完成原事项，不能凭安排核销')
        from .request_responsibility import deliveries,_delivery_fact
        items=[i for i in deliveries(app,family) if i['event']['id']==event['id'] and i['message']['id']==message['id'] and i['source_current']]
        require(items,'DELIVERY_SOURCE_MISMATCH','只能核销同一原来信中该事项实际覆盖的要求')
        if cmd.report_key:
            report=app.store.get('report',cmd.report_key,family)
            require(report['version']==cmd.expected_report_version,'EVIDENCE_SUPERSEDED','报告版本过期；重读，或同时省略report_key和expected_report_version由宿主选择真实完成报告')
            fact=_delivery_fact(app,family,items[0],event,report)
        else:
            # The Service read projection intentionally hides old Planner rows.
            # Derive only proof of an already completed transaction; no decision
            # or content coverage is inferred and no records are manufactured.
            fact=None
            for report in app.store.list('report',family):
                if event['id'] in report.get('event_ids',[]) and report.get('resolution')=='applied':
                    fact=_delivery_fact(app,family,items[0],event,report)
                    if fact:break
        require(fact,'DELIVERY_NOT_VERIFIED','须有已应用的完成判断和原授权下真实送达的证据；可省略报告编号与版本由宿主查验')
        from .human_help import relevant
        require(not any(r['status']=='pending' for r in relevant(app,family,binding)),'HUMAN_HELP_PENDING','人工缺口未解决，不能核销')
        scope=binding.get('reply_scope',{}).get(message['id'],[])
        require(len({c.intent_id for c in cmd.coverage})==len(cmd.coverage),'INVALID_SCHEMA','每个意图只核销一次')
        covered=[]
        for c in cmd.coverage:
            intent=next((i for i in message['intents'] if i['intent_id']==c.intent_id),None)
            require(intent and intent['requires_reply'] and not intent['reply_operation_refs'] and ('*' in scope or c.intent_id in scope),'REPLY_SCOPE','不属于本轮未答意图')
            require(c.source_excerpt.strip() and c.source_excerpt in message['body'],'DELIVERY_COVERAGE_INVALID','来源引文不在原来信')
            require(c.operation_id in fact['operation_ids'],'DELIVERY_COVERAGE_INVALID','操作不是已核验的原事项交付')
            op=app.store.get('operation',c.operation_id,family)
            require(c.delivery_excerpt.strip() and c.delivery_excerpt in op['body'],'DELIVERY_COVERAGE_INVALID','交付引文不在实际发送正文')
            covered.append((intent,c))
        key='delivery-accounting:'+digest([family,binding['run_id'],cmd.request_id])
        for intent,c in covered:
            intent['reply_operation_refs']=[c.operation_id]
            intent['delivery_accounting_ref']=key
        app.store.put('message',message['id'],family,message,message['version'])
        return app.store.put('delivery_accounting',key,family,{'origin_run_id':binding['run_id'],'job_id':binding['job_id'],
            'event_id':event['id'],'message_id':message['id'],'coverage':[c.model_dump() for _,c in covered],
            'verified_delivery':fact,'recorded_at':app.clock.iso(),'new_send':False,
            'authority':'model_per_intent_coverage_with_host_verified_original_delivery'})
    return app.write(token,{'service'},'service.account_delivery_reply',cmd.model_dump(),apply)

SPECS={'service.account_delivery_reply':({'service'},AccountDelivery,
    '仅内部核销：同一原来信拆出多个要求，而原事项已真实交付且Planner结案时，逐项引用已发送正文与原文，关联已有回执，解决余下未答意图。不新发消息、不重复报告、不自动核销全部意图。state.read或resource.read取得当前消息、done事项；report_key和expected_report_version通常省略，宿主自动查验真实已应用完成报告。未实际满足的要求继续等待或提请人工。')}
HANDLERS={'service.account_delivery_reply':account_delivery}
