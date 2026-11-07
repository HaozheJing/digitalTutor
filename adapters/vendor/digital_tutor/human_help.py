"""Durable internal help requests. No external notification or human acceptance."""
from typing import Literal
from pydantic import Field
from .domain import Strict, ID, Positive, require
from .storage import digest

READ_ROLES={'service','planner_daily','planner_progress','admin'}
WRITE_ROLES={'service','planner_daily','planner_progress'}

class SourceRef(Strict):
    kind: Literal['message','event','pending']
    id: ID
    version: Positive

class TelephoneAppointment(Strict):
    requested_start_at: str = Field(description='家长希望电话开始的时间，含时区；不是已确认预约。')
    original_time_text: str = Field(min_length=1,max_length=200,description='原消息中表达希望时段的连续原文。')

class HelpRequest(Strict):
    appointment: TelephoneAppointment | None = None
    request_id: ID
    reason_code: Literal['missing_material','missing_data','conflicting_information','permission_blocked','runtime_failure','telephone_appointment','other']
    summary: str=Field(min_length=1,max_length=1600)
    missing_items: list[str]=Field(min_length=1,max_length=20)
    source_refs: list[SourceRef]=Field(min_length=1,max_length=20)

class HelpRead(Strict):
    ticket_id: ID | None=None
    status: Literal['pending','resolved','all']='pending'
    offset: int=Field(default=0,ge=0)
    limit: int=Field(default=20,ge=1,le=100)

class HelpResolve(Strict):
    request_id: ID
    ticket_id: ID
    expected_version: Positive
    resolution: str=Field(min_length=1,max_length=4000)
    resolver: str=Field(min_length=1,max_length=120)

def validate_sources(app,family,binding,refs):
    job=app.store.get('job',binding['job_id'],family) if binding.get('job_id') else {}
    mids=set(binding.get('reply_scope',{}))|set(job.get('coalesced_message_ids',[]))
    if job.get('message_id'):mids.add(job['message_id'])
    events=set(binding.get('event_ids',[]))
    if job.get('event_id'):events.add(job['event_id'])
    if binding['role'].startswith('planner'):events.update(binding.get('event_versions',{}))
    for ref in refs:
        row=app.store.get(ref.kind,ref.id,family)
        require(row['version']==ref.version,'EVIDENCE_SUPERSEDED','人工请求须关联当前真实记录')
        if not app.strict_runtime:continue
        allowed=(ref.kind=='message' and ref.id in mids or ref.kind=='event' and ref.id in events or
                 ref.kind=='pending' and (ref.id==binding.get('pending_id') or row.get('source_message_id') in mids))
        require(allowed,'HUMAN_HELP_SCOPE','人工请求来源不属于当前任务')

def request_help(app,token,args):
    cmd=HelpRequest.model_validate(args)
    require(all(s.strip() and len(s)<=1000 for s in cmd.missing_items),'INVALID_SCHEMA','资料缺口须为非空短文字')
    def apply(family,binding):
        require(not app.strict_runtime or binding.get('run_id') and binding.get('job_id'),
                'RUNTIME_BINDING_REQUIRED','人工请求须绑定本轮任务')
        validate_sources(app,family,binding,cmd.source_refs)
        if cmd.reason_code=='telephone_appointment':
            from .domain import timestamp
            require(cmd.appointment is not None,'APPOINTMENT_TIME_REQUIRED','电话邀约需保留家长期望时段')
            require(timestamp(cmd.appointment.requested_start_at)>app.clock.now(),'APPOINTMENT_TIME_INVALID','期望电话时间必须晚于当前时间')
            require(any(ref.kind=='message' and cmd.appointment.original_time_text in app.store.get('message',ref.id,family).get('body','') for ref in cmd.source_refs),'APPOINTMENT_SOURCE_REQUIRED','预约时段原文必须来自当前请求消息')
        else:require(cmd.appointment is None,'APPOINTMENT_REASON_REQUIRED','电话预约须使用telephone_appointment原因')
        # Different request IDs/runs cannot create duplicate tickets for the same
        # family, source and gap. A resolved ticket stays resolved, not reopened.
        key='human:'+digest({'family':family,'reason':cmd.reason_code,
            'sources':sorted({(r.kind,r.id) for r in cmd.source_refs}),
            'missing':([] if cmd.reason_code=='telephone_appointment' else sorted({s.strip() for s in cmd.missing_items})),**({'appointment_at':cmd.appointment.requested_start_at} if cmd.appointment else {})})
        old=next((r for r in app.store.list('human_request',family) if r['id']==key),None)
        if old:return old
        return app.store.put('human_request',key,family,{
            **({'appointment':cmd.appointment.model_dump(),'appointment_status':'awaiting_human_confirmation'} if cmd.appointment else {}),
            'status':'pending','reason_code':cmd.reason_code,'summary':cmd.summary,
            'missing_items':cmd.missing_items,'source_refs':[r.model_dump() for r in cmd.source_refs],
            'origin_job_id':binding.get('job_id'),'origin_run_id':binding.get('run_id'),
            'requested_at':app.clock.iso(),'notification_status':'not_configured','notified':False,
            'accepted_by_human':False,'resolution':None,
            'contract':'仅已登记内部待处理请求，未通知或交接给真人；原任务和未答责任继续保留。'})
    return app.write(token,WRITE_ROLES,'human.request_help',cmd.model_dump(),apply)

def get_requests(app,token,args):
    cmd=HelpRead.model_validate(args)
    with app.store.transaction():
        family,_=app.auth(token,READ_ROLES)
        if cmd.ticket_id:return app.store.get('human_request',cmd.ticket_id,family)
        rows=[r for r in app.store.list('human_request',family) if cmd.status=='all' or r['status']==cmd.status]
        end=cmd.offset+cmd.limit
        return {'items':rows[cmd.offset:end],'has_more':end<len(rows),'next_offset':end if end<len(rows) else None,
                'notification_adapter':'not_configured','scope':'current_family'}

def resolve_request(app,token,args):
    """Future integration seam: trusted admin only; never exposed as an LLM tool.

    Record a human-provided result and wake re-evaluation via dependency version.
    This does not send a message, settle an intent or complete an event.
    """
    cmd=HelpResolve.model_validate(args)
    def apply(family,binding):
        row=app.store.get('human_request',cmd.ticket_id,family)
        require(row['version']==cmd.expected_version,'STALE_EVENT','人工请求版本已变化')
        require(row['status']=='pending','HUMAN_HELP_CLOSED','该人工请求已处理')
        return app.store.put('human_request',row['id'],family,{**row,'status':'resolved',
            'resolution':cmd.resolution,'resolver':cmd.resolver,'resolved_at':app.clock.iso(),
            'resolution_authority':'human_supplied_input_requires_service_verification'},row['version'])
    return app.write(token,{'admin'},'human.resolve_request',cmd.model_dump(),apply)

def relevant(app,family,binding):
    mids=set(binding.get('reply_scope',{}));events=set(binding.get('event_ids',[]))
    if binding.get('job_id'):
        job=app.store.get('job',binding['job_id'],family)
        mids.update(job.get('coalesced_message_ids',[]));mids.add(job.get('message_id'));events.add(job.get('event_id'))
    return [r for r in app.store.list('human_request',family) if r.get('origin_job_id')==binding.get('job_id') and binding.get('job_id')
        or any(s['kind']=='message' and s['id'] in mids or s['kind']=='event' and s['id'] in events
               or s['kind']=='pending' and s['id']==binding.get('pending_id') for s in r['source_refs'])]

SPECS={
 'human.request_help':(WRITE_ROLES,HelpRequest,'家长请求电话预约时用telephone_appointment，填写appointment期望时段及原文，先受理不直接拒绝；也支持资料不足、来源矛盾、权限受阻或反复失败时登记内部人工请求。返回真实编号并去重；只入队，尚未接通通知渠道，不代表已通知、有人接手或问题解决。保留原责任，用waiting/needs_input记录缺口。source_refs引用本轮消息、事项或待办的当前版本。'),
 'human.get_requests':(READ_ROLES,HelpRead,'读取当前家庭的人工请求状态和人工补充内容；补充内容仍需核验，已处理请求不代表原任务已交付。'),
}
HANDLERS={'human.request_help':request_help,'human.get_requests':get_requests}
