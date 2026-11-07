"""Model-owned completion plans committed atomically into the existing scheduler."""
import datetime as dt,hashlib,json,sqlite3
from contextlib import closing
import sql_tools as s
import test_mode as tm
import service_ledger as ledger
from completion_service import RULES, TITLES
INTERNAL={'plan-after-lesson-completion','review-service-case'}

def default_mode():
    path=s.R/'config/completion-planning.json'
    return bool(path.is_file() and json.loads(path.read_text()).get('enabled') is True)

def enabled(state=None):return (state or tm.state() or {}).get('completion_planning_mode')=='ledger-v1' or __import__('service_workflow').enabled(state)
def internal(task=None):
    if task is None:
        import agent_plan
        task=agent_plan.active()
        if not task:
            rows=s.read("SELECT value FROM metadata WHERE key='native_service_content_task'")
            if rows:
                found=s.read('SELECT * FROM service_tasks WHERE id=? AND run_id=?',(json.loads(rows[0]['value']),(tm.state() or {}).get('run_id')))
                task=found[0] if found else None
    return bool(task and task['skill'] in INTERNAL)

def insert(c,state,task_id,kind,skill,due,expires,reference,reason):
    expires=__import__('service_hours').task_expiry(due,expires)
    c.execute('INSERT OR IGNORE INTO service_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (task_id,state['run_id'],due[:10],kind,skill,skill,due,expires,reference,reason,'',
         'planned',0,0,s.now(),None,None,None,None))

def sync():
    state=tm.state()
    if not enabled(state):return
    ledger.initialize()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        ledger.progress(c,state);ledger.reconcile(c,state)
        __import__('s11_service').ensure(c,state)
        for source,event_id,stamp in c.execute('SELECT source,event_id,completed_at FROM completion_events WHERE run_id=? AND completed_at LIKE ?',(state['run_id'],state['as_of'][:10]+'%')).fetchall():
            task_id='service-completion-'+ledger.ident(state['run_id'],source,event_id)
            insert(c,state,task_id,'completion_plan','plan-after-lesson-completion',state['as_of'],state['as_of'][:10]+'T23:59:59+08:00',s.encoded([source,event_id]),'完课信号：先读取独立规划Skill，提交四项判断，不发送消息')

def s02_skip_basis(c,state,event,item):
    """Only authoritative restrictions or this exact matter may suppress eligible S02."""
    import service_workflow as wf
    if not wf.enabled(state) or not item or item['status']!='eligible':return 'outside_required_s02'
    if state['as_of']>item['time_window']['latest']:return 'window_expired'
    spacing=__import__('proactive').contact_spacing(c,[item],dt.datetime.fromisoformat(state['as_of']))
    if spacing['not_before'] and spacing['not_before']>item['time_window']['latest']:return 'contact_window_expired'
    if c.execute("SELECT 1 FROM service_tasks t JOIN proactive_candidates pc ON pc.id=t.candidate_id WHERE t.run_id=? AND json_extract(pc.body,'$.dedupe_key')=?",(state['run_id'],item['dedupe_key'])).fetchone():return 'same_matter_exists'
    due=max(state['as_of'],item['time_window']['earliest'])
    if c.execute('SELECT 1 FROM contact_constraints WHERE day=?',(due[:10],)).fetchone():return 'day_paused'
    constraint=c.execute('SELECT status FROM service_case_constraints WHERE run_id=? AND uid=? AND enrollment_id=?',(state['run_id'],event['uid'],event['enrollment_id'])).fetchone()
    if constraint and constraint[0] in ('paused','stopped'):return 'enrollment_paused'
    if wf.constraint_blocks(c,state,item['skill'],at=due):return 'workflow_paused'
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='operator_alerts'").fetchone() and c.execute("SELECT 1 FROM operator_alerts WHERE run_id=? AND status IN ('pending','queued','accepted','unknown','partial','failed')",(state['run_id'],)).fetchone():return 'human_pending'
    return None


def facts(c,task,state):
    import completion_service,completion_events
    if task['kind']=='case_review':
        return {'business_time':state['as_of'],'ledger':ledger.view(state['run_id'],c),'learning':tm.snapshot(),'case_id':task['candidate_id']}
    source,event_id=json.loads(task['candidate_id'])
    event=next((e for e in completion_events.saved(state,c) if e['source']==source and e['event_id']==event_id),None)
    if not event:raise ValueError('完课事件不存在')
    snapshot=tm.snapshot();raw=tm.fixture_data(state,include_events=False)
    candidates=[x for x in completion_service.decisions(c,state,snapshot,raw) if x['evidence']['event_id']==event_id and x['skill'] in RULES.values()]
    if any(str(h.get('id','')).endswith('-09') for h in snapshot.get('available_highlights',[])):
        for item in candidates:
            if item['skill']==RULES['S04']:item.update(status='merged_into_higher_priority',reason='连续学习高光优先，由宿主发送原文原图')
    # The authoritative host projection also covers any alternative highlight schema.
    for h in snapshot.get('available_highlights',[]):
        if h.get('medal_no')==9:
            for item in candidates:
                if item['skill']==RULES['S04']:item.update(status='merged_into_higher_priority',reason='连续学习高光优先')
    for item in candidates:
        item['contact_spacing']=__import__('proactive').contact_spacing(c,[item],dt.datetime.fromisoformat(state['as_of']))
        if item['skill']==RULES['S02'] and __import__('service_workflow').enabled(state):
            basis=s02_skip_basis(c,state,event,item)
            item['planning_requirement']={'required_task':basis is None,'skip_basis':basis,'note':'S02是首周逐课鼓励；S01课前邀请、其他课的鼓励和未送达的未来任务不能覆盖本次完课。required_task为true时须schedule或keep本事项；执行时仍复核联系限制。'}
    tasks=[dict(zip([d[0] for d in cur.description],row)) for cur in [c.execute('SELECT * FROM service_tasks WHERE run_id=? ORDER BY id',(state['run_id'],))] for row in cur]
    return {'business_time':state['as_of'],'event':event,'learning':snapshot,'candidates':candidates,
            'ledger':ledger.view(state['run_id'],c),'tasks':tasks,
            'workflow_facts':__import__('service_workflow').facts(c,state,[RULES[k] for k in RULES]) if __import__('service_workflow').enabled(state) else None,
            'workflow_versions':__import__('service_workflow').task_rows(c,state) if __import__('service_workflow').enabled(state) else None,
            'instructions':'四项都必须判断。S02按planning_requirement：required_task为true须schedule或keep本次完课任务，S01、其他课的鼓励和未送达的未来任务不能覆盖。其他候选资格是事实校验上限，不代表应该联系。未知用unknown；先核对家庭暂停、目标、已有任务与同事件高光覆盖。'}

def context():
    import agent_plan as ap
    task=ap.active()
    if not internal(task):raise ValueError('仅当前内部规划或检查任务可读取规划资料')
    state=tm.state()
    with closing(sqlite3.connect(s.DB)) as c,c:
        value=facts(c,task,state)
        # Exclude own transient status from optimistic concurrency token.
        if 'tasks' in value:value['tasks']=[{k:r[k] for k in ('id','status','due_at','candidate_id')} for r in value['tasks'] if r['id']!=task['id']]
        digest=hashlib.sha256(s.encoded(value).encode()).hexdigest()
        c.execute('INSERT OR REPLACE INTO completion_plan_contexts VALUES(?,?,?)',(task['id'],digest,s.encoded(value)))
    result={**value,'context_token':digest,'task_id':task['id']}
    return __import__('working_context').model(__import__('completion_model_view').for_model(result) if task['kind']=='completion_plan' else result)

def commit(request_id,context_token,decisions):
    import agent_plan as ap
    task=ap.active()
    if not task or task['kind']!='completion_plan' or not enabled():raise ValueError('仅当前完课规划任务可提交')
    ap.require_skills(task['skill']);ap.require_queries('completion_plan_context')
    if not isinstance(request_id,str) or not 8<=len(request_id)<=160:raise ValueError('需要稳定request_id')
    if not isinstance(decisions,list) or len(decisions)!=4 or {d.get('code') for d in decisions}!=set(RULES):raise ValueError('必须原子提交S02/S03/S04/S06四项判断')
    state=tm.state();body=s.encoded({'context_token':context_token,'decisions':decisions})
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT body,request_id FROM completion_plans WHERE task_id=?',(task['id'],)).fetchone()
        if old:
            if old!=(body,request_id):raise ValueError('规划已提交，不能覆盖或重复创建')
            return {'status':'committed','task_id':task['id'],'duplicate':True}
        if json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])!=state:raise ValueError('轮次或业务时钟已变化')
        cur=facts(c,task,state);cur['tasks']=[{k:r[k] for k in ('id','status','due_at','candidate_id')} for r in cur['tasks'] if r['id']!=task['id']]
        saved=c.execute('SELECT digest FROM completion_plan_contexts WHERE task_id=?',(task['id'],)).fetchone()
        if not saved or saved[0]!=context_token or hashlib.sha256(s.encoded(cur).encode()).hexdigest()!=context_token:raise ValueError('资料已变化，请重新读取后判断')
        event=cur['event'];available={x['skill']:x for x in cur['candidates']}
        result=[]
        for decision in decisions:
            code=decision['code'];choice=decision.get('decision');reason=decision.get('reason');evidence=decision.get('evidence')
            if choice not in ('schedule','keep','skip','cancel','unknown') or not isinstance(reason,str) or not reason.strip() or len(reason)>2000 or not isinstance(evidence,list) or event['event_id'] not in evidence:raise ValueError('每项需要结果、可见事实依据及事件编号，不保存思维链')
            item=available.get(RULES[code]);schedule_id=None
            if code=='S02' and choice not in ('schedule','keep') and s02_skip_basis(c,state,event,item) is None:
                raise ValueError('本次S02已核实eligible且没有暂停、人工待处理或同事项任务，必须schedule；已有同事项任务用keep。S01课前邀请、其他课的S02/S04和未来未送达任务不能覆盖本次完课。沿用本次候选窗口，交付前仍会即时复核。')
            if choice in ('schedule','keep'):
                if not item:raise ValueError('没有可核验候选，需unknown')
                existing=c.execute("SELECT t.id,t.status FROM service_tasks t JOIN proactive_candidates pc ON pc.id=t.candidate_id WHERE t.run_id=? AND json_extract(pc.body,'$.dedupe_key')=? AND t.kind='signal'",(state['run_id'],item['dedupe_key'])).fetchone()
                if choice=='keep' and not existing:raise ValueError('keep必须引用已有任务')
                constraint=c.execute('SELECT status FROM service_case_constraints WHERE run_id=? AND uid=? AND enrollment_id=?',(state['run_id'],event['uid'],event['enrollment_id'])).fetchone()
                if constraint and constraint[0] in ('paused','stopped'):raise ValueError('家庭持续暂停或停止仍然有效')
                if existing:
                    if existing[1] in ('cancelled','blocked','expired','no_contact'):raise ValueError('已终止任务不能keep或重新安排')
                    # Keep the original task and matter scope; no new goals or follow-up tasks.
                    if choice=='schedule' and existing[1] in ('cancelled','blocked','expired','no_contact'):raise ValueError('旧任务已终止，不能借重试重新发送')
                    schedule_id=existing[0]
                else:
                    if item['status']!='eligible':raise ValueError('事实不支持安排：'+item['status'])
                    stamp=dt.datetime.fromisoformat(decision.get('run_at',''))
                    if stamp.tzinfo is None:raise ValueError('执行时间必须带时区')
                    due=stamp.astimezone(tm.TZ).isoformat(timespec='seconds')
                    if due<state['as_of'] or not item['time_window']['earliest']<=due<=item['time_window']['latest']:raise ValueError('执行时间超出允许窗口')
                    requested_due=due;spacing=item['contact_spacing']
                    due=max(due,spacing['not_before'] or due)
                    if due>item['time_window']['latest']:raise ValueError(spacing['reason']+'；已超出任务有效期，不能跨天补发')
                    if due!=requested_due:reason+='；宿主调整：'+spacing['reason']
                    if __import__('service_workflow').enabled(state) and __import__('service_workflow').constraint_blocks(c,state,item['skill'],at=due):raise ValueError('家庭明确暂停该服务范围')
                    goals=[x['lesson_id'] for x in item['evidence'].get('overdue_lessons',[])] if code in ('S03','S06') else []
                    case_id=ledger.case_for(c,state,event,code,goals)
                    if c.execute('SELECT status FROM service_cases WHERE id=?',(case_id,)).fetchone()[0] in ('paused','stopped'):raise ValueError('家庭暂停或停止仍然有效')
                    schedule_id='service-ledger-'+ledger.ident(state['run_id'],item['dedupe_key'])
                    c.execute('INSERT OR REPLACE INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',(item['id'],item['day'],item['rule'],item['scope'],s.encoded(item),item['status'],s.now()))
                    insert(c,state,schedule_id,'signal',item['skill'],due,item['time_window']['latest'],item['id'],reason)
                    c.execute('INSERT OR IGNORE INTO service_case_tasks VALUES(?,?,?,?)',(schedule_id,case_id,event['event_id'],code))
                    if __import__('service_workflow').enabled(state):
                        import service_content
                        c.execute('INSERT INTO service_task_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(schedule_id,case_id,state['uid'],1,service_content.bundle_digest(item['skill']),s.encoded({'event_id':event['event_id'],'request_id':request_id}),reason,item['time_window']['earliest'],item['dedupe_key'],None,None,0,s.now()))
                        c.execute("UPDATE service_cases SET status='planned',revision=1 WHERE id=?",(case_id,))
                    ledger.timeline(c,case_id,'scheduled',schedule_id,{'task_id':schedule_id,'due_at':due,'reason':reason,'event_id':event['event_id']})
            elif choice=='cancel':
                schedule_id=decision.get('task_id')
                linked=c.execute("SELECT l.case_id FROM service_case_tasks l JOIN service_cases x ON x.id=l.case_id JOIN service_tasks t ON t.id=l.task_id WHERE t.id=? AND x.run_id=? AND x.enrollment_id=? AND l.code=? AND t.status IN ('planned','deferred')",(schedule_id,state['run_id'],event['enrollment_id'],code)).fetchone()
                if not linked:raise ValueError('只能取消同报名同动作的待执行任务')
                c.execute("UPDATE service_tasks SET status='cancelled',finished_at=?,result=? WHERE id=?",(s.now(),s.encoded({'reason':reason}),schedule_id))
                ledger.timeline(c,linked[0],'cancelled',schedule_id,{'task_id':schedule_id,'reason':reason})
            persisted=c.execute('SELECT due_at,reason FROM service_tasks WHERE id=?',(schedule_id,)).fetchone() if schedule_id else None
            result.append({'code':code,'decision':choice,'task_id':schedule_id,**({'run_at':persisted[0],'requested_run_at':decision.get('run_at'),'reason':persisted[1]} if persisted else {})})
        ledger.progress(c,state)
        c.execute('INSERT INTO completion_plans VALUES(?,?,?,?,?,?)',(task['id'],state['run_id'],event['event_id'],request_id,body,s.now()))
        return {'status':'committed','task_id':task['id'],'decisions':result}

def review(reason):
    import agent_plan as ap
    task=ap.active()
    if not task or task['kind']!='case_review' or not isinstance(reason,str) or not reason.strip():raise ValueError('仅当前检查任务可记录有依据的检查结果')
    ap.require_skills('review-service-case');ap.require_queries('completion_plan_context')
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');ledger.progress(c,tm.state())
        ledger.timeline(c,task['candidate_id'],'internal_review',task['id'],{'reason':reason,'contacts_user':False})
    return {'status':'reviewed','contacts_user':False}

def filter_candidates(c,state,items):
    if not enabled(state):return items
    if not ledger.exists(c):return []
    result=[]
    for item in items:
        link=c.execute('SELECT x.status FROM service_case_tasks l JOIN service_tasks t ON t.id=l.task_id JOIN service_cases x ON x.id=l.case_id WHERE t.run_id=? AND t.candidate_id=?',(state['run_id'],item['id'])).fetchone()
        constraint=c.execute('SELECT status FROM service_case_constraints WHERE run_id=? AND uid=? AND enrollment_id=?',(state['run_id'],state['uid'],item.get('evidence',{}).get('enrollment_id'))).fetchone()
        if constraint and constraint[0] in ('paused','stopped'):item={**item,'status':'not_eligible','reason':'家庭持续暂停或停止'}
        if link:
            if link[0] in ('paused','stopped','closed','resolved'):item={**item,'status':'not_eligible','reason':'事项暂停、关闭或目标已完成'}
            result.append(item)
    return result


def finish_internal(task,outcome,reason):
    import agent_plan as ap
    if outcome!='blocked':
        ap.require_skills(task['skill']);ap.require_queries('completion_plan_context')
        if task['kind']=='completion_plan' and not s.read('SELECT 1 FROM completion_plans WHERE task_id=?',(task['id'],)):
            raise ap.ServiceValidationError('先原子提交四项判断再结束规划')
        if task['kind']=='case_review':ap.require_queries('service_case_review')
    if outcome not in ('completed','no_contact','blocked'):raise ValueError('内部任务不循环延期联系')
    status='blocked' if outcome=='blocked' else 'done'
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('UPDATE service_tasks SET status=?,finished_at=?,result=? WHERE id=?',(status,s.now(),s.encoded({'outcome':outcome,'reason':reason,'contacts_user':False}),task['id']))
        c.execute("DELETE FROM metadata WHERE key='active_service_task' AND value=?",(s.encoded(task['id']),))
    return {'task_id':task['id'],'status':status,'contacts_user':False}

def prepare(task_id):
    import agent_plan as ap,observability as audit
    task=ap.active()
    if not task or task['id']!=task_id or not internal(task):raise ValueError('当前任务身份不一致')
    skill=audit.invoke('service_skill',{'name':task['skill']},lambda:ap.load_skill(task['skill']),origin='host')
    material=audit.invoke('completion_plan_context',{},context,origin='host')
    body=skill['body']+'\n'+s.encoded(skill.get('shared_rules',[]))
    return {'prompt':'内部任务，只读独立Skill与事实，不向家庭发送。先阅读下面Skill，再根据材料作出判断并调用工具提交，最后service_task_finish。\n<loaded_skill>\n'+body+'\n</loaded_skill>\n<task_material>\n'+s.encoded(material)+'\n</task_material>'+('\n<effective_tool_contract>'+__import__('tool_surface').guidance()+'</effective_tool_contract>' if __import__('host_tool_steps').enabled() else ''),
            'context_version':'completion-ledger-v1','prepared_skills':[{'name':skill['name'],'bundle_sha256':skill['bundle_sha256']}]}
