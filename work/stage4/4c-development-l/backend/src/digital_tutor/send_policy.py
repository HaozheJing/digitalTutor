"""Simulation contact policy and final checks shared by preparation and dispatch."""
from dataclasses import dataclass
from datetime import timedelta
from .domain import require
from .jobs import enabled
from .control_scope import control_event_id


@dataclass(frozen=True)
class ContactPolicy:
    proactive_per_day: int = 2
    reminders_per_lesson_week: int = 2
    policy_kind: str = 'simulation'


def action_paused(app, family, action, event_id):
    return any(c['status']=='active' and action in c['action_kinds'] and control_event_id(c) in (None,event_id)
               for c in app.store.list('control',family))


def check_send(app, family, cmd, exclude_operation=None, capability=None):
    require(enabled(app),'RUNTIME_DISABLED','执行已停用')
    cap = capability or {}
    if cap.get('run_id'):
        active=app.store.connection.execute("SELECT 1 FROM sessions WHERE family_id=? AND role='service' AND active=1 AND json_extract(binding,'$.run_id')=?",(family,cap['run_id'])).fetchone()
        require(active is not None,'RUN_REVOKED','原运行能力已撤销，旧操作不能发起')
        closed=app.store.connection.execute("SELECT 1 FROM records WHERE kind='run_outcome' AND family_id=? AND id=?",(family,cap['run_id'])).fetchone()
        require(closed is None,'OUTCOME_FINALIZED','内部结果已提交，运行不能新增发送')
    strict = getattr(app,'strict_runtime',False)
    if strict and cmd.reply_to:
        from .request_responsibility import check_reply
        check_reply(app,family,cmd, operation=app.store.get('operation',exclude_operation,family) if exclude_operation else None)
    modes = {getattr(app,'mode','full'), cap.get('mode')}
    reply_only = bool(modes & {'passive','daily'})
    require(not cap.get('no_contact') and cap.get('output_mode')!='teacher_draft', 'NO_CONTACT_RUN', '内部任务禁止外发')
    if strict or reply_only:
        require(bool(cap.get('run_id')), 'SEND_CAPABILITY_REQUIRED', '旧操作缺少本次授权范围')
        if reply_only:
            code = 'PASSIVE_MODE' if 'passive' in modes else 'DAILY_MODE'
            require(bool(cmd.reply_to) and not cmd.event_id, code, '当前运行模式只允许授权待答回复，禁止主动外发')
        if cmd.reply_to:
            scope = cap.get('reply_scope') or {}
            for ref in cmd.reply_to:
                allowed = scope.get(ref.message_id, [])
                require('*' in allowed or ref.intent_id in allowed, 'REPLY_SCOPE', '回复不属于本次授权待答范围')
                msg = app.store.get('message',ref.message_id,family)
                intent = next((i for i in msg['intents'] if i['intent_id']==ref.intent_id),None)
                require(intent and intent['requires_reply'], 'REPLY_SCOPE', '该意图没有待答责任')
            from .service_context import require_reply_messages_read
            require_reply_messages_read(app,family,cmd,cap)
    now=app.clock.now()
    require(app.service_start<=now.hour<app.service_end,'WINDOW_CLOSED','当前不在常规对外窗口')
    require(not action_paused(app,family,cmd.action_kind,cmd.event_id),
            'CONTROL_ACTIVE','此动作处于暂停范围')
    if cmd.event_id:
        event=app.event(family,cmd.event_id)
        require(event['status'] in {'pending','in_progress'} and not event['update_pending'],'UPDATE_PENDING','事项等待、结束或待更新')
        if strict:
            from .scheduling import require_step_authority, step_key
            require_step_authority(app,family,event,cap)
            require(cmd.step_id==step_key(event),'STALE_STEP','主动发送须引用本轮已提交步骤')
            require(cmd.action_kind==event['next_action']['action_kind'],
                    'STEP_SCOPE_VIOLATION','外发动作必须与当前已提交步骤一致')
            require(not any(op['id']!=exclude_operation and op.get('event_id')==event['id']
                            and op['stage'] in {'queued','dispatching','accepted','unknown'}
                            for op in app.store.list('operation',family)),
                    'OPERATION_UNCERTAIN','原事项仍有待核实副作用，须核原操作不能新发')
        else:
            plans=[p for p in app.store.list('plan',family) if p['business_date']==now.date().isoformat()]
            require(plans and any(x['event_id']==cmd.event_id for x in plans[0]['items']),'NOT_IN_DAILY_PLAN','不在今日主动范围')
        require(not any(m['direction']=='inbound' and not m['interpreted'] for m in app.store.list('message',family)),'INPUT_UNRESOLVED','新来信尚未理解')
        if cmd.step_id:
            from .scheduler import step_key
            require(event['next_action'] and cmd.step_id==step_key(event),'STALE_STEP','原步骤已改变')
    for ref in cmd.reply_to:
        msg=app.store.get('message',ref.message_id,family)
        require(msg['direction']=='inbound' and msg['interpreted'],'INPUT_UNRESOLVED','先解释原消息')
        intent=next((i for i in msg['intents'] if i['intent_id']==ref.intent_id),None)
        require(intent is not None,'INVALID_TARGET','回复意图不存在')
        require(not intent['reply_operation_refs'],'ALREADY_ANSWERED','该意图已有实际回复')
        for prior in app.store.list('operation',family):
            if prior['id']!=exclude_operation and any(x['message_id']==ref.message_id and x['intent_id']==ref.intent_id for x in prior.get('reply_to',[])) and prior['stage'] in {'queued','unknown','accepted','dispatching'}:
                require(False,'OPERATION_UNCERTAIN','该意图已有待核实回复')


def reserve_contact(app, family, cmd):
    """Checked in the preparation transaction; queued/unknown operations consume quota."""
    if not cmd.event_id:
        return []
    policy=getattr(app,'contact_policy',ContactPolicy())
    require(policy.policy_kind=='simulation','POLICY_UNCONFIRMED','当前仅支持明确标记的模拟频控')
    now=app.clock.now(); today=now.date().isoformat()
    charged=[op for op in app.store.list('operation',family) if op.get('event_id') and op['stage']!='cancelled']
    require(sum(op.get('business_date',op['occurred_at'][:10])==today for op in charged)<policy.proactive_per_day,'CONTACT_LIMIT','模拟日触达额度不足')
    lessons=app.event(family,cmd.event_id)['completion']['lesson_refs']
    if cmd.action_kind=='remind_makeup':
        week=(now.date()-timedelta(days=now.weekday())).isoformat()
        for lesson in lessons:
            count=sum(op.get('business_date',op['occurred_at'][:10])>=week and lesson in op.get('lesson_refs',[]) and op['action_kind']=='remind_makeup' for op in charged)
            require(count<policy.reminders_per_lesson_week,'CONTACT_LIMIT','模拟原课节周触达额度不足')
    return lessons
