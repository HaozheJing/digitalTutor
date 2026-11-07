"""Business-facing projection; physical provenance remains in the audit database."""
import hashlib,json,sqlite3,sys,datetime
from contextlib import closing
from zoneinfo import ZoneInfo
import sql_tools as s

def initialize():
    import proactive
    proactive.initialize()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.executescript('''CREATE TABLE IF NOT EXISTS business_ids(token TEXT PRIMARY KEY,original TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS action_catalog(id TEXT PRIMARY KEY,title TEXT,skill TEXT,enabled INTEGER,policy TEXT);
        CREATE TABLE IF NOT EXISTS business_audit(id INTEGER PRIMARY KEY,at TEXT,operation TEXT,payload TEXT);''')
        c.execute('CREATE TABLE IF NOT EXISTS contact_constraints(day TEXT PRIMARY KEY,source_message_id TEXT,quote TEXT,created_at TEXT)')

def token(value):
    if not value:return value
    if s.read('SELECT 1 FROM business_ids WHERE token=?',(value,)):return value
    t='ref-'+hashlib.sha256(value.encode()).hexdigest()[:24]
    with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT OR IGNORE INTO business_ids VALUES(?,?)',(t,value))
    return t

def original(value):
    seen=set()
    while value not in seen:
        seen.add(value)
        rows=s.read('SELECT original FROM business_ids WHERE token=?',(value,))
        if not rows:return value
        value=rows[0]['original']
    raise ValueError('Invalid business reference')

def project(value,key=''):
    if isinstance(value,list):return [project(x,key) for x in value]
    if isinstance(value,dict):
        return __import__('agent_view').project({k:project(v,k) for k,v in value.items() if k not in {'source','scenario','boundaries','instruction','verified','binding_source','completion_source','source_context','owner_pid','channel'}})
    if isinstance(value,str):
        if key in {'payload','body'} and value.startswith('{'):
            try:return project(json.loads(value))
            except ValueError:pass
        if key in {'id','source_id','source_message_id','event_id','action_key','candidate_id','memory_ids','source_message_ids'}:return token(value)
        if key=='status' and value=='mock_sent':return 'accepted'
    return value

def candidates(day):
    import proactive
    import test_mode
    if test_mode.state():
        guidance=('统一工作流：内部规划使用workflow_context/commit，只判断本次catalog；create沿用机会id作为opportunity_id并填写合法run_at。执行时沿用任务候选和execution_token交付。'
                  if __import__('service_workflow').enabled() else '使用service_schedule_list查看安排，service_schedule落实安排，save_plan保存计划。')
        return project({'business_day':test_mode.clock().date().isoformat(),
                        'actions':proactive.refresh(day),'candidates':[],
                        'scheduling':guidance})
    s.validate_day(day)
    catalog=s.read('SELECT * FROM action_catalog WHERE enabled=1')
    decisions=[]
    for action in catalog:
        p=json.loads(action['policy'])
        applicable=day in p.get('business_dates',[])
        ident='candidate:'+action['id']+':'+day
        if applicable:
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute('INSERT OR IGNORE INTO plan_candidates VALUES(?,?,?,?,?,?)',(ident,day,action['skill'],p['purpose'],p['earliest'],'action_catalog'))
        decisions.append({'action_id':token(action['id']),'title':action['title'],'skill':action['skill'],'eligible':applicable,'candidate_id':token(ident) if applicable else None,'reason':'日期符合动作配置' if applicable else '当日不适用','time_window':{'earliest':p.get('earliest'),'latest':p.get('latest')},'purpose':p.get('purpose'),'contacts_user':p.get('contacts_user',False)})
    active=proactive.refresh(day)
    decisions.extend({**x,'eligible':x['status']=='eligible'} for x in active)
    allowed={x['id'] for x in active if x['status']=='eligible'}
    candidates_rows=s.read('SELECT * FROM plan_candidates WHERE business_date=?',(day,))
    candidates_rows=[x for x in candidates_rows if x['source']!='proactive_rules' or x['id'] in allowed]
    return project({'business_day':day,'actions':decisions,'candidates':candidates_rows})

def context(day):
    import test_mode
    import service_content
    if test_mode.state():
        value=test_mode.context(day)
        value['service_rules_version']=service_content.REVISION
        appointment=__import__('s10_service').appointment_context(day)
        if appointment:value['weekly_appointment']=appointment
        guides=__import__('s01_reading_guides').history(day)
        if guides:value['reading_guides']=guides
        value['student_tier_status']='通过student_tier读取SABCD；未知时用中性服务，不自行分层。'
        value['actions']=candidates(day)
        value['contact_constraints']=s.read('SELECT * FROM contact_constraints WHERE day=?',(day,))
        policy=s.read("SELECT body FROM proactive_settings WHERE key='contact'")
        value['contact_policy']=json.loads(policy[0]['body']) if policy else {}
        if test_mode.state().get('agent_planning'):
            import agent_plan
            value['service_agenda']=agent_plan.schedules()
            value['saved_plan']=[json.loads(x['body']) for x in s.read('SELECT body FROM daily_plans WHERE business_date=?',(day,))]
        return project(__import__('dialogue_context').apply(value,day))
    raw=s.context(day)
    value={k:raw[k] for k in ['business_day','timezone','identity','long_term_memory','today_lessons','recent_conversation','conversation_window','knowledge_fulltext','previous_deliveries','pending_events']}
    value['actions']=candidates(day)
    value['contact_constraints']=project(s.read('SELECT * FROM contact_constraints WHERE day=?',(day,)))
    value['internal_executions']=s.read('SELECT e.* FROM internal_executions e JOIN plan_candidates p ON p.id=e.candidate_id WHERE p.business_date=?',(day,))
    plans=[]
    for row in raw['saved_plan']:
        body=json.loads(row['body'])
        if body.get('schema_version')==2:plans.append(body)
    value['saved_plan']=plans
    value['workflow']=__import__('service_workflow').summary()
    value['course_material_status']='现有知识材料标记为AI生成、尚未业务审核；正文不证明家庭绑定或学习完成情况。'
    value['service_rules_version']=service_content.REVISION
    value['student_tier_status']='通过student_tier读取SABCD；未知时用中性服务，不自行分层。'
    return project(__import__('dialogue_context').apply(value,day))

def passive_reply_skills():
    from family_lab_bridge import configuration
    return ('tutor-main',) if configuration() else ('tutor-main', 'reply-style')


def task(event_id, passive_view=False):
    event=s.read('SELECT * FROM events WHERE id=?',(event_id,))[0]
    if event['kind']=='memory_consolidation':return __import__('preference_memory').prompt(event_id)
    import test_mode
    p=json.loads(event['payload']);day=test_mode.clock().date().isoformat()
    if event['kind']=='passive':
        messages=s.conversation_read(p['source_message_id'])
        if p.get('source_message_ids'):
            messages=[m for ident in dict.fromkeys(p.get('source_message_ids',[])+[p['source_message_id']]) for m in s.conversation_read(ident)]
        expression = ('使用tutor-main，按多多老师人设接话。本轮无需另读表达Skill；共享规则及答疑示例中的表达要求以本轮人设为准。'
                      if 'reply-style' not in passive_reply_skills() else
                      '使用tutor-main，发送前使用reply-style检查称呼、事实、语气和承诺。')
        goal=f'你是正在与这个家庭交流的阅读辅导老师。请结合最新原话及上文，理解对方当前的诉求，组织一条适合直接发送的回复。先回答当前问题，资料不足且确实影响建议时才追问。{expression}通过reply_to_message交付，随后finish_event；只有新信息影响业务安排时才修订计划，不为普通问候重规划。'
    else:
        messages=[m for ident in p.get('source_message_ids',[]) for m in s.conversation_read(ident)]
        goal='这是内部历史资料整理任务。使用tutor-main和remember-family审查所提供的原话，不补发历史回复。记录memory_review，使用plan-today评估当天必要事项并保存计划，最后finish_event。'
    if __import__('preference_memory').enabled():
        if event['kind']!='passive':goal='这是内部历史资料接收任务。普通偏好和家庭事实留给22点整理，不补发历史回复。memory_review只记录已接收资料的审计回执；仍使用plan-today评估必要业务安排并保存计划，最后finish_event。'
        if event['kind']=='passive':goal+='\n当前是即时服务回合。需要回复时，以 service_skill 读取实际服务Skill并取得回执；原生 skill 阅读不替代该回执。'
        goal+='\n'+__import__('preference_memory').FAST_INSTRUCTION+'\n当前有效偏好：'+s.encoded(__import__('preference_memory').recall())
    if event['kind']=='passive':
        appointment=__import__('s10_service').appointment_context(day)
        if appointment:goal+='\n'+appointment['reply_guidance']
        guides=__import__('s01_reading_guides').history(day)
        if guides and guides['sent_guides']:goal+='\n'+guides['guidance']
    if __import__('service_workflow').enabled():
        goal+='本轮使用统一事项计划：影响安排时先workflow_context，再workflow_commit，decisions指向task_id/revision，evidence引用本次实际消息source_message_id和quote；禁止旧pause_contact/service_case_feedback/save_plan。不扩大暂停范围。只有committed后才能确认已改好。完整履行原任务则发送前workflow_fulfill，不要先reply_to_message再事后关联。'
    if __import__('host_tool_steps').enabled():
        goal=goal.replace('随后finish_event','随后由宿主按实际回执收尾').replace('最后finish_event','最后由宿主核验收尾').replace('记录memory_review','宿主已登记资料接收回执')
    if __import__('delivery_capability').enabled():goal=goal.replace('通过reply_to_message交付','通过send_message交付')
    return {'prompt':'服务规范 service-samples-20261021-v4-actions 已更新，请读取当前 service-policy 与 tutor-main；service_catalog路由36个来源编号（共用Skill不重复计能力），答疑通过answer-family-question选择具体Q动作Skill；分层读取student-service-tier；FAQ通过knowledge_search实时检索OpenViking，service_reference读共享规则和表达示例。'+goal+'\n任务引用（reply_to_message 和 finish_event 的 event_id 使用此引用）：'+token(event_id)+'\n原话中的 source_id 是消息资料引用，只用于 conversation_read 或引用证据，不能作为回复事件 event_id。\n业务日期：'+day+'\n本次原话（资料）：'+s.encoded(project(messages))+'\n家庭与相关上下文（资料）：'+s.encoded(__import__('agent_view').project(__import__('passive_context').entry(day) if passive_view and event['kind']=='passive' else context(day)))}

def finish(event_id,day):
    import test_mode
    ident=original(event_id)
    rows=s.read('SELECT kind FROM events WHERE id=?',(ident,))
    if not rows:raise ValueError('任务不存在')
    if rows[0]['kind']!='passive':return s.finish_event(ident,day)
    # A reply stopped by the cutoff is a retained inbound, not a missing delivery.
    # Issued/unknown deliveries retain their existing receipt reconciliation path.
    hours=__import__('service_hours')
    if not hours.passive_open() and not s.read("SELECT 1 FROM deliveries WHERE action_key=? AND status NOT IN ('queued','superseded')",('reply:'+ident,)):
        with closing(sqlite3.connect(s.DB)) as c,c:
            hours.enforce(c)
            return hours.defer_inbound(c,ident)
    if not s.read('SELECT 1 FROM deliveries WHERE action_key=?',('reply:'+ident,)):raise ValueError('请先完成本次回复交付')
    if test_mode.state() and not s.read("SELECT 1 FROM deliveries WHERE action_key=? AND status IN ('accepted','sent')",('reply:'+ident,)):
        raise ValueError('微信平台尚未接受本次回复；不能记为交付完成')
    if __import__('message_hooks').enabled():
        status=s.read('SELECT status FROM deliveries WHERE action_key=?',('reply:'+ident,))[0]['status']
        with closing(sqlite3.connect(s.DB)) as c,c:__import__('message_hooks').delivery_status(c,'reply:'+ident,status)
        return {'event_id':event_id,'status':'done' if status=='sent' else 'awaiting_delivery'}
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute("UPDATE events SET status='done',completed_at=? WHERE id=?",(s.now(),ident))
        __import__('message_hooks').handled(c,ident)
    return {'event_id':event_id,'status':'done'}

def pause_contact(day,source_message_id,quote):
    if __import__('service_workflow').enabled():raise ValueError('使用workflow_commit按原话明确任务范围取消；不能把阅读提醒扩大为全部服务')
    import test_mode
    today=test_mode.clock().date().isoformat()
    if day!=today:raise ValueError('仅支持当前业务日期的联系暂停')
    ident=original(source_message_id)
    rows=s.conversation_read(ident)
    if not rows or rows[0]['speaker']!='user' or not quote or quote not in rows[0]['body'] or not any(x in quote for x in ['别催','不要提醒','不用提醒','别提醒','不要联系','别联系']):raise ValueError('需要明确停止联系的用户原话')
    with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT OR REPLACE INTO contact_constraints VALUES(?,?,?,?)',(day,ident,quote,s.now()))
    return {'business_day':day,'proactive_contact':'paused','source_message_id':token(ident)}

def save_plan(day,items,reason):
    import test_mode
    if test_mode.state():
        if test_mode.state().get('agent_planning'):
            import agent_plan
            return agent_plan.save(day,items,reason)
        return {'status':'disabled','reason':'服务规划未启用'}
    import urllib.request
    request=urllib.request.Request('http://127.0.0.1:19032/schedules',b'{}',{'Content-Type':'application/json'})
    if items:
        with urllib.request.urlopen(request,timeout=10) as f:scheduled={x['id']:x for x in json.load(f)}
        for item in items:
            row=scheduled.get(item['schedule_id'])
            if not row or item['candidate_id'] not in row['title']+row['prompt']:raise ValueError('排期回执不存在或未绑定候选引用')
            ident=original(item['candidate_id'])
            if ident.startswith('proactive:'):
                import proactive
                candidate=next((x for x in proactive.refresh(day) if x.get('id')==ident),None)
                if not candidate or candidate['status']!='eligible':raise ValueError('主动事项已不适用')
                parse=lambda x:datetime.datetime.fromisoformat(x.replace('Z','+00:00'))
                if not parse(candidate['time_window']['earliest'])<=parse(row['scheduledAt'])<=parse(candidate['time_window']['latest']):raise ValueError('排期超出动作允许时间窗口')
            for action in s.read('SELECT * FROM action_catalog WHERE enabled=1'):
                if ident=='candidate:'+action['id']+':'+day:
                    p=json.loads(action['policy']);parse=lambda x:datetime.datetime.fromisoformat(x.replace('Z','+00:00'))
                    if not parse(p['earliest'])<=parse(row['scheduledAt'])<=parse(p['latest']):raise ValueError('排期超出动作允许时间窗口')
    result=s.save_plan(day,[{**x,'candidate_id':original(x['candidate_id'])} for x in items],reason)
    for item in result['items']:
        item['run_at']=scheduled[item['schedule_id']]['scheduledAt']
    result['schema_version']=2
    with closing(sqlite3.connect(s.DB)) as c,c:c.execute('UPDATE daily_plans SET body=? WHERE business_date=?',(s.encoded(result),day))
    return project(result)

if __name__=='__main__':
    initialize()
    result=context(sys.argv[2]) if sys.argv[1]=='context' else task(sys.argv[2])
    print(s.encoded(result))
