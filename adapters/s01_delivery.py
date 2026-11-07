"""S01 eligibility and durable delivery controls, independent of model wording."""
import datetime as dt
import hashlib
import json
import sqlite3
import time
from contextlib import closing
import sql_tools as s
import test_mode as tm


def check(day):
    at=tm.clock(); today=at.date().isoformat()
    if day!=today:return {'status':'unknown','reason':'not_current_business_day'}
    import course_service
    snap=tm.snapshot(day)
    profile=snap['profile']
    binding={k:profile.get(k) for k in ('class_type','cohort')}
    if not all(binding.values()):return {'status':'unknown','reason':'班型或期次尚未核实'}
    schedule=course_service.course_schedule(day,**binding)
    if schedule['status']!='scheduled':
        return {'status':'not_eligible' if schedule['status'] in ('no_lesson','not_started') else 'unknown','reason':schedule['reason'],'schedule':schedule}
    from s01_contract import lesson_state
    result=lesson_state(schedule,snap,day)
    if result['status']!='eligible':return result
    # Use the bound version's contract, so historical 07:00 configurations
    # keep their own lower bound when replayed against this code.
    import service_workflow
    hour = service_workflow.planning_contract('remind-reading').get('fixed_time','06:30')
    if at.strftime('%H:%M')<hour:return {'status':'not_due','reason':'尚未到课前提醒时间'}
    if not __import__('service_hours').is_open(__import__('service_hours').now()):return {'status':'not_eligible','reason':'每日22:00停止主动服务，不顺延至次日。'}
    return {**result,'delivery_blocked':False,'reason':'今日有应学课程且未完成'}


def deliver(day, body):
    import agent_plan as ap
    import wechat_test as w
    if not ap.enabled():return {'status':'disabled','reason':'主动服务未启用'}
    ap.guard_delivery(s01=True)
    at=tm.clock()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        current=check(day)
        if current['status']!='eligible':return {'status':'skipped','reason':current['reason'],'evidence':current}
        if c.execute('SELECT 1 FROM contact_constraints WHERE day=?',(day,)).fetchone():
            return {'status':'skipped','reason':'用户要求暂停当日主动联系'}
        lesson_ids=sorted(x['lesson_id'] for x in current['evidence'])
        key=tm.state()['run_id']+':S01:'+day+':'+hashlib.sha256('|'.join(lesson_ids).encode()).hexdigest()[:16]
        key=__import__('message_hooks').draft_key(c,key)
        prior=c.execute('SELECT status,body FROM deliveries WHERE action_key=?',(key,)).fetchone()
        if prior:
            result={'status':prior[0],'duplicate':True,'action_key':key,'body':prior[1]}
        else:
            raw=c.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone()
            gate=json.loads(raw[0]) if raw else {}
            waiting=c.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched')").fetchone()
            if gate.get('active') or gate.get('until',0)>time.time() or waiting:
                return {'status':'deferred','reason':'当前对话优先'}
            policy=c.execute("SELECT body FROM proactive_settings WHERE key='contact'").fetchone()
            gap=json.loads(policy[0])['min_gap_minutes'] if policy else 120
            stamps=[dt.datetime.fromisoformat(x[0]) for x in c.execute("SELECT created_at FROM deliveries WHERE action_key NOT LIKE 'reply:%' AND status<>'superseded'")]
            if stamps and (at-max(stamps)).total_seconds()<gap*60:
                retry=max(stamps)+dt.timedelta(minutes=gap)
                if retry.date()!=at.date() or not __import__('service_hours').is_open(retry):return {'status':'skipped','reason':'当日联系窗口已结束，不顺延至次日'}
                return {'status':'deferred','retry_at':retry.isoformat(),'reason':'联系间隔尚未满足'}
            from s01_contract import preparation
            trace=[{'name':x['name'],'arguments':json.loads(x['input']),'result':json.loads(x['output'] or '{}')} for x in ap.tool_calls()]
            reason=preparation(trace,current['schedule'],day)
            if reason:return {'status':'needs_revision','reason':reason}
            actual=body.strip()
            from message_text import parts
            try:parts(actual)
            except ValueError as error:return {'status':'needs_revision','reason':str(error)}
            import s01_reading_guides as guides
            guide=None
            if guides.required():
                selected=guides.select(current['schedule'])
                if selected['status']!='ready':return {'status':'skipped','reason':selected['reason'],'evidence':{'status':'unknown','reading_guide':selected}}
                if not (__import__('experiment_runtime').manifest() or __import__('manual_workbench').verify() or __import__('family_lab_bridge').verify()):return {'status':'skipped','reason':'导读单真实渠道尚未配置','evidence':{'status':'unknown'}}
                __import__('preference_memory').guard_media(c,6)
                guide=selected['asset']
            __import__('service_workflow').bind_delivery(c,key)
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,actual,'queued',at.isoformat(),'test_wechat_pending'))
            w.queue_text(c,key,actual)
            if guide is not None:guides.bind(c,key,day,guide)
            result={'status':'queued','action_key':key,'body':actual}
    ap.link_delivery(result)
    w.flush()
    result['status']=s.read('SELECT status FROM deliveries WHERE action_key=?',(key,))[0]['status']
    return result
