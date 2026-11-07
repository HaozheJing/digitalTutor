"""Model-owned plans, durable test-clock receipts, and native-agent dispatch.

The host supplies checks, never their conclusions or messages. A test-clock
receipt is not presented as a native wall-clock Schedule receipt.
"""
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
import sql_tools as s
import test_mode as tm

FIXED = [
    ('plan','06:30','当日服务规划','plan-today'),
    ('calendar','09:30','日历关怀检查','proactive-service'),
    ('s01','06:30','今日阅读邀请','remind-reading'),
    ('s07','19:00','周三阅读习惯检查','rebuild-reading-rhythm'),
    ('s08','18:00','周五当周收尾检查','close-week-reading-gap'),
    ('interaction','19:00','周互动','invite-reading-interaction'),
    ('festival','09:30','节日图文祝福','send-festival-wishes'),
    ('weekly','09:00','周末补读与周报检查','proactive-service'),
    ('evening','19:00','晚间学情与周月节点检查','proactive-service'),
]
TERMINAL = ('done','no_contact','blocked','expired','cancelled')

class ServiceValidationError(ValueError):
    """Only controlled business precondition messages are safe to expose."""


def initialize():
    import observability
    observability.initialize()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS service_tasks(
          id TEXT PRIMARY KEY,run_id TEXT,day TEXT,kind TEXT,title TEXT,skill TEXT,
          due_at TEXT,expires_at TEXT,candidate_id TEXT,reason TEXT,checks TEXT,
          status TEXT,attempts INTEGER,not_before_wall REAL,created_at TEXT,
          started_at TEXT,finished_at TEXT,result TEXT,native_request_id TEXT);
        CREATE TABLE IF NOT EXISTS service_plan_versions(
          id TEXT PRIMARY KEY,run_id TEXT,day TEXT,created_at TEXT,body TEXT);
        CREATE TABLE IF NOT EXISTS service_skill_reads(
          run_id TEXT,name TEXT,sha256 TEXT,read_at TEXT,PRIMARY KEY(run_id,name));
        CREATE TABLE IF NOT EXISTS service_links(task_id TEXT,action_key TEXT,
          PRIMARY KEY(task_id,action_key));
        CREATE TABLE IF NOT EXISTS service_recurrences(
          run_id TEXT PRIMARY KEY,start_day TEXT,end_day TEXT,hour TEXT,timezone TEXT);
        ''')
        if 'task_id' not in [r[1] for r in c.execute('PRAGMA table_info(tool_audit)')]:
            c.execute('ALTER TABLE tool_audit ADD COLUMN task_id TEXT')


def enabled():
    return bool((tm.state() or {}).get('agent_planning'))


def enable():
    initialize()
    state=tm.state();state['agent_planning']=True
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
    return sync()


def active():
    rows=s.read("SELECT value FROM metadata WHERE key='active_service_task'")
    if not rows:return None
    task_id=json.loads(rows[0]['value'])
    rows=s.read('SELECT * FROM service_tasks WHERE id=? AND run_id=? AND status=?',
                (task_id,tm.state()['run_id'],'running'))
    return rows[0] if rows else None


def insert_task(kind,title,skill,due_at,reason,candidate_id=None,checks='',ident=None,expires_at=None):
    if __import__('service_workflow').enabled() and kind not in __import__('service_workflow').INTERNAL:
        raise ServiceValidationError('新模式业务任务必须来自原子模型规划提交')
    state=tm.state();ident=ident or 'service-'+uuid.uuid4().hex
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('INSERT OR IGNORE INTO service_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (ident,state['run_id'],state['as_of'][:10],kind,title,skill,due_at,
           __import__('service_hours').task_expiry(due_at,expires_at or state['as_of'][:10]+'T23:59:00+08:00'),candidate_id,reason,checks,
           'planned',0,0,s.now(),None,None,None,None))
    return s.read('SELECT * FROM service_tasks WHERE id=?',(ident,))[0]


def sync():
    initialize()
    if not enabled():return {'status':'disabled'}
    state=tm.state();day=state['as_of'][:10]
    if __import__('service_workflow').enabled(state):return __import__('service_workflow').sync()
    reconcile_s01_receipts()
    import fixture_query
    fixture=json.loads(fixture_query.FILE.read_text(encoding='utf-8'))
    start=min(e['course_start_date'] for e in fixture['enrollments'])
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('INSERT OR IGNORE INTO service_recurrences VALUES(?,?,?,?,?)',
                  (state['run_id'],start,fixture['window']['end'],'06:30','Asia/Shanghai'))
    rule=s.read('SELECT * FROM service_recurrences WHERE run_id=?',(state['run_id'],))[0]
    if not rule['start_day']<=day<=rule['end_day']:return {'status':'outside_service_period'}
    for kind,hour,title,skill in FIXED:
        if kind=='s01':hour=__import__('service_workflow').planning_contract(skill).get('fixed_time',hour)
        if kind=='s07' and dt.date.fromisoformat(day).weekday()!=2:continue
        if kind=='s08' and dt.date.fromisoformat(day).weekday()!=4:continue
        if kind=='interaction' and dt.date.fromisoformat(day).weekday()!=4:continue
        if kind=='festival':
            from festival_service import event
            if not event(state):continue
        if kind=='weekly':
            if dt.date.fromisoformat(day).weekday() not in (4,5):continue
            hour='20:00' if dt.date.fromisoformat(day).weekday()==4 else '09:00'
        # Keep legacy receipts on their original day; never replay a completed entry on upgrade.
        if s.read('SELECT 1 FROM service_tasks WHERE id=? AND day=?',
                  ('service-'+state['run_id']+'-'+kind,day)):continue
        insert_task(kind,title,skill,day+'T'+hour+':00+08:00',
                    'Skill 规定的固定检查入口；是否联系、如何安排由智能体核对后决定。',
                    ident='service-'+state['run_id']+'-'+day+'-'+kind)
    sync_completion_signals()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute("UPDATE service_tasks SET status='expired',finished_at=? WHERE run_id=? AND status IN ('planned','deferred') AND expires_at<?",
                  (s.now(),state['run_id'],state['as_of']))
        c.execute("UPDATE service_tasks SET status='blocked',finished_at=?,result=? WHERE run_id=? AND status='deferred' AND attempts>=3",
                  (s.now(),s.encoded({'reason':'已连续三次延期，停止自动恢复；请查看条件与回执后重新评估。'}),state['run_id']))
    return {'status':'ready','run_id':state['run_id']}


def receipt_driven(task):
    if __import__('service_workflow').enabled() and s.read('SELECT 1 FROM service_task_versions WHERE task_id=?',(task.get('id'),)):return True
    return task['kind'] in ('s01','s07','s08','interaction','festival','signal') or (
        task['kind']=='weekly' and dt.date.fromisoformat(task['day']).weekday() in (4,5))


def reconcile_s01_receipts():
    """A pending provider receipt never causes a second send or model run."""
    for task in s.read("SELECT id,kind,day,status,result FROM service_tasks WHERE run_id=? AND kind IN ('s01','s07','s08','weekly','interaction','festival','signal') AND status IN ('awaiting_delivery','deferred','blocked')",(tm.state()['run_id'],)):
        if not receipt_driven(task):continue
        previous=json.loads(task['result'] or '{}')
        missing_finish=previous.get('code')=='missing_finish_receipt' or previous.get('reason')=='模型运行结束但未交付 service_task_finish 回执，请查看调用记录后重新评估。'
        if task['status']=='blocked' and not missing_finish and previous.get('code')!='delivery_unconfirmed':continue
        rows=s.read('SELECT d.action_key,d.status FROM service_links l JOIN deliveries d ON l.action_key=d.action_key WHERE l.task_id=? AND d.status<>"superseded"',(task['id'],))
        if not rows:continue
        state='done' if all(r['status']=='sent' for r in rows) else 'blocked' if any(r['status'] in ('failed','unknown','partial') for r in rows) else 'awaiting_delivery' if all(r['status'] in ('accepted','sent') for r in rows) else None
        if state:
            result=json.loads(task['result'] or '{}');result.update(outcome='completed' if state=='done' else state,deliveries=rows)
            if task['status']=='blocked' and missing_finish:
                result['finish_receipt_missing']=True;result['previous_finish_reason']=previous.get('reason')
            if state=='blocked':result['code']='delivery_unconfirmed'
            elif result.get('code')=='delivery_unconfirmed':result.pop('code')
            result['reason']='本次发送任务已完成；持续目标由事项账本跟踪。' if state=='done' else '平台已受理，等待发送确认，不重新执行发送。' if state=='awaiting_delivery' else '交付未确认，停止并保留回执，不自动重发。'
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute('UPDATE service_tasks SET status=?,result=?,finished_at=? WHERE id=?',(state,s.encoded(result),None if state=='awaiting_delivery' else s.now(),task['id']))


def sync_completion_signals():
    import proactive
    import completion_events
    from completion_service import SKILLS
    completion_events.ingest_fixture()
    state=tm.state()
    proactive.initialize()
    import completion_planner
    if completion_planner.enabled(state):
        completion_planner.sync();return
    decisions=proactive.refresh(state['as_of'][:10])
    current={x['id']:x for x in decisions if x.get('id')}
    with closing(sqlite3.connect(s.DB)) as c,c:
        for task in s.read("SELECT * FROM service_tasks WHERE run_id=? AND kind='signal' AND status IN ('planned','deferred')",(state['run_id'],)):
            item=current.get(task['candidate_id'])
            if item and item['status'] in ('not_eligible','merged_into_higher_priority','already_delivered'):
                c.execute("UPDATE service_tasks SET status='no_contact',finished_at=?,result=? WHERE id=?",
                    (s.now(),s.encoded({'reason':item.get('reason','条件变化，无需重复联系'),'source':'completion_recheck'}),task['id']))
                if state.get('manual_service_task_id')==task['id']:
                    state.pop('manual_service_task_id')
                    c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
    for candidate in decisions:
        if candidate['rule'] not in SKILLS or not candidate.get('evidence',{}).get('event_id'):continue
        if candidate['status']!='eligible':continue
        # Event wakeups provide an entry to the model, not an automatic message or model-authored plan.
        existing=s.read("SELECT 1 FROM service_tasks WHERE run_id=? AND candidate_id=?",
                        (state['run_id'],candidate['id']))
        if existing:continue
        insert_task('signal',candidate['title'],candidate['skill'],candidate['time_window']['earliest'],
                    '已接收完课事件；在规定窗口唤起智能体，重新核验学情、频控和交付条件。',
                    candidate['id'],'重新查询今日学情、当前候选和已有交付；不补造课文或学习效果。',
                    ident='service-'+state['run_id']+'-signal-'+hashlib.sha256(candidate['id'].encode()).hexdigest()[:16],
                    expires_at=candidate['time_window']['latest'])


def request(kind='plan'):
    sync()
    if kind not in ('plan','evening'):raise ServiceValidationError('Unsupported internal task')
    row=insert_task(kind,'重新评估今日计划' if kind=='plan' else '按请求检查当前学情与高光',
                    'plan-today' if kind=='plan' else 'proactive-service',tm.state()['as_of'],
                    '重新检查请求；先读取最新学情，仍须遵守 Skill 和联系约束。')
    return {'status':'queued','task_id':row['id']}


def s10_task(task=None):
    if task is None:
        task=active()
        if not task:
            scope=s.read("SELECT value FROM metadata WHERE key='native_service_content_task'")
            if scope:
                found=s.read('SELECT * FROM service_tasks WHERE id=? AND run_id=?',
                             (json.loads(scope[0]['value']),tm.state()['run_id']))
                task=found[0] if found else None
    return bool(task and (task.get('skill')=='weekly-learning-review' or (
        task['kind']=='weekly' and dt.date.fromisoformat(task['day']).weekday()==5)))


def interaction_task(task=None, skill='invite-reading-interaction'):
    if task is None:
        task=active()
        if not task:
            scope=s.read("SELECT value FROM metadata WHERE key='native_service_content_task'")
            if scope:
                found=s.read('SELECT * FROM service_tasks WHERE id=? AND run_id=?',
                             (json.loads(scope[0]['value']),tm.state()['run_id']))
                task=found[0] if found else None
    return bool(task and task.get('skill')==skill)


def festival_task(task=None):
    return interaction_task(task, 'send-festival-wishes')


def completion_task(task=None):
    from completion_service import SKILLS
    return any(interaction_task(task, skill) for skill in SKILLS)


def zero_task(task=None):
    task=task or active()
    return bool(task and task['kind']=='action' and task['skill']=='support-zero-completion')


def rhythm_task(task=None):
    task = task or active()
    return bool(task and task['kind']=='s07' and task['skill']=='rebuild-reading-rhythm')


def closing_task(task=None):
    task = task or active()
    return bool(task and task['kind']=='s08' and task['skill']=='close-week-reading-gap')


def weekend_task(task=None):
    task=task or active()
    return bool(task and dt.date.fromisoformat(task['day']).weekday()==4 and (
        task['kind']=='weekly' and task['skill'] in ('plan-weekend-makeup','proactive-service') or
        task['kind']=='action' and task['skill']=='plan-weekend-makeup' and task.get('candidate_id')))


def independent_task():
    if active() and active()['kind']=='s01':return True
    if __import__('service_workflow').enabled() and active() and active()['kind']=='plan':return True
    if __import__('completion_planner').internal():return True
    return weekend_task() or closing_task() or rhythm_task() or zero_task() or s10_task() or interaction_task() or festival_task() or completion_task()


def action_skill_names(names):
    from family_lab_bridge import configuration
    if 'reply-style' in names and configuration():
        return [name for name in names if name != 'reply-style']
    return list(names)


def require_action_skills(*names):
    require_skills(*action_skill_names(names))


def excluded_s10_shared_skill(name):
    if name == 'reply-style' and not action_skill_names([name]) and active():
        return {'name':name,'status':'not_applicable','body':'本次主动动作的消息表达使用老师人设。'}
    if __import__('service_workflow').enabled() and active() and active()['kind']=='plan' and name=='service-policy':
        return {'name':name,'status':'not_applicable','body':'内部规划不依赖service-policy'}
    if __import__('completion_planner').internal() and name in ('service-policy','tutor-main','proactive-service','plan-today'):
        return {'name':name,'status':'not_applicable','body':'内部规划与检查使用独立Skill，不依赖service-policy。'}
    if closing_task() and name in ('service-policy','tutor-main','proactive-service','plan-today'):
        return {'name':name,'status':'not_applicable','body':'本次S08仅需动作Skill与reply-style'}
    if rhythm_task() and name in ('service-policy','tutor-main','proactive-service','plan-today'):
        return {'name':name,'status':'not_applicable','body':'本次S07按对应动作Skill和reply-style处理阅读关怀。'}
    if zero_task() and name in ('service-policy','tutor-main','proactive-service','plan-today'):
        return {'name':name,'status':'not_applicable','body':'本次S05仅使用对应动作Skill和reply-style处理初次关怀。'}
    if completion_task() and name in ('service-policy','tutor-main','proactive-service','plan-today'):
        return {'name':name,'status':'not_applicable','body':'本次完课事项只读取对应动作Skill和reply-style，按当前学情独立执行。'}
    if festival_task() and name in ('service-policy','tutor-main','proactive-service','plan-today'):
        return {'name':name,'status':'not_applicable','body':'本次节日祝福使用send-festival-wishes和reply-style独立执行。'}
    if interaction_task() and name in ('service-policy','tutor-main','proactive-service','plan-today'):
        return {'name':name,'status':'not_applicable','body':'本次周互动使用invite-reading-interaction和reply-style独立执行，不加载其他共享规则。'}
    if s10_task() and name in ('service-policy','tutor-main','proactive-service','plan-today'):
        return {'name':name,'status':'not_applicable','body':'本次S10使用weekly-learning-review和reply-style独立执行，不加载其他共享规则。'}
    return None


def load_skill(name):
    import service_content
    import service_catalog
    name=service_catalog.canonical_skill(name)
    excluded=excluded_s10_shared_skill(name)
    if excluded:return excluded
    try:file=service_content.skill_file(name)
    except ValueError as error:raise ServiceValidationError(str(error))
    body=file.read_text(encoding='utf-8');digest=hashlib.sha256(body.encode()).hexdigest()
    bundle=service_content.bundle_digest(name)
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('INSERT OR REPLACE INTO service_skill_reads VALUES(?,?,?,?)',
                  (tm.state()['run_id'],name,bundle,s.now()))
    return {'name':name,'sha256':digest,'bundle_sha256':bundle,'body':body,'revision':service_content.REVISION,
            # The policy bundle is a mandatory dependency. Return its actual
            # attached rules, not just paths, before accepting the new digest.
            'shared_rules':[{'path':str(p.relative_to(file.parent)).replace('\\','/'),
                             'body':p.read_text(encoding='utf-8')}
                            for p in service_content.reference_files(name)] if name=='service-policy' or name in __import__('completion_planner').INTERNAL else [],
            'action_ids':[r[0] for r in service_catalog.ROWS+service_catalog.SUPPLEMENTAL_ROWS if r[2]==name],
            'required_shared_skill':'service-policy' if name!='service-policy' and name not in __import__('completion_service').SKILLS and name not in __import__('completion_planner').INTERNAL and not independent_task() else None,
            'references':[str(p.relative_to(file.parent)).replace('\\','/') for p in service_content.reference_files(name)]}


def require_skills(*names):
    import service_content
    from service_catalog import canonical_skill
    names=tuple(canonical_skill(name) for name in names)
    known={canonical_skill(r['name']) for r in s.read('SELECT name,sha256 FROM service_skill_reads WHERE run_id=?',(tm.state()['run_id'],))
           if service_content.bundle_digest(r['name'])==r['sha256']}
    missing=(set(names)|(set() if independent_task() else {'service-policy'}))-known
    if missing:raise ServiceValidationError('先用 '+('skill_read' if __import__('capability_surface').enabled() else 'service_skill')+' 读取实际 Skill 正文：'+', '.join(sorted(missing)))


def tool_calls(task=None):
    task=task or active()
    if not task:
        events=s.read("SELECT id FROM events WHERE status='dispatched' AND kind='passive'")
        if len(events)!=1:return []
        task={'id':'event:'+events[0]['id']}
    return s.read("SELECT name,input,output FROM tool_audit WHERE task_id=? AND status='ok' ORDER BY started_at",(task['id'],))


def require_queries(*names):
    known={r['name'] for r in tool_calls()}
    if not set(names)<=known:raise ServiceValidationError('本任务需先查询：'+', '.join(sorted(set(names)-known)))


def schedules():
    state=tm.state()
    import service_workflow as wf
    if wf.enabled(state):
        return {'clock':'test_business_time','as_of':state['as_of'],**wf.summary(),
                'instruction':'修改事项使用workflow_context/commit，沿用其中task_id与revision。当前内部规划任务不是对客事项，不提交keep；只判断当前catalog，不枚举全部Skill。'}
    return {'clock':'test_business_time','as_of':state['as_of'],'active_task':active(),
            'tasks':tasks(),'instruction':'固定检查只作约束，不重复创建；使用service_schedule落实非周期安排。'}


def schedule(candidate_id,run_at,reason,check_conditions):
    if __import__('service_workflow').enabled():raise ServiceValidationError('新模式使用workflow_context/commit原子提交事项与任务')
    import proactive
    require_skills('tutor-main','plan-today','proactive-service')
    require_queries('daily_context','action_candidates','service_schedule_list')
    row=next((x for x in proactive.refresh(tm.clock().date().isoformat()) if x.get('id')==candidate_id),None)
    if not row or row['status']!='eligible':raise ServiceValidationError('Only current eligible candidates may be scheduled')
    if row.get('evidence',{}).get('event_id') and row['skill'] in __import__('completion_service').SKILLS:
        raise ServiceValidationError('该候选由完课事件入口排期；沿用已有入口，不重复创建。')
    if row['skill'] in ('rebuild-reading-rhythm','close-week-reading-gap','invite-reading-interaction','send-festival-wishes'):
        raise ServiceValidationError('该动作由已有周期入口唤醒，沿用固定检查，不重复创建非周期安排')
    stamp=dt.datetime.fromisoformat(run_at)
    if stamp.tzinfo is None:raise ServiceValidationError('排期必须带时区')
    run_at=stamp.astimezone(tm.TZ).isoformat(timespec='seconds')
    if not tm.state()['as_of']<run_at<=row['time_window']['latest'] or run_at<row['time_window']['earliest']:
        raise ServiceValidationError('排期必须在当前业务时间之后且在候选允许窗口内')
    if not reason.strip() or not check_conditions.strip():raise ServiceValidationError('需要业务理由和到点复查条件')
    old=s.read("SELECT * FROM service_tasks WHERE run_id=? AND candidate_id=? AND status IN ('planned','running','deferred')",
               (tm.state()['run_id'],candidate_id))
    if old:
        if old[0]['kind']=='signal':raise ServiceValidationError('该候选已由完课事件入口排期；沿用已有入口，不加入模型非周期计划，不重复创建。')
        return {**old[0],'duplicate':True,'schedule_id':old[0]['id'],'clock':'test_business_time'}
    task=insert_task('action',row['title'],row['skill'],run_at,reason,candidate_id,check_conditions)
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('UPDATE service_tasks SET expires_at=? WHERE id=?',(row['time_window']['latest'],task['id']))
    return {**task,'schedule_id':task['id'],'clock':'test_business_time'}


def cancel(task_id,reason):
    if __import__('service_workflow').enabled():raise ServiceValidationError('使用workflow_context/commit带版本和原话修订')
    if not reason.strip():raise ServiceValidationError('需要取消依据')
    require_queries('daily_context','action_candidates')
    with closing(sqlite3.connect(s.DB)) as c,c:
        n=c.execute("UPDATE service_tasks SET status='cancelled',finished_at=?,result=? WHERE id=? AND run_id=? AND kind='action' AND status IN ('planned','deferred')",
                    (s.now(),s.encoded({'reason':reason}),task_id,tm.state()['run_id'])).rowcount
    if not n:raise ServiceValidationError('只能取消本轮尚未执行的非周期安排')
    return {'status':'cancelled','task_id':task_id,'reason':reason}


def save(day,items,reason):
    if __import__('service_workflow').enabled():raise ServiceValidationError('新模式使用workflow_commit保存实际计划，包括空计划')
    import business
    require_skills('tutor-main','plan-today','proactive-service')
    require_queries('daily_context','action_candidates','service_schedule_list')
    if day!=tm.state()['as_of'][:10] or not reason.strip():raise ServiceValidationError('计划需属于当前业务日期并说明理由')
    normalized=[]
    for item in items:
        task_id=business.original(item['schedule_id']);candidate_id=business.original(item['candidate_id'])
        rows=s.read("SELECT * FROM service_tasks WHERE id=? AND run_id=? AND kind='action' AND candidate_id=? AND status IN ('planned','deferred','running')",
                    (task_id,tm.state()['run_id'],candidate_id))
        if not rows:raise ServiceValidationError('缺少已落实的服务排期回执')
        normalized.append(rows[0])
    if len({r['id'] for r in normalized})!=len(normalized):raise ServiceValidationError('排期重复')
    all_live={r['id'] for r in s.read("SELECT id FROM service_tasks WHERE run_id=? AND kind='action' AND status IN ('planned','deferred','running')",(tm.state()['run_id'],))}
    if {r['id'] for r in normalized}!=all_live:raise ServiceValidationError('必须保留已有有效安排，或先说明依据并取消')
    value={'schema_version':3,'source':'model','run_id':tm.state()['run_id'],'business_date':day,
           'as_of':tm.state()['as_of'],'planned_at':s.now(),'reason':reason,'items':normalized,
           'schedule_clock':'test_business_time','fixed_checks':[{'time':x[1],'title':x[2]} for x in FIXED]}
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('INSERT OR REPLACE INTO daily_plans VALUES(?,?,?,?)',(day,s.now(),s.encoded(value),len(items)))
        c.execute('INSERT INTO service_plan_versions VALUES(?,?,?,?,?)',
                  (uuid.uuid4().hex,tm.state()['run_id'],day,s.now(),s.encoded(value)))
    return value


def guard_delivery(candidate_ids=None,s01=False):
    """A query can support a decision; neither a prewritten caption nor a plan is execution."""
    task=active()
    if __import__('service_workflow').enabled() and not task:raise ServiceValidationError('主动发送必须绑定当前已规划任务')
    if __import__('completion_planner').internal(task):raise ServiceValidationError('内部规划和检查禁止发送')
    periodic={'s07':'rebuild-reading-rhythm','s08':'close-week-reading-gap'}
    periodic_skill=periodic.get(task['kind']) if task else None
    if task and task['kind']=='s01':
        require_action_skills('remind-reading','reply-style')
    elif weekend_task(task):
        require_action_skills('plan-weekend-makeup','reply-style')
        require_queries('family_profile','learning_query','daily_context','action_candidates')
    elif completion_task(task) or zero_task(task) or rhythm_task(task) or closing_task(task):
        require_action_skills(task['skill'],'reply-style')
        require_queries('family_profile','learning_query','daily_context','action_candidates')
    elif s10_task(task):
        require_action_skills('weekly-learning-review','reply-style')
        require_queries('family_profile','learning_query','daily_context','action_candidates')
    elif festival_task(task):
        require_action_skills('send-festival-wishes','reply-style')
        require_queries('family_profile','learning_query','daily_context','action_candidates')
    elif interaction_task(task):
        require_action_skills('invite-reading-interaction','reply-style')
        require_queries('family_profile','learning_query','daily_context','action_candidates')
    else:require_action_skills('tutor-main','reply-style','remind-reading' if s01 else periodic_skill or 'proactive-service')
    if task and task['kind']=='plan':raise ServiceValidationError('规划任务只落实计划，不直接发送消息')
    if task and s01 and task['kind']!='s01':raise ServiceValidationError('今日催课仅由 S01 任务执行')
    if task and not s01 and task['kind']=='s01':raise ServiceValidationError('S01 不能替代其他主动任务')
    if task:
        if periodic_skill:
            require_queries('daily_context','learning_query')
            if task['kind']=='s08':require_queries('course_schedule')
        elif s01:require_queries('daily_context')
        else:require_queries('daily_context','action_candidates')
    if candidate_ids:
        import proactive
        current={x.get('id'):x for x in proactive.refresh(tm.state()['as_of'][:10])}
        for ident in candidate_ids:
            row=current.get(ident)
            if not row:raise ServiceValidationError('候选不再适用')
            if row.get('evidence',{}).get('event_id') and row['skill'] in __import__('completion_service').SKILLS and not completion_task(task):
                raise ServiceValidationError('完课事项只由绑定事件任务独立执行')
            if weekend_task(task) and row['skill']!='plan-weekend-makeup':raise ServiceValidationError('本次只执行S09')
            if row['skill']=='plan-weekend-makeup' and not weekend_task(task):raise ServiceValidationError('S09只由周五补读任务执行')
            if s10_task(task) and row['skill']!='weekly-learning-review':
                raise ServiceValidationError('本次S10只执行周度回顾，不发送其他动作')
            if festival_task(task) and row['skill']!='send-festival-wishes':
                raise ServiceValidationError('本次只执行节日图文祝福')
            if row['skill']=='support-zero-completion' and not zero_task(task):
                raise ServiceValidationError('S05只由对应的初次关怀任务执行')
            if zero_task(task) and row['skill']!='support-zero-completion':
                raise ServiceValidationError('本次仅执行S05初次关怀')
            if row['skill']=='send-festival-wishes' and not festival_task(task):
                raise ServiceValidationError('节日图文祝福只由对应节日任务执行')
            if interaction_task(task) and row['skill']!='invite-reading-interaction':
                raise ServiceValidationError('本次只执行周互动，不发送其他动作')
            if row['skill']=='invite-reading-interaction' and not interaction_task(task):
                raise ServiceValidationError('周互动只由对应周期任务执行')
            if row['skill'] in periodic.values() and row['skill']!=periodic_skill:
                raise ServiceValidationError('S07和S08只由对应的周期检查任务执行')
            if periodic_skill and row['skill']!=periodic_skill:
                raise ServiceValidationError('本次只处理当前周期动作，不发送其他动作')
            if task and task['kind'] in ('action','signal') and task['candidate_id']!=ident:
                raise ServiceValidationError('只能执行本排期绑定的候选')
            if task and task['kind']=='calendar' and row['skill'] not in ('celebrate-important-day','care-calendar-day'):
                raise ServiceValidationError('日历检查只能处理生日及日历关怀')
            if task and task['kind']=='weekly' and row['skill'] not in ('plan-weekend-makeup','weekly-learning-review'):
                raise ServiceValidationError('周末09:00检查只处理补读或周报')
            require_skills(row['skill'])
            # Check must be in this task (or the current passive turn's recent audit).
            calls=tool_calls(task)
            import business
            if interaction_task(task):
                import weekly_interaction
                reason=weekly_interaction.preparation([{'name':c['name'],'arguments':json.loads(c['input']),
                    'result':json.loads(c['output'] or '{}')} for c in calls],tm.snapshot())
                if reason:raise ServiceValidationError(reason)
            if periodic_skill=='close-week-reading-gap':
                import s08_service
                reason=s08_service.preparation([{'name':c['name'],'arguments':json.loads(c['input']),
                                                  'result':json.loads(c['output'] or '{}')} for c in calls],task['day'])
                if reason:raise ServiceValidationError(reason)
            if not periodic_skill and not weekend_task(task) and not s10_task(task) and not (task and task['skill'] in ('celebrate-lesson-completion','remind-historical-makeup','celebrate-learning-streak','support-zero-completion','prevent-lesson-skipping','welcome-back-to-reading') and task['kind'] in ('signal','action')) and not any(c['name']=='check_proactive' and business.original(json.loads(c['input']).get('candidate_id',''))==ident
                       and json.loads(c['output'] or '{}').get('status')=='eligible' for c in calls):
                raise ServiceValidationError('发送前先 check_proactive 复核当前候选')
            waiting=s.read("SELECT id,due_at FROM service_tasks WHERE run_id=? AND candidate_id=? AND status IN ('planned','deferred') AND due_at>?",
                           (tm.state()['run_id'],ident,tm.state()['as_of']))
            if waiting:raise ServiceValidationError('该候选已有尚未到点的安排，应沿用已有计划')


def link_delivery(result):
    task=active()
    if task and result.get('action_key'):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT OR IGNORE INTO service_links VALUES(?,?)',(task['id'],result['action_key']))
    return result


def finish(task_id,outcome,reason):
    task=active()
    if not task and __import__('service_workflow').enabled():
        recorded=s.read("SELECT t.status FROM service_tasks t JOIN service_task_versions v ON v.task_id=t.id WHERE t.id=? AND t.run_id=?",(task_id,tm.state()['run_id']))
        if recorded and recorded[0]['status'] in ('done','awaiting_delivery','blocked') and s.read('SELECT 1 FROM service_links WHERE task_id=?',(task_id,)):
            return {'task_id':task_id,'status':recorded[0]['status'],'outcome':'completed' if recorded[0]['status']=='done' else recorded[0]['status'],'reason':'按原交付实际回执收尾，不重复发送。'}
    if not task or task['id']!=task_id:raise ServiceValidationError('只能完成当前正在执行的服务任务')
    if outcome not in ('completed','no_contact','blocked','deferred') or not reason.strip():raise ServiceValidationError('需要有效结果和业务依据')
    if __import__('service_workflow').enabled() and task['kind']=='plan':
        if outcome!='blocked':
            require_queries('workflow_commit')
            if not s.read("SELECT 1 FROM service_workflow_commits WHERE run_id=? AND json_extract(result,'$.status')='committed' AND created_at>=?",(task['run_id'],task['started_at'])):raise ServiceValidationError('缺少本次合法模型规划回执')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('UPDATE service_tasks SET status=?,finished_at=?,result=? WHERE id=?',('blocked' if outcome=='blocked' else 'done',s.now(),s.encoded({'reason':reason}),task_id))
            c.execute("DELETE FROM metadata WHERE key='active_service_task'")
        return {'status':'done' if outcome!='blocked' else 'blocked','task_id':task_id,'outcome':'completed' if outcome!='blocked' else 'blocked','reason':reason}
    if __import__('completion_planner').internal(task):
        return __import__('completion_planner').finish_internal(task,outcome,reason)
    if outcome!='blocked':
        if task['kind']=='s01':require_skills('remind-reading')
        elif weekend_task(task):require_action_skills('plan-weekend-makeup','reply-style')
        elif completion_task(task) or zero_task(task) or rhythm_task(task) or closing_task(task):require_action_skills(task['skill'],'reply-style')
        elif s10_task(task):require_action_skills('weekly-learning-review','reply-style')
        elif festival_task(task):require_action_skills('send-festival-wishes','reply-style')
        elif interaction_task(task):require_action_skills('invite-reading-interaction','reply-style')
        else:require_skills('tutor-main',task['skill'])
        if task['kind']=='s01':require_queries('course_schedule')
        elif task['kind'] in ('s07','s08'):require_queries('learning_query')
        else:require_queries('daily_context','action_candidates')
    if task['kind']=='plan' and outcome!='blocked':
        require_queries('save_plan')
        plans=s.read('SELECT body FROM daily_plans WHERE business_date=?',(task['day'],))
        if not plans or json.loads(plans[0]['body']).get('planned_at','')<(task['started_at'] or ''):
            raise ServiceValidationError('先 save_plan 保存本任务制定的计划，空计划也需说明理由')
    links=s.read('SELECT d.* FROM service_links l JOIN deliveries d ON l.action_key=d.action_key WHERE l.task_id=? AND d.status<>"superseded"',(task_id,))
    delivery_unconfirmed=any(r['status'] not in ('accepted','sent') for r in links)
    # Only a delivery-caused block may be resolved by a late provider receipt.
    # Preserve explicit model/business blocks even when they also have deliveries.
    delivery_block=delivery_unconfirmed and outcome!='blocked'
    if delivery_unconfirmed:
        outcome='blocked';reason='交付未全部确认，不自动重发。'+reason
    if outcome=='no_contact' and links:raise ServiceValidationError('本任务已有交付，不能标记为未联系')
    if outcome=='completed' and task['kind']!='plan' and not links:
        raise ServiceValidationError('没有交付回执；有据不联系请使用 no_contact，无法执行请使用 blocked')
    status={'completed':'done','no_contact':'no_contact','blocked':'blocked','deferred':'deferred'}[outcome]
    if receipt_driven(task) and links and outcome in ('completed','deferred'):
        if all(r['status']=='sent' for r in links):
            status='done';outcome='completed';reason='消息发送已确认，事项完成。'
        elif all(r['status'] in ('accepted','sent') for r in links):
            status='awaiting_delivery';outcome='awaiting_delivery';reason='平台已受理，等待发送确认，不重新执行发送。'
    retry_at=task['due_at'];wall=0
    if status=='deferred':
        gate=s.gate('read') if hasattr(s,'gate') else {}
        wall=max(time.time()+15,gate.get('until',0))
        if not gate.get('active') and gate.get('until',0)<=time.time():
            for call in reversed(tool_calls(task)):
                result=json.loads(call['output'] or '{}')
                if isinstance(result,dict) and result.get('retry_at'):
                    retry_at=result['retry_at'];break
    result={'outcome':outcome,'reason':reason,'deliveries':[{'action_key':r['action_key'],'status':r['status']} for r in links]}
    if delivery_block:result['code']='delivery_unconfirmed'
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('UPDATE service_tasks SET status=?,due_at=?,not_before_wall=?,finished_at=?,result=? WHERE id=?',
                  (status,retry_at,wall,None if status=='awaiting_delivery' else s.now(),s.encoded(result),task_id))
        c.execute("DELETE FROM metadata WHERE key='active_service_task'")
    return {'task_id':task_id,'status':status,**result}


def claim():
    lab=__import__('family_lab_bridge').status()
    if lab and (not lab['dispatch'] or lab['family']['paused']):return None
    sync()
    if not __import__("service_hours").tick()["open"]:return None
    if not enabled() or active():return None
    state=tm.state()
    if s.read("SELECT 1 FROM events WHERE status IN ('pending','dispatched')"):return None
    # Operator-only isolation for explicit acceptance runs; absent in normal operation.
    scope=state.get('task_scope')
    if scope is not None and scope not in (['s07'],['s08'],['interaction'],['festival']):return None
    scope_kind=scope[0] if scope else None
    gate_rows=s.read("SELECT payload FROM task_state WHERE id='chat-gate'")
    gate=json.loads(gate_rows[0]['payload']) if gate_rows else {}
    if gate.get('active') or gate.get('until',0)>time.time():return None
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
        # Cross-process ownership must be checked under the same write lock.
        if c.execute("SELECT 1 FROM service_tasks WHERE status='running'").fetchone():return None
        if c.execute("SELECT 1 FROM events WHERE status IN ('pending','dispatched')").fetchone():return None
        fresh=c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
        if not fresh or json.loads(fresh[0])!=state:return None
        gate_row=c.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone()
        current_gate=json.loads(gate_row[0]) if gate_row else {}
        if current_gate.get('active') or current_gate.get('until',0)>time.time():return None
        row=c.execute("""SELECT * FROM service_tasks WHERE run_id=? AND status IN ('planned','deferred')
          AND due_at<=? AND expires_at>=? AND not_before_wall<=? AND attempts<3
          AND (? OR kind='plan' OR (kind='evening' AND id NOT LIKE ?) OR id=?)
          AND (? IS NULL OR kind=?)
          AND (? IS NULL OR id=?)
          ORDER BY CASE kind WHEN 'plan' THEN 0 ELSE 1 END,due_at,COALESCE((SELECT json_extract(pc.body,'$.priority') FROM proactive_candidates pc WHERE pc.id=service_tasks.candidate_id),0) DESC,created_at LIMIT 1""",
          (state['run_id'],state['as_of'],state['as_of'],time.time(),bool(state.get('auto_highlights')),
           'service-'+state['run_id']+'-%',state.get('manual_service_task_id'),scope_kind,scope_kind,
           state.get('manual_service_task_id'),state.get('manual_service_task_id'))).fetchone()
        if not row:return None
        if state.get('manual_service_task_id')==row['id']:
            state.pop('manual_service_task_id')
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        if not (lab and row['skill']=='plan-weekend-makeup') and __import__('service_workflow').enabled(state) and __import__('service_workflow').preflight_contact(c,dict(row),state):return None
        c.execute("UPDATE service_tasks SET status='running',started_at=?,finished_at=NULL,attempts=attempts+1 WHERE id=?",(s.now(),row['id']))
        c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(row['id']),))
    return s.read('SELECT * FROM service_tasks WHERE id=?',(row['id'],))[0]


def dispatched(task_id,request_id):
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('UPDATE service_tasks SET native_request_id=? WHERE id=?',(request_id,task_id))
        c.execute("INSERT OR REPLACE INTO metadata VALUES('native_service_content_task',?)",(s.encoded(task_id),))
    return {'status':'dispatched'}


def settle(task_id):
    with closing(sqlite3.connect(s.DB)) as c,c:
        __import__('service_hours').enforce(c)
        c.execute("DELETE FROM metadata WHERE key='native_service_content_task' AND value=?",(s.encoded(task_id),))
        if __import__('service_workflow').enabled():
            deferred=__import__('service_workflow').defer_pending_contact(c,task_id)
            if deferred:return deferred
            c.execute("DELETE FROM metadata WHERE key='active_service_task' AND value=? AND NOT EXISTS(SELECT 1 FROM service_tasks WHERE id=? AND status='running')",(s.encoded(task_id),task_id))
    rows=s.read("SELECT * FROM service_tasks WHERE id=? AND status='running'",(task_id,))
    if not rows:return {'status':'already_finished'}
    if __import__('service_workflow').enabled() and rows[0]['kind'] in ('plan','completion_plan'):
        return __import__('service_workflow').settle_internal(task_id)
    # Confirmed delivery completes a periodic message even if the model omits
    # its final tool call. Keep the missing finish visible in the audit result.
    if receipt_driven(rows[0]):
        links=s.read('SELECT d.action_key,d.status FROM service_links l JOIN deliveries d ON l.action_key=d.action_key WHERE l.task_id=? AND d.status<>"superseded"',(task_id,))
        if links and all(r['status'] in ('accepted','sent') for r in links):
            status='done' if all(r['status']=='sent' for r in links) else 'awaiting_delivery'
            result={'outcome':'completed' if status=='done' else status,'finish_receipt_missing':True,
                    'reason':'本次发送任务已完成；模型未调用结束工具，按实际交付回执收尾，持续目标由事项账本跟踪。' if status=='done' else '平台已受理，等待发送确认，不重新执行发送。',
                    'deliveries':links}
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute('UPDATE service_tasks SET status=?,finished_at=?,result=? WHERE id=?',
                          (status,s.now() if status=='done' else None,s.encoded(result),task_id))
                c.execute("DELETE FROM metadata WHERE key='active_service_task' AND value=?",(s.encoded(task_id),))
            return {'status':status,**result}
    # Without a trusted delivery, missing completion is still a blocked task.
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute("UPDATE service_tasks SET status='blocked',finished_at=?,result=? WHERE id=?",
                  (s.now(),s.encoded({'code':'missing_finish_receipt','reason':'模型运行结束但未交付 service_task_finish 回执，请查看调用记录后重新评估。'}),task_id))
        c.execute("DELETE FROM metadata WHERE key='active_service_task' AND value=?",(s.encoded(task_id),))
    return {'status':'blocked'}


def recover():
    initialize()
    if __import__('service_workflow').enabled():return __import__('service_workflow').recover()
    for row in s.read("SELECT id FROM service_tasks WHERE status='running'"):settle(row['id'])
    return {'status':'recovered'}


def prompt(task_id):
    """Supply completion instructions before the first model generation.

    Host preparation is audited separately from model tool calls. The existing
    version guard still rejects delivery if a published bundle changes later.
    A missing Skill aborts dispatch instead of allowing an uninstructed draft.
    """
    row=s.read('SELECT * FROM service_tasks WHERE id=?',(task_id,))[0]
    import task_context
    if __import__('completion_planner').internal(row):return __import__('completion_planner').prepare(task_id)
    if __import__('service_workflow').enabled() and row['kind']=='plan':return __import__('service_workflow').prepare_plan(task_id)
    if task_context.supported(row):
        if __import__('service_workflow').enabled():__import__('service_workflow').prepare_execution(task_id)
        return task_context.prepare(task_id)
    if not completion_task(row):
        return expression_packet(task_prompt(task_id))
    current=active()
    if not current or current['id']!=task_id or row['run_id']!=tm.state()['run_id']:
        raise ServiceValidationError('只能为当前执行中的完课事项装载 Skill')
    import observability
    skills=[observability.invoke('prepare_completion_skill',{'name':name,'task_id':task_id},
                                lambda name=name:load_skill(name),origin='host')
            for name in action_skill_names((row['skill'],'reply-style'))]
    if any(not skill.get('body') for skill in skills):
        raise ServiceValidationError('完课事项的 Skill 正文未完整装载，停止生成')
    context='本次任务开始前，宿主已装载下列最新 Skill 正文。先完整阅读并按其要求决定如何执行，再查询事实、组织消息。不要先拟稿后补读规则。\n'
    for skill in skills:
        context+='\n<loaded_skill name="'+skill['name']+'" sha256="'+skill['sha256']+'">\n'+skill['body']+'\n</loaded_skill>\n'
    result=task_prompt(task_id)
    return expression_packet({**result,'prompt':context+'\n'+result['prompt'],
            'prepared_skills':[{'name':v['name'],'sha256':v['sha256'],'bundle_sha256':v['bundle_sha256']} for v in skills]})


def expression_packet(packet):
    if not action_skill_names(['reply-style']):
        return {**packet,'prompt':'本轮为五家庭主动服务，按动作Skill决定要做什么，给家庭的文字按老师人设来写。无需读取独立表达Skill，动作资料中的旧表达依赖以此为准。\n'+packet['prompt']}
    return packet


def task_prompt(task_id):
    row=s.read('SELECT * FROM service_tasks WHERE id=?',(task_id,))[0]
    if completion_task(row):
        import business
        task_id=business.token(task_id)
        return {'prompt':'这是内部完课事件事项。对应动作Skill和reply-style的最新正文已在本任务开头提供，先阅读，再独立执行；需要重新读取时调用service_skill。'
                '先查询family_profile、learning_query、daily_context和action_candidates，核实完课事实、缺课或连续学习条件；使用具体情节前按需读取对应课程知识。'
                '只处理绑定候选；不加载service-policy及附件、tutor-main、proactive-service或plan-today，不重新排期，不自行切换动作。'
                '事件到达只代表需要检查。发送前check_proactive，通过后用deliver_proactive；不符合就不联系。最后调用service_task_finish记录实际结果。'
                '业务时间：'+tm.state()['as_of']+'；任务ID：'+task_id+'；candidate_ids：'+s.encoded([business.token(row['candidate_id'])])}
    if festival_task(row):
        return {'prompt':'这是内部节日图文祝福任务。读取send-festival-wishes和reply-style的最新正文，独立执行。'
                '先用family_profile、learning_query、daily_context、action_candidates核实孩子与节点，自主选择真实阅读细节；使用具体故事内容前读取对应已学课文。'
                '候选标记simulated时，是用户授权的节日模拟情景，学情仍以business_time为准，不伪造已学记录。'
                '通过generate_image(content,request_id)生成真实图片，再调用inspect_festival_image(asset_id)核对成图。'
                '文字以孩子昵称或昵称加宝贝开头。check_proactive通过后用deliver_festival(candidate_id,body,asset_id)一次提交图文，不用deliver_proactive只发文字。'
                '本任务不加载service-policy及附件，不创建排期，不处理其他动作。最后service_task_finish，按图文实际回执收尾，不重复发送。'
                '\n任务ID：'+task_id+'\n业务时间：'+tm.state()['as_of']}
    if interaction_task(row):
        return {'prompt':'这是家庭阅读服务的内部周五19:00周互动任务，不是家长消息。'
                '请读取service_skill(name="invite-reading-interaction")和service_skill(name="reply-style")的最新正文，独立执行周互动。'
                '先用family_profile、learning_query和daily_context了解家庭、当周已学内容与近期交流，自主选取互动角度和课程资料。'
                '具体情节、知识点和方法请读取对应已学课程正文。使用现有微信文字通道发出自然的互动邀请。'
                '本次不加载service-policy及其附件，也不读取tutor-main、proactive-service或plan-today。'
                '本任务已到点，不创建service_schedule，不调用save_plan，不处理其他动作。'
                '用action_candidates获取周互动候选，发送前check_proactive，允许才deliver_proactive。'
                '最后调用service_task_finish记录结果，确认送达即完成邀请，不等待孩子回复；仅受理时等待确认，不重发。'
                '\n任务ID：'+task_id+'\n业务时间：'+tm.state()['as_of']}
    if s10_task(row):
        return {'prompt':'这是家庭阅读服务的内部周六09:00周度总结与下周预告任务，不是家长消息。周六只处理周报。'
                '请读取service_skill(name="weekly-learning-review")和service_skill(name="reply-style")的最新正文，按S10独立执行。'
                '先用family_profile、learning_query核对本周真实学情，用daily_context了解家庭安排，再自主选择需要的课程资料和读取深度。'
                '本次不加载service-policy及其附件，也不读取tutor-main、proactive-service或plan-today。'
                '当前已经是到点的固定执行入口，不创建新的service_schedule，不调用save_plan，不处理其他动作。'
                '使用action_candidates取得S10候选，发送前check_proactive，允许才deliver_proactive。'
                '最后调用service_task_finish记录结果。消息确认送达即完成，仅受理时等待发送回执，不重新发送。'
                '\n任务ID：'+task_id+'\n业务时间：'+tm.state()['as_of']}
    if row['kind']=='weekly' and dt.date.fromisoformat(row['day']).weekday()==4:
        return {'prompt':'这是家庭阅读服务的内部周五20:00补读检查，不是家长消息。'
                '请读取service_skill中的service-policy、tutor-main、proactive-service和plan-weekend-makeup最新正文，按S09执行。'
                '周期唤醒不代表必须发送；先用family_profile、learning_query核对家庭与本周逐课状态，由你依据Skill判断是否需要补读。'
                '用daily_context、action_candidates和service_schedule_list了解家庭安排、候选和已有事项。'
                '本任务已经是到点的固定执行入口，直接核验并执行，不创建新的service_schedule，不调用save_plan；已有未来等价安排则沿用，不提前发送。'
                '本任务只处理S09，不处理周报或其他动作。无需联系就直接结束，不为说明跳过而联系。'
                '需要联系时理解对应课程知识，使用reply-style组织周末安排建议，check_proactive通过后提交deliver_proactive。'
                '最后调用service_task_finish：有据不联系用no_contact，材料缺失用blocked，已交付据真实回执收尾；仅受理就等待确认，不重新发送。'
                '\n任务ID：'+task_id+'\n业务时间：'+tm.state()['as_of']}
    if row['kind'] in ('s07','s08'):
        import business,proactive,s07_service,s08_service
        is_s08=row['kind']=='s08'
        code='S08' if is_s08 else 'S07'
        label='周五当周收尾检查' if is_s08 else '周三周期检查'
        skill=s08_service.RULE if is_s08 else s07_service.RULE
        module=s08_service if is_s08 else s07_service
        proactive.refresh(row['day'])
        ident=business.token(module.candidate_id(row['run_id'],row['day']))
        return {'prompt':'这是家庭阅读服务的内部'+label+'，不是家长消息。请读取service_skill(name="tutor-main")和service_skill(name="'+skill+'")的最新正文，按'+code+'执行。'
                '周期唤醒不代表必须发送，先调用learning_query查询weekly_learning中的周学情和逐课记录，由你依据Skill判断是否需要督学。'
                '无需联系就结束，不为解释跳过而发送消息。需要联系时充分阅读相关课程知识、了解家庭安排，再调用reply-style生成消息，提交deliver_proactive统一校验。'
                '本任务只处理'+code+'，不创建周期任务、不改每日计划、不发送其他动作，不需要预先调用check_proactive。'
                '最后必须调用service_task_finish记录结果：有发送确认用completed，有据不联系用no_contact，缺资料用blocked；仅受理则等待回执。'
                '\n任务ID：'+task_id+'\n交付candidate_ids：'+s.encoded([ident])+'\n业务时间：'+tm.state()['as_of']}
    scopes={'calendar':'只处理生日、日历关怀；不要催课或发送学习高光。',
            's01':'只核对今日课程，不用历史欠课代替今日未完课。缺少课程正文时仅发简单开课邀请，不编造兴趣点；完课状态未知则停止。',
            'weekly':'周五20:00只处理补读，周六09:00只处理周报；核对当前事实，不重复已交付内容。',
            'evening':'根据当前学情、已有计划和近期交付，判断晚间需要做什么。先检查已经落实的非周期安排，未到点的沿用。可以合并相关高光或正确地不联系。',
            'action':'只执行本任务绑定的候选；先重新核验，条件改变则停止。',
            'signal':'这是完课事件唤醒，只核验绑定的S02候选；文字表扬不依赖高光图片。先读取对应Skill，按已发生事实具体表扬；已交付或已合并则不重复发送。',
            'plan':'读取当前学情和候选，为今天选择必要的非周期安排，说明选择时间、保留或跳过的原因。空计划也有效。规划任务不直接对客发送。'}
    return {'prompt':'服务规范版本 service-samples-20261021-v4-actions；旧上下文中的规则不得覆盖新版本，请重新读取相关Skill。你是同一个家庭的阅读辅导智能体。以下是内部服务任务，不是家长消息，不用 reply_to_message。'
       '先通过 service_skill 读取 service-policy、tutor-main 与 '+row['skill']+' 的实际正文（本轮已经读过同版本可沿用），用service_catalog定位具体动作及共享依赖，严格依Skill完成。'
       '规划也要读取 proactive-service；同一任务的完成结果只有真实交付才能用 completed，无需联系用 no_contact，材料不足用 blocked。'
       +('S01先按Skill查今日课表和学情，已完课或无课直接结束；未完课才读正文、daily_context与回复Skill。' if row['kind']=='s01' else
         '用 daily_context、action_candidates 与 service_schedule_list 读取最新事实和已有安排。本任务必须实际调用 daily_context，不能只沿用上一个任务的上下文。')
       +scopes[row['kind']]+
       '按当前业务时间，通过service_schedule落实非周期安排，再用save_plan保存。'
       '执行主动任务时读取候选对应 Skill 和 reply-style；发前 check_proactive，再 deliver_proactive。'
       'S01先查询今日课表与学情，已完课或无课直接结束，未完课才读取对应课程知识，调用reply-style生成消息，最后提交deliver_message统一校验发送。'
       '最后必须 service_task_finish(task_id,outcome,reason)，记录可读的业务依据和实际结果；工具错误不等于完成。'
       '工具若返回缺少查询或Skill的明确提示，补齐后再提交；同一错误连续两次不要原样重试，记录blocked并结束。'
       '业务理由用简短中文说明事实、安排及取舍，避免重复内部编号和工具名；结束后只需一句内部结果。'
       '\n任务资料：'+s.encoded(row)+'\n当前业务时间：'+tm.state()['as_of']}


def tasks(day=None):
    state=tm.state();day=day or state['as_of'][:10]
    rows=s.read('SELECT * FROM service_tasks WHERE run_id=? AND day=? ORDER BY due_at,created_at',(state['run_id'],day))
    for row in rows:
        row['result']=json.loads(row['result']) if row['result'] else None
        row['deliveries']=s.read('SELECT d.action_key,d.status FROM service_links l JOIN deliveries d ON l.action_key=d.action_key WHERE l.task_id=? AND d.status<>"superseded"',(row['id'],))
    return rows


def view(day=None):
    state=tm.state();day=day or state['as_of'][:10]
    current=day==state['as_of'][:10]
    rows=tasks(day)
    plans=[json.loads(r['body']) for r in s.read('SELECT body FROM service_plan_versions WHERE run_id=? AND day=? ORDER BY created_at DESC LIMIT 10',(state['run_id'],day))]
    return {'day':day,'source':'model_with_skill_checks','auto_highlights':state.get('auto_highlights',False),
            'items':[{'time':r['due_at'][11:16],'title':r['title'],'kind':r['kind'],'status':r['status'],
                      'due_at':r['due_at'],'expires_at':r['expires_at'],'candidate_id':r['candidate_id'],
                      'reason':r['reason'],'evidence':(r['result'] or {}).get('reason',''),
                      'task_id':r['id'],'checks':r['checks'],'deliveries':r['deliveries'],
                      'native_request_id':r['native_request_id']} for r in rows],
            'model_plans':plans,'last_check':None,'preview_note':None if current else
            ('本轮历史执行记录，只读回看。' if rows or plans else '尚未在该日运行智能体，不能预先生成或冒充其计划。')}


def recurrence():
    """Read-only schedule projection, distinct from a model plan or execution receipt."""
    state=tm.state()
    rules=s.read('SELECT * FROM service_recurrences WHERE run_id=?',(state['run_id'],)) if s.read(
        "SELECT 1 FROM sqlite_master WHERE name='service_recurrences'") else []
    if not rules:return {'enabled':False}
    rule=rules[0];day=state['as_of'][:10]
    rows=[r for r in tasks(day) if r['id'] in (
        'service-'+state['run_id']+'-plan','service-'+state['run_id']+'-'+day+'-plan')]
    today=rows[0] if rows else None
    due=day+'T'+rule['hour']+':00+08:00'
    next_day=day if state['as_of']<due or (today and today['status'] in ('planned','deferred')) else (
        dt.date.fromisoformat(day)+dt.timedelta(days=1)).isoformat()
    next_day=max(next_day,rule['start_day'])
    return {**rule,'enabled':enabled(),'today_status':today['status'] if today else 'not_created',
            'today_due_at':today['due_at'] if today else due,
            'next_due_at':next_day+'T'+rule['hour']+':00+08:00' if next_day<=rule['end_day'] else None,
            'clock':'test_business_time','missed_days':'skipped_no_replay'}


if __name__=='__main__':
    command=sys.argv[1]
    functions={'enable':enable,'sync':sync,'claim':claim,'recover':recover,
       'request':lambda:request(sys.argv[2] if len(sys.argv)>2 else 'plan'),
       'task':lambda:prompt(sys.argv[2]),'settle':lambda:settle(sys.argv[2]),
       'dispatched':lambda:dispatched(sys.argv[2],sys.argv[3]),'view':view}
    print(s.encoded(functions[command]()))
