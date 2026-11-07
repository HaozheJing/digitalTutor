"""Durable run-scoped cases, goals and observed outcomes. Views never mutate."""
import hashlib,json,sqlite3
from contextlib import closing
import sql_tools as s
import test_mode as tm

def ident(*parts):
    return hashlib.sha256(s.encoded(parts).encode()).hexdigest()[:32]

def initialize():
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS service_case_constraints(run_id TEXT,uid TEXT,enrollment_id TEXT,status TEXT,source_message_id TEXT,PRIMARY KEY(run_id,uid,enrollment_id));
        CREATE TABLE IF NOT EXISTS service_cases(id TEXT PRIMARY KEY,run_id TEXT,uid TEXT,enrollment_id TEXT,kind TEXT,status TEXT,revision INTEGER,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS service_case_goals(case_id TEXT,lesson_id TEXT,completed_event TEXT,PRIMARY KEY(case_id,lesson_id));
        CREATE TABLE IF NOT EXISTS service_case_timeline(id TEXT PRIMARY KEY,case_id TEXT,kind TEXT,at TEXT,body TEXT);
        CREATE TABLE IF NOT EXISTS service_case_tasks(task_id TEXT PRIMARY KEY,case_id TEXT,event_id TEXT,code TEXT);
        CREATE TABLE IF NOT EXISTS service_case_requests(run_id TEXT,request_id TEXT,body TEXT,result TEXT,PRIMARY KEY(run_id,request_id));
        CREATE TABLE IF NOT EXISTS completion_plans(task_id TEXT PRIMARY KEY,run_id TEXT,event_id TEXT,request_id TEXT,body TEXT,created_at TEXT,UNIQUE(run_id,request_id));
        CREATE TABLE IF NOT EXISTS completion_plan_contexts(task_id TEXT PRIMARY KEY,digest TEXT,body TEXT);
        """)

def exists(c):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='service_cases'").fetchone())

def timeline(c,case_id,kind,key,body):
    clock=c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
    body={**body,'business_at':json.loads(clock[0])['as_of'] if clock else s.now()}
    c.execute('INSERT OR IGNORE INTO service_case_timeline VALUES(?,?,?,?,?)',
              (ident(case_id,kind,key),case_id,kind,s.now(),s.encoded(body)))

def change(c,case_id,status):
    c.execute('UPDATE service_cases SET status=?,revision=revision+1,updated_at=? WHERE id=? AND status<>?',(status,s.now(),case_id,status))

def case_for(c,state,event,code,goals):
    kind='makeup' if code in ('S03','S06') else code
    # One matter per independently scheduled action. Existing deduplicated tasks
    # are kept by the planner without creating or expanding another matter.
    case_id='case-'+ident(state['run_id'],event['uid'],event['enrollment_id'],code,event['event_id'])
    c.execute('INSERT OR IGNORE INTO service_cases VALUES(?,?,?,?,?,?,?,?,?)',(case_id,state['run_id'],event['uid'],event['enrollment_id'],kind,'open',1,s.now(),s.now()))
    for lesson in goals:
        inserted=c.execute('INSERT OR IGNORE INTO service_case_goals VALUES(?,?,NULL)',(case_id,lesson)).rowcount
        if inserted:c.execute('UPDATE service_cases SET revision=revision+1,updated_at=? WHERE id=?',(s.now(),case_id))
    timeline(c,case_id,'completion_signal',event['event_id'],{'event_id':event['event_id'],'code':code,'goals':goals})
    return case_id

def cancel_pending(c,case_id,reason):
    c.execute("UPDATE service_tasks SET status='cancelled',finished_at=?,result=? WHERE id IN (SELECT task_id FROM service_case_tasks WHERE case_id=?) AND status IN ('planned','deferred')",(s.now(),s.encoded({'reason':reason}),case_id))

def progress(c,state):
    if not exists(c):return
    if __import__('service_workflow').enabled(state):return  # task outcome is the sole matter projection
    events=c.execute('SELECT event_id,enrollment_id,lesson_id FROM completion_events WHERE run_id=? AND completed_at<=?',(state['run_id'],state['as_of'])).fetchall()
    for event_id,enrollment,lesson in events:
        cases=c.execute("SELECT g.case_id FROM service_case_goals g JOIN service_cases x ON x.id=g.case_id WHERE x.run_id=? AND x.enrollment_id=? AND g.lesson_id=? AND g.completed_event IS NULL",(state['run_id'],enrollment,lesson)).fetchall()
        for (case_id,) in cases:
            c.execute('UPDATE service_case_goals SET completed_event=? WHERE case_id=? AND lesson_id=?',(event_id,case_id,lesson))
            c.execute('UPDATE service_cases SET revision=revision+1,updated_at=? WHERE id=?',(s.now(),case_id))
            timeline(c,case_id,'goal_completed',event_id,{'event_id':event_id,'lesson_id':lesson})
            if not c.execute('SELECT 1 FROM service_case_goals WHERE case_id=? AND completed_event IS NULL',(case_id,)).fetchone():
                status=c.execute('SELECT status FROM service_cases WHERE id=?',(case_id,)).fetchone()[0]
                if status!='completed':
                    change(c,case_id,'resolved');cancel_pending(c,case_id,'所有补读目标已有完课事实')
                timeline(c,case_id,'goals_completed' if status=='completed' else 'resolved',event_id,{'reason':'全部目标已有完课事实'})

def cancel_automatic_reviews(c,run_id=None):
    if not exists(c):return []
    rows=c.execute("""SELECT t.id,l.case_id FROM service_tasks t
        JOIN service_case_tasks l ON l.task_id=t.id JOIN service_cases x ON x.id=l.case_id
        WHERE t.kind='case_review' AND t.skill='review-service-case'
        AND t.id LIKE 'service-review-%' AND x.kind='makeup'
        AND t.status IN ('planned','deferred') AND (? IS NULL OR t.run_id=?)""",
        (run_id,run_id)).fetchall()
    reason='用户取消七天自动复查；近期一个事项对应一个任务，保留历史记录。'
    for task_id,case_id in rows:
        c.execute("UPDATE service_tasks SET status='cancelled',finished_at=?,result=? WHERE id=?",
                  (s.now(),s.encoded({'reason':reason,'code':'automatic_review_removed'}),task_id))
        timeline(c,case_id,'task_cancelled',task_id+':automatic_review_removed',{'task_id':task_id,'reason':reason})
    return [row[0] for row in rows]


def reconcile(c,state):
    if not exists(c):return
    if __import__('service_workflow').enabled(state):
        __import__('service_workflow').reconcile(c);return
    cancel_automatic_reviews(c,state['run_id'])
    c.execute('INSERT OR IGNORE INTO service_links SELECT t.id,pr.delivery_key FROM service_case_tasks l JOIN service_tasks t ON t.id=l.task_id JOIN proactive_receipts pr ON pr.candidate_id=t.candidate_id WHERE t.run_id=?',(state['run_id'],))
    rows=c.execute("SELECT x.id,x.kind,x.status,t.id,t.status FROM service_cases x JOIN service_case_tasks l ON l.case_id=x.id JOIN service_tasks t ON t.id=l.task_id WHERE x.run_id=? AND t.kind='signal'",(state['run_id'],)).fetchall()
    for case_id,kind,status,task_id,task_status in rows:
        deliveries=c.execute('SELECT d.action_key,d.status FROM service_links l JOIN deliveries d ON d.action_key=l.action_key WHERE l.task_id=?',(task_id,)).fetchall()
        for key,delivery in deliveries:timeline(c,case_id,'delivery',key+':'+delivery,{'task_id':task_id,'action_key':key,'status':delivery})
        if deliveries and all(d[1]=='sent' for d in deliveries):
            if status not in ('resolved','paused','stopped','closed'):
                change(c,case_id,'completed')
        elif deliveries and status not in ('resolved','paused','stopped','closed','completed'):
            change(c,case_id,'awaiting_delivery' if all(d[1] in ('accepted','sent') for d in deliveries) else 'delivery_failed')
        if task_status in ('cancelled','blocked','expired','no_contact'):
            timeline(c,case_id,'task_outcome',task_id+':'+task_status,{'task_id':task_id,'status':task_status})
            if not deliveries and status not in ('completed','closed','resolved','paused','stopped'):
                change(c,case_id,'cancelled' if task_status=='cancelled' else 'failed' if task_status=='blocked' else 'closed')

def view(run_id=None,connection=None):
    run_id=run_id or (tm.state() or {}).get('run_id')
    def read(c):
        if not exists(c):return {'run_id':run_id,'cases':[],'runs':[]}
        def rows(sql,args=()):
            cur=c.execute(sql,args);return [dict(zip([d[0] for d in cur.description],r)) for r in cur]
        cases=rows('SELECT * FROM service_cases WHERE run_id=? ORDER BY created_at DESC',(run_id,))
        for case in cases:
            case['goals']=rows('SELECT * FROM service_case_goals WHERE case_id=?',(case['id'],))
            case['timeline']=rows('SELECT kind,at,body FROM service_case_timeline WHERE case_id=? ORDER BY at,id',(case['id'],))
            for entry in case['timeline']:entry['body']=json.loads(entry['body'])
            case['tasks']=rows('SELECT t.id,t.kind,t.due_at,t.status,t.result,l.event_id,l.code FROM service_case_tasks l JOIN service_tasks t ON l.task_id=t.id WHERE l.case_id=? ORDER BY t.created_at',(case['id'],))
            case['completion_scope']='service_delivery'
            case['business_issue_resolved']='not_established'
            for task in case['tasks']:
                task['execution_state']=__import__('execution_contract').task_projection(c,task)
            case['next_review']=next((t['due_at'] for t in case['tasks'] if t['kind']=='case_review' and t['status'] in ('planned','deferred')),None)
            case['title']={'makeup':'补读建议交付','S02':'首周完课鼓励','S04':'连续学习鼓励'}.get(case['kind'],case['kind'])
            case['targets']=[g['lesson_id'] for g in case['goals']]
            case['next_review_at']=case['next_review']
            case['history']=[{**x,'business_at':x['body'].get('business_at',x['at'])} for x in case['timeline']]
            case['summary']=('关联课节已完成 %d/%d；最终sent完成本次建议交付，学情独立记录，不追加复查。' % (sum(bool(g['completed_event']) for g in case['goals']),len(case['goals']))) if case['kind']=='makeup' else '以最终sent回执确认交付；accepted只是受理。'
        plans=rows('SELECT * FROM completion_plans WHERE run_id=? ORDER BY created_at DESC',(run_id,))
        for plan in plans:
            plan['body']=json.loads(plan['body'])
            for decision in plan['body'].get('decisions',[]):
                task=c.execute('SELECT t.id,t.due_at,t.reason FROM service_tasks t JOIN service_case_tasks l ON l.task_id=t.id WHERE t.run_id=? AND l.event_id=? AND l.code=? ORDER BY t.created_at LIMIT 1',(run_id,plan['event_id'],decision.get('code'))).fetchone()
                if task and decision.get('decision') in ('schedule','keep'):
                    decision.update(task_id=task[0],requested_run_at=decision.get('run_at'),run_at=task[1],reason=task[2])
        return {'run_id':run_id,'cases':cases,'plans':plans,'constraints':rows('SELECT * FROM service_case_constraints WHERE run_id=?',(run_id,)),'runs':[r[0] for r in c.execute('SELECT run_id FROM service_cases UNION SELECT run_id FROM completion_plans')]}
    if connection is not None:return read(connection)
    with closing(sqlite3.connect('file:'+str(s.DB)+'?mode=ro',uri=True)) as c:return read(c)

def feedback(case_id,revision,request_id,source_message_id,quote,action):
    if __import__('service_workflow').enabled():raise ValueError('新模式通过workflow_commit引用本轮消息局部修订任务，事项结果由任务投影')
    if action not in ('pause','stop','resume','intent','close') or not request_id or not quote or type(revision) is not int:
        raise ValueError('需要反馈动作、原话、稳定请求编号与版本')
    import business
    case_id=business.original(case_id);source_message_id=business.original(source_message_id)
    state=tm.state();body=s.encoded([case_id,revision,source_message_id,quote,action])
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        prior=c.execute('SELECT body,result FROM service_case_requests WHERE run_id=? AND request_id=?',(state['run_id'],request_id)).fetchone()
        if prior:
            if prior[0]!=body:raise ValueError('request_id内容冲突')
            return json.loads(prior[1])
        row=c.execute('SELECT status,revision FROM service_cases WHERE id=? AND run_id=?',(case_id,state['run_id'])).fetchone()
        if not row or row[1]!=revision:raise ValueError('事项版本已变化，请重新读取')
        source=c.execute("SELECT body,sha256,created_at FROM messages WHERE source_id=? AND speaker='user' AND visible=1",(source_message_id,)).fetchone()
        latest=c.execute("SELECT source_id FROM messages WHERE speaker='user' AND visible=1 ORDER BY rowid DESC LIMIT 1").fetchone()
        if not source or not quote.strip() or quote not in source[0] or not latest or latest[0]!=source_message_id:
            raise ValueError('必须引用当前实际家庭消息原话，不接受旧话或推测')
        constraint=c.execute('SELECT status FROM service_case_constraints WHERE (run_id,uid,enrollment_id) IN (SELECT run_id,uid,enrollment_id FROM service_cases WHERE id=?)',(case_id,)).fetchone()
        if row[0] in ('resolved','completed','closed') and action!='resume':raise ValueError('已结案事项不能通过反馈重新打开')
        if action=='resume' and not (row[0] in ('paused','stopped') or constraint and constraint[0] in ('paused','stopped')):raise ValueError('当前事项未暂停')
        identity=c.execute('SELECT uid,enrollment_id FROM service_cases WHERE id=?',(case_id,)).fetchone()
        if action in ('pause','stop','resume'):
            c.execute('INSERT OR REPLACE INTO service_case_constraints VALUES(?,?,?,?,?)',(state['run_id'],identity[0],identity[1],{'pause':'paused','stop':'stopped','resume':'open'}[action],source_message_id))
        status=row[0] if action=='resume' and row[0] in ('resolved','completed','closed') else {'pause':'paused','stop':'stopped','resume':'open','close':'closed'}.get(action,row[0])
        c.execute('UPDATE service_cases SET status=?,revision=revision+1,updated_at=? WHERE id=?',(status,s.now(),case_id))
        if action in ('pause','stop','close'):cancel_pending(c,case_id,'家庭反馈：'+action)
        timeline(c,case_id,'family_feedback',request_id,{'action':action,'quote':quote,'source_message_id':source_message_id,'source_sha256':source[1]})
        result={'case_id':case_id,'status':status,'revision':revision+1}
        c.execute('INSERT INTO service_case_requests VALUES(?,?,?,?)',(state['run_id'],request_id,body,s.encoded(result)))
        return result

if __name__=='__main__':
    import sys
    print(s.encoded(view(json.loads(sys.argv[1]).get('ledger_run_id') if len(sys.argv)>1 else None)))
