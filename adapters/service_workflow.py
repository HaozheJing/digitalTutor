"""Versioned planning/authorization adapter over the existing task and delivery stores.

No model client, timer or sender lives here. SQLite serializes each bound family's
mutations; the native host owns admission and wakeups, and wechat_test owns transport.
"""
import datetime as dt
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
import sql_tools as s
import test_mode as tm

MODE = 'agent-workflow-v1'
INTERNAL = {'plan', 'completion_plan', 'case_review'}
FINAL = {'done', 'cancelled', 'no_contact', 'expired', 'blocked'}
# These are existing fixed entry times, not instructions to create a task.
DAILY = {
    'remind-reading': ('s01', '06:30', None),
    'support-zero-completion': ('action', '19:00', None),
    'rebuild-reading-rhythm': ('s07', '19:00', 2),
    'close-week-reading-gap': ('s08', '18:00', 4),
    'invite-reading-interaction': ('interaction', '19:00', 4),
    'send-festival-wishes': ('festival', '09:30', None),
    'plan-weekend-makeup': ('weekly', '20:00', 4),
    'weekly-learning-review': ('weekly', '09:00', 5),
    'celebrate-important-day': ('action', '09:30', None),
    'care-calendar-day': ('action', '09:30', None),
}

def default_mode():
    p = s.R / 'config/service-workflow.json'
    return p.is_file() and json.loads(p.read_text()).get('enabled') is True

def enabled(state=None):
    return (state or tm.state() or {}).get('service_workflow_mode') == MODE

def digest(value):
    return hashlib.sha256(s.encoded(value).encode()).hexdigest()

def initialize():
    import service_ledger
    service_ledger.initialize()
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS service_task_versions(
          task_id TEXT PRIMARY KEY REFERENCES service_tasks(id),
          case_id TEXT NOT NULL UNIQUE REFERENCES service_cases(id),uid TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 1,skill_version TEXT NOT NULL,
          source TEXT NOT NULL,goal TEXT NOT NULL,window_start TEXT NOT NULL,
          dedupe_key TEXT NOT NULL,execution_token TEXT,execution_facts TEXT,
          needs_revalidation INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS workflow_task_dedupe ON service_task_versions(uid,dedupe_key);
        CREATE TABLE IF NOT EXISTS service_workflow_contexts(
          id TEXT PRIMARY KEY,run_id TEXT,owner TEXT,digest TEXT,body TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS service_workflow_commits(
          run_id TEXT,request_id TEXT,body TEXT,result TEXT,created_at TEXT,
          PRIMARY KEY(run_id,request_id));
        CREATE TABLE IF NOT EXISTS service_workflow_constraints(
          id TEXT PRIMARY KEY,run_id TEXT,uid TEXT,scope TEXT,starts_at TEXT,ends_at TEXT,source TEXT);
        CREATE TABLE IF NOT EXISTS service_send_authorizations(
          operation_id TEXT PRIMARY KEY,action_key TEXT,task_id TEXT,revision INTEGER,
          facts TEXT,authorized_at TEXT,boundary TEXT);
        CREATE TRIGGER IF NOT EXISTS workflow_one_task_per_case BEFORE INSERT ON service_case_tasks
        WHEN EXISTS(SELECT 1 FROM service_task_versions v WHERE v.case_id=NEW.case_id AND v.task_id<>NEW.task_id)
        BEGIN SELECT RAISE(ABORT,'one action per workflow matter'); END;
        CREATE TRIGGER IF NOT EXISTS workflow_task_projection AFTER UPDATE ON service_tasks
        WHEN EXISTS(SELECT 1 FROM service_task_versions WHERE task_id=NEW.id)
        BEGIN
          UPDATE service_task_versions SET revision=revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE task_id=NEW.id;
          UPDATE service_cases SET status=CASE NEW.status WHEN 'done' THEN 'completed' ELSE NEW.status END,
            revision=(SELECT revision FROM service_task_versions WHERE task_id=NEW.id),
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id=(SELECT case_id FROM service_task_versions WHERE task_id=NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS workflow_revision_projection AFTER UPDATE ON service_task_versions
        BEGIN
          UPDATE service_cases SET revision=NEW.revision,updated_at=NEW.updated_at WHERE id=NEW.case_id;
        END;
        ''')

def rows(c, query, args=()):
    cur = c.execute(query, args)
    return [dict(zip([d[0] for d in cur.description], r)) for r in cur]

def bound_state(c):
    value = json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
    if not enabled(value):
        raise ValueError('当前轮次未启用统一事项规划')
    return value

def task_rows(c, state):
    result = rows(c, '''SELECT t.*,v.case_id,v.uid,v.revision,v.skill_version,v.source,v.goal,
      v.window_start,v.dedupe_key,v.needs_revalidation FROM service_tasks t
      JOIN service_task_versions v ON v.task_id=t.id WHERE t.run_id=? AND v.uid=? ORDER BY t.due_at,t.id''',
      (state['run_id'], state['uid']))
    from execution_contract import task_projection
    return [{**task,'execution_state':task_projection(c,task)} for task in result]

def messages(c):
    return rows(c, "SELECT rowid AS cursor,source_id,body,sha256 FROM messages WHERE speaker='user' AND visible=1 ORDER BY rowid DESC LIMIT 60")

def owner(c):
    task = c.execute("SELECT value FROM metadata WHERE key='active_service_task'").fetchone()
    if task:
        tid = json.loads(task[0])
        row = c.execute("SELECT kind,status FROM service_tasks WHERE id=?", (tid,)).fetchone()
        if row and row[1] == 'running':
            return {'type': 'daily' if row[0] == 'plan' else 'execution', 'id': tid}
    events = rows(c, "SELECT id,payload FROM events WHERE kind='passive' AND status='dispatched' ORDER BY created_at")
    if events:
        ids = []
        for event in events:
            p = json.loads(event['payload'])
            ids.extend(p.get('source_message_ids', []) + ([p['source_message_id']] if p.get('source_message_id') else []))
        return {'type': 'dialogue', 'id': events[0]['id'], 'event_ids': [e['id'] for e in events], 'source_message_ids': ids}
    raise ValueError('需要当前原生规划、执行或真实对话上下文')

def facts(c, state, skills=()):
    import service_content
    return {'run_id': state['run_id'], 'uid': state['uid'],
        'message_cursor': c.execute("SELECT COALESCE(MAX(rowid),0) FROM messages WHERE speaker='user' AND visible=1").fetchone()[0],
        'learning': tm.snapshot(),
        'constraints': rows(c, 'SELECT * FROM contact_constraints ORDER BY day') + rows(c,
            'SELECT * FROM service_workflow_constraints WHERE run_id=? AND uid=? ORDER BY id', (state['run_id'], state['uid'])),
        'legacy_constraints': rows(c, 'SELECT * FROM service_case_constraints WHERE run_id=? AND uid=?', (state['run_id'], state['uid'])),
        'skills': {name: service_content.bundle_digest(name) for name in sorted(set(skills))}}

def catalog(c, state):
    import proactive, service_content, course_service
    # Evaluate existing facts without creating a task, including explicit unknowns.
    candidates = proactive.evaluate(c, state['as_of'][:10])
    day = state['as_of'][:10]
    weekday = dt.date.fromisoformat(day).weekday()
    result = []
    for name, (kind, hour, dow) in DAILY.items():
        contract=planning_contract(name)
        if contract.get('enabled') is False:continue
        hour=contract.get('fixed_time',hour);dow=contract.get('weekday',dow)
        if dow is not None and dow != weekday:
            continue
        file = service_content.skill_file(name)
        entry = {'skill': name, 'skill_version': service_content.bundle_digest(name), 'kind': kind,
            'body': file.read_text(), 'timing': {'mode': 'fixed', 'at': day+'T'+hour+':00+08:00'},
            'enabled':True,'readiness':'existing_adapter','allowed_triggers': ['daily'], 'planning_contract':contract,
            'references':[{ 'path':str(p.relative_to(file.parent)), 'body':p.read_text()} for p in service_content.reference_files(name)],'opportunities': []}
        if name == 'remind-reading':
            profile = tm.snapshot()['profile']
            schedule = course_service.course_schedule(day, profile['class_type'], profile['cohort'])
            if schedule['status'] == 'scheduled':
                entry['opportunities'] = [{'id': 's01:'+day, 'dedupe_key': state['run_id']+':s01:'+day,
                    'status': 'eligible', 'title': '今日课前阅读邀请', 'evidence': schedule,
                    'time_window': {'earliest': entry['timing']['at'], 'latest': day+'T21:59:59+08:00'}}]
        else:
            # Host-owned cards must be planned as highlight tasks. Publishing
            # the same opportunity under a plain action first causes commit to
            # bind it to the wrong delivery interface.
            entry['opportunities'] = [x for x in candidates if x.get('skill') == name and not x.get('evidence', {}).get('event_id') and x.get('delivery_owner') != 'host_highlights']
        result.append(entry)
    # Only already observed assets may be planned; no future completion prediction.
    for item in candidates:
        if item.get('delivery_owner') != 'host_highlights':
            continue
        name = item['skill']
        result.append({'skill': name, 'skill_version': service_content.bundle_digest(name), 'kind': 'highlight',
            'body': service_content.skill_file(name).read_text(), 'allowed_triggers': ['observed_asset'],
            'timing': {'mode': 'window'}, 'opportunities': [item]})
    # One observed birthday/card opportunity must not coexist with an empty
    # daily entry advertising a different timing contract for the same Skill.
    observed={x['skill'] for x in result if x['kind']=='highlight' and x['opportunities']}
    result=[x for x in result if x['kind']=='highlight' or x['skill'] not in observed or x['opportunities']]
    for entry in result:
        for item in entry['opportunities']:
            if entry['skill']!='remind-reading':item['contact_spacing']=proactive.contact_spacing(c,[{**item,'rule':entry['skill']}],dt.datetime.fromisoformat(state['as_of']))
            due=entry['timing'].get('at') or item.get('time_window',{}).get('earliest') or state['as_of']
            if item.get('status')=='eligible' and planning_paused(c,state,entry['skill'],due):
                item.update(status='not_eligible',reason='家庭明确暂停该服务范围',pause_blocked=True)
    return result

def planning_contract(name):
    import service_content,re
    path=service_content.skill_file(name).parent/'references/planning-contract.md'
    if not path.exists():return {'source':'existing_fixed_entry','enabled':True}
    matched=re.search(r'```json\s*(.*?)\s*```',path.read_text(),re.S)
    if not matched:raise ValueError('发布规划契约不可解析，停止安排')
    result=json.loads(matched[1])
    if result.get('skill')!=name:raise ValueError('规划契约Skill不一致')
    if result.get('fixed_time') and not re.fullmatch(r'\d{2}:\d{2}',result['fixed_time']):raise ValueError('规划时间格式无效')
    return result

def pause_targets():
    # A service category exists independently of today's opportunities/tasks.
    # This registry grants no scheduling or sending permission.
    from service_catalog import ROWS, SUPPLEMENTAL_ROWS
    known={row[2]:row[1] for row in ROWS+SUPPLEMENTAL_ROWS if not row[0].startswith('Q')}
    known.update({'care-calendar-day':'家庭节点关怀','invite-reading-interaction':'阅读互动'})
    return [{'skill':name,'title':title} for name,title in sorted(known.items())]

def pause_window_today(state):
    start=stamp(state['as_of'])
    return {'starts_at':start,'ends_at':start[:10]+'T23:59:59+08:00','timezone':'Asia/Shanghai'}

def envelope(c, state, who):
    skills = catalog(c, state) if who['type'] == 'daily' else []
    tasks = task_rows(c, state)
    return {'business_time': state['as_of'], 'owner': who, 'facts': facts(c, state, [r['skill'] for r in tasks]+[r['skill'] for r in skills]),
        'tasks': tasks, 'messages': messages(c), 'catalog': skills,
        'human_handoffs':__import__('execution_contract').handoffs(c,state),
        'pause_targets':pause_targets(), 'pause_window_today':pause_window_today(state),
        'deliveries': rows(c, 'SELECT l.task_id,d.action_key,d.status FROM service_links l JOIN deliveries d ON d.action_key=l.action_key JOIN service_tasks t ON t.id=l.task_id WHERE t.run_id=? ORDER BY l.task_id,d.action_key', (state['run_id'],)),
        'gaps': ['学情仍由模拟提供器提供；完课使用统一POST。', '非完课的新高光/榜单/节点缺少独立事实通知；只在已有规划唤醒时观察，不另加周期巡检。', 'S05仅接初次关怀；48小时复约核验、真人交接及其他未具备数据/交付契约的动作不开放。']}

def context():
    initialize()
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        state = bound_state(c); who = owner(c); data = envelope(c, state, who)
        token = uuid.uuid4().hex
        c.execute('INSERT INTO service_workflow_contexts VALUES(?,?,?,?,?,?)',
            (token, state['run_id'], s.encoded(who), digest(data), s.encoded(data), s.now()))
    return __import__('working_context').model({**data, 'context_token': token})

def check_context(c, token):
    state = bound_state(c)
    saved = c.execute('SELECT owner,digest,body FROM service_workflow_contexts WHERE id=? AND run_id=?', (token,state['run_id'])).fetchone()
    if not saved:
        raise ValueError('上下文令牌不存在')
    who = owner(c)
    if s.encoded(who) != saved[0] or digest(envelope(c,state,who)) != saved[1]:
        raise ValueError('计划、消息、学情或Skill已变化，请重新读取')
    return state, who, json.loads(saved[2])

def add(c, state, item, contract, due, reason, source, source_key):
    import completion_planner, service_ledger
    if planning_paused(c,state,contract['skill'],due):raise ValueError('该安排处于家庭明确暂停范围')
    key = item['dedupe_key']
    existing = c.execute('SELECT task_id FROM service_task_versions WHERE uid=? AND dedupe_key=?', (state['uid'],key)).fetchone()
    if existing:
        raise ValueError('已有同一服务实例，须keep，不重建')
    tid = 'service-workflow-'+service_ledger.ident(state['run_id'],state['uid'],key)
    cid = 'case-'+service_ledger.ident(tid)
    c.execute('INSERT INTO service_cases VALUES(?,?,?,?,?,?,?,?,?)', (cid,state['run_id'],state['uid'],item.get('evidence',{}).get('enrollment_id'),contract['skill'],'planned',1,s.now(),s.now()))
    completion_planner.insert(c,state,tid,contract['kind'],contract['skill'],due,item['time_window']['latest'],None if contract['kind']=='s01' else item['id'],reason)
    c.execute('INSERT INTO service_task_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (tid,cid,state['uid'],1,contract['skill_version'],s.encoded(source),reason,item['time_window']['earliest'],key,None,None,0,s.now()))
    c.execute('INSERT INTO service_case_tasks VALUES(?,?,?,?)',(tid,cid,source_key,contract['skill']))
    if contract['kind']!='s01':
        c.execute('INSERT OR REPLACE INTO proactive_candidates VALUES(?,?,?,?,?,?,?)', (item['id'],item['day'],item['rule'],item['scope'],s.encoded(item),item['status'],s.now()))
    service_ledger.timeline(c,cid,'planned',tid,{'task_id':tid,'reason':reason,'source':source,'skill_version':contract['skill_version']})
    return tid

def stamp(value):
    parsed=dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:raise ValueError('时间必须带时区')
    return parsed.astimezone(tm.TZ).isoformat(timespec='seconds')

def amend(c,state,decision,who):
    task = next((r for r in task_rows(c,state) if r['id']==decision.get('task_id')),None)
    if not task or type(decision.get('revision')) is not int or task['revision'] != decision['revision']:
        raise ValueError('任务版本冲突：keep/reschedule/cancel/no_contact/blocked必须引用workflow_context.tasks中的task_id和整数revision；刷新workflow_context后沿用原值，不引用内部规划任务')
    choice=decision['decision']
    if choice=='keep':
        if task['needs_revalidation']:
            import service_content
            contract=planning_contract(task['skill'])
            if contract.get('enabled') is False:raise ValueError('该服务已停用，不能keep')
            if contract.get('fixed_time') and task['due_at']!=task['day']+'T'+contract['fixed_time']+':00+08:00':raise ValueError('新规则时点变化，需合法改时或取消')
            if task['status'] not in ('planned','deferred') or task['due_at']<state['as_of']:
                raise ValueError('旧时点已过，不能以keep补发')
            c.execute('UPDATE service_task_versions SET skill_version=?,needs_revalidation=0,revision=revision+1,updated_at=? WHERE task_id=?',
                (service_content.bundle_digest(task['skill']),s.now(),task['id']))
        return task['id']
    if task['status'] not in ('planned','deferred','running'):
        raise ValueError('已结束或已在途的交付不能重新安排；核对原交付')
    if c.execute('SELECT 1 FROM service_links WHERE task_id=?',(task['id'],)).fetchone():
        raise ValueError('已有在途交付，不能声称撤回或重发')
    if who['type']=='execution' and who['id']!=task['id']:
        raise ValueError('执行模式只可修订自身任务')
    if choice=='reschedule':
        due=stamp(decision['run_at'])
        # Fixed-time Skills stay fixed. Event/asset windows use their published bounds.
        current_contract=planning_contract(task['skill'])
        if current_contract.get('enabled') is False:raise ValueError('该服务已停用')
        if task['skill'] in DAILY and due != task['day']+'T'+current_contract.get('fixed_time',DAILY[task['skill']][1])+':00+08:00':
            raise ValueError('该Skill规定固定时点，不能承诺其他时间')
        if not max(state['as_of'],task['window_start'])<=due<=task['expires_at']:
            raise ValueError('超出原任务有效窗口')
        c.execute("UPDATE service_tasks SET due_at=?,status='planned',result=?,reason=? WHERE id=?",(due,s.encoded({'reason':decision['reason']}),decision['reason'],task['id']))
        if task['needs_revalidation']:
            import service_content
            c.execute('UPDATE service_task_versions SET skill_version=?,needs_revalidation=0 WHERE task_id=?',(service_content.bundle_digest(task['skill']),task['id']))
    elif choice in ('cancel','no_contact','blocked'):
        c.execute('UPDATE service_tasks SET status=?,finished_at=?,result=? WHERE id=?',('cancelled' if choice=='cancel' else choice,s.now(),s.encoded({'reason':decision['reason']}),task['id']))
    else:raise ValueError('不支持的局部修改')
    import service_ledger
    service_ledger.timeline(c,task['case_id'],'amendment',str(task['revision']),{'decision':decision,'owner':who})
    return task['id']

def constraint_blocks(c,state,skill,task_id=None,at=None):
    at=stamp(at or state['as_of'])
    for row in rows(c,'SELECT scope,starts_at,ends_at FROM service_workflow_constraints WHERE run_id=? AND uid=?',(state['run_id'],state['uid'])):
        scope=json.loads(row['scope'])
        matches=(scope.get('kind')=='all' or
                 (scope.get('kind')=='skill' and bool(skill) and scope.get('skill')==skill) or
                 (scope.get('kind')=='task' and bool(task_id) and scope.get('task_id')==task_id))
        if row['starts_at']<=at<=row['ends_at'] and matches:return True
    return False

def planning_paused(c,state,skill,due):
    # A fixed earlier due time does not authorize an outreach attempt now.
    return constraint_blocks(c,state,skill,at=max(stamp(state['as_of']),stamp(due)))

def commit(request_id=None,context_token=None,decisions=None,evidence=None,contact_constraint=None):
    if not isinstance(request_id,str) or not 8<=len(request_id)<=160 or not isinstance(context_token,str) or not context_token or not isinstance(decisions,list):
        raise ValueError('workflow_commit必须同时传request_id、context_token和decisions列表；decisions逐项包含skill、decision、reason，按本次catalog判断，不能只传请求ID和上下文令牌')
    import business
    choices=('create','keep','reschedule','cancel','skip','unknown','no_contact','blocked')
    for index,d in enumerate(decisions):
        if not isinstance(d,dict) or d.get('decision') not in choices:
            raise ValueError(f'decisions[{index}].decision只能是 '+','.join(choices)+'；创建事项用create，不支持schedule；请按workflow_context中的catalog逐项判断')
        if not isinstance(d.get('reason'),str) or not d['reason'].strip():
            raise ValueError(f'decisions[{index}].reason需要非空事实依据')
    decisions=[{**d,**{k:business.original(d[k]) for k in ('task_id','opportunity_id') if k in d}} for d in decisions]
    if evidence is not None and (not isinstance(evidence,list) or any(not isinstance(e,dict) or not isinstance(e.get('source_message_id'),str) or not e['source_message_id'] or not isinstance(e.get('quote'),str) or not e['quote'] for e in evidence)):
        raise ValueError('evidence仅用于本轮对话原话，格式为[{source_message_id:消息引用,quote:原话}]；内部规划的事实依据写在decisions各项reason中，不传顶层evidence')
    evidence=[{**e,'source_message_id':business.original(e['source_message_id'])} for e in evidence] if evidence else evidence
    if contact_constraint and contact_constraint.get('scope',{}).get('task_id'):
        contact_constraint={**contact_constraint,'scope':{**contact_constraint['scope'],'task_id':business.original(contact_constraint['scope']['task_id'])}}
    initialize(); body=s.encoded([context_token,decisions,evidence,contact_constraint])
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');state=bound_state(c)
        prior=c.execute('SELECT body,result FROM service_workflow_commits WHERE run_id=? AND request_id=?',(state['run_id'],request_id)).fetchone()
        if prior:
            if prior[0]!=body:raise ValueError('请求ID内容冲突')
            return {**json.loads(prior[1]),'duplicate':True}
        state,who,data=check_context(c,context_token)
        if who['type']=='dialogue':
            if not evidence:raise ValueError('对话修订必须引用本轮真实原话')
            for ref in evidence:
                if ref.get('source_message_id') not in who['source_message_ids']:
                    raise ValueError('来源不在本轮实际处理消息集合内')
                source=c.execute('SELECT body FROM messages WHERE source_id=?',(ref['source_message_id'],)).fetchone()
                if not ref.get('quote') or ref['quote'] not in source[0]:raise ValueError('原话不匹配')
        saved_constraint=None
        if who['type']=='dialogue' and not decisions and not contact_constraint:raise ValueError('没有待提交的事项修改或暂停约束，不能返回修改成功')
        if contact_constraint:
            if who['type']!='dialogue':raise ValueError('联系暂停必须来自本轮家庭原话')
            if not isinstance(contact_constraint,dict):raise ValueError('暂停约束必须为对象')
            scope=contact_constraint.get('scope')
            if not isinstance(scope,dict) or scope.get('kind') not in ('task','skill','all'):raise ValueError('暂停范围无效')
            fields={'task':{'kind','task_id'},'skill':{'kind','skill'},'all':{'kind'}}[scope['kind']]
            if set(scope)!=fields:raise ValueError('暂停范围字段必须与kind一致，不能混用任务、类别和全部范围')
            if contact_constraint.get('period')=='today':
                if set(contact_constraint)-{'scope','period'}:raise ValueError('today时间范围由宿主确定，不传starts_at/ends_at')
                window=pause_window_today(state);start=window['starts_at'];end=window['ends_at']
            else:
                if set(contact_constraint)!={'scope','starts_at','ends_at'}:raise ValueError('暂停须传period=today或明确起止时间')
                start=stamp(contact_constraint['starts_at']);end=stamp(contact_constraint['ends_at'])
            if start>end:raise ValueError('暂停时间无效')
            quotes=' '.join(r['quote'] for r in evidence)
            if scope['kind']=='all' and not any(x in quotes for x in ['所有','任何','不要联系','别联系','别发消息','不要发消息']):raise ValueError('原话未授权暂停所有主动服务')
            if scope['kind']=='task' and not any(r['id']==scope.get('task_id') for r in data['tasks']):raise ValueError('暂停任务不属于当前家庭')
            if scope['kind']=='skill' and scope.get('skill') not in {r['skill'] for r in data['pause_targets']}:raise ValueError('暂停类别必须来自本轮pause_targets，不接受未知或内部Skill')
            ident=uuid.uuid4().hex
            c.execute('INSERT INTO service_workflow_constraints VALUES(?,?,?,?,?,?,?)',(ident,state['run_id'],state['uid'],s.encoded(scope),start,end,s.encoded(evidence)))
            saved_constraint={'id':ident,'scope':scope,'starts_at':start,'ends_at':end,'applies_to':'existing_and_future_tasks'}
        coverage=set(); result=[]
        for decision in decisions:
            choice=decision.get('decision');reason=decision.get('reason')
            if choice not in ('create','keep','reschedule','cancel','skip','unknown','no_contact','blocked') or not isinstance(reason,str) or not reason.strip():
                raise ValueError('每项需要合法判断与简短依据')
            name=decision.get('skill')
            if who['type']=='daily' and name not in {entry['skill'] for entry in data['catalog']}:
                raise ValueError('只判断本次workflow_context.catalog中的Skill；不要把全量Skill目录或内部规划任务放入decisions')
            coverage.add(name)
            tid=None
            if choice=='create':
                if who['type']!='daily':raise ValueError('只有规划入口可创建事项')
                if not all(isinstance(decision.get(k),str) and decision[k].strip() for k in ('opportunity_id','run_at')):
                    raise ValueError('create必须传opportunity_id和run_at：沿用本次workflow_context.catalog中该Skill机会的id与合法时点；这是参数缺失，不是业务事实未知，请补齐后重新提交')
                contract=next((x for x in data['catalog'] if x['skill']==name and any(o['id']==decision.get('opportunity_id') for o in x['opportunities'])),None)
                if not contract:raise ValueError('不在已授权服务机会内')
                item=next(o for o in contract['opportunities'] if o['id']==decision['opportunity_id'])
                if item['status']!='eligible':raise ValueError('事实不支持创建')
                due=stamp(decision['run_at'])
                # S01 shares the daily planning wake. Preserve its fixed due
                # time even if planning completes later; never extend expiry,
                # bypass dedupe, or allow an arbitrary backdated task.
                fixed_s01 = name=='remind-reading' and contract['timing']['mode']=='fixed' and due==contract['timing']['at']
                earliest = item['time_window']['earliest'] if fixed_s01 else max(state['as_of'],item['time_window']['earliest'])
                if state['as_of']>item['time_window']['latest'] or not earliest<=due<=item['time_window']['latest']:raise ValueError('时间超出允许窗口')
                if contract['timing']['mode']=='fixed' and due!=contract['timing']['at']:raise ValueError('固定时点不能自行改写')
                requested_due=due
                spacing=item.get('contact_spacing',{})
                due=max(due,spacing.get('not_before') or due)
                if due>item['time_window']['latest']:raise ValueError(spacing.get('reason','联系限制')+'；超出任务有效期')
                if due!=requested_due:reason+='；宿主调整：'+spacing['reason']
                if planning_paused(c,state,name,due):raise ValueError('该安排处于家庭明确暂停范围')
                tid=add(c,state,item,contract,due,reason,{'owner':who,'request_id':request_id,'opportunity_id':item['id']},item['id'])
            elif choice not in ('skip','unknown'):
                tid=amend(c,state,decision,who)
            elif who['type']!='daily':raise ValueError('局部修订需指向实际任务')
            persisted=c.execute('SELECT due_at,reason FROM service_tasks WHERE id=?',(tid,)).fetchone() if tid else None
            result.append({'decision':choice,'task_id':tid,'skill':name,**({'run_at':persisted[0],'requested_run_at':decision.get('run_at'),'reason':persisted[1]} if persisted else {})})
        if who['type']=='daily':
            required={x['skill'] for x in data['catalog']}
            if not required<=coverage:raise ValueError('遗漏已授权检查项；无适用服务也应逐项skip或unknown')
            plan={'schema_version':4,'source':'model','run_id':state['run_id'],'business_date':state['as_of'][:10],
                'planned_at':s.now(),'decisions':[{**d,**{k:r[k] for k in ('run_at','requested_run_at','reason') if k in r}} for d,r in zip(decisions,result)],'items':[r for r in result if r['task_id']],'fixed_checks':[{'time':'06:30','title':'内部规划'}]}
            c.execute('INSERT OR REPLACE INTO daily_plans VALUES(?,?,?,?)',(state['as_of'][:10],s.now(),s.encoded(plan),len(plan['items'])))
            c.execute('INSERT INTO service_plan_versions VALUES(?,?,?,?,?)',(uuid.uuid4().hex,state['run_id'],state['as_of'][:10],s.now(),s.encoded(plan)))
        receipt={'status':'committed','request_id':request_id,'decisions':result}
        if saved_constraint is not None:receipt['contact_constraint']=saved_constraint
        c.execute('INSERT INTO service_workflow_commits VALUES(?,?,?,?,?)',(state['run_id'],request_id,body,s.encoded(receipt),s.now()))
    return receipt

def summary():
    if not enabled():return {}
    initialize()
    with closing(sqlite3.connect(s.DB)) as c:
        state=bound_state(c)
        relevant=[r for r in task_rows(c,state) if r['day']==state['as_of'][:10] or (r['day']>state['as_of'][:10] and r['status'] not in FINAL)]
        return {'mode':MODE,'tasks':[{k:r[k] for k in ('id','case_id','skill','goal','due_at','expires_at','status','revision','needs_revalidation','execution_state')} for r in relevant[:40]],'more_tasks':len(relevant)>40,
            'message_cursor':facts(c,state)['message_cursor'],
            'workflow_guidance':__import__('tool_surface').current_instructions('这是每轮最新任务状态。修改需workflow_context/commit实际回执；固定时点不能随意改。暂停可独立保存，作用于原话明确的服务类别及有效期内现有和未来事项；以contact_constraint回执为准。完整履行已有事项须先加载manage-service-arrangements，再workflow_read(view=context,purpose=fulfill,task_id=实际事项ID)取得本轮专用入口；普通回复不自动完成任务。')}

def prepare_execution(task_id):
    initialize()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');state=bound_state(c)
        task=next(r for r in task_rows(c,state) if r['id']==task_id)
        if task['needs_revalidation']:raise ValueError('Skill变更后的计划尚未重新核验')
        token=uuid.uuid4().hex; value=facts(c,state,[task['skill']])
        c.execute('UPDATE service_task_versions SET execution_token=?,execution_facts=? WHERE task_id=?',(token,s.encoded(value),task_id))
        return {'revision':task['revision'],'execution_token':token,'message_cursor':value['message_cursor']}

def execution_authority(execution_token):
    import agent_plan
    task=agent_plan.active()
    if not task or not isinstance(execution_token,str):raise ValueError('需要本次任务执行令牌')
    known=s.read('SELECT execution_token FROM service_task_versions WHERE task_id=?',(task['id'],))
    if not known or known[0]['execution_token']!=execution_token:raise ValueError('旧执行令牌已失效，请读取本次任务资料')
    with closing(sqlite3.connect(s.DB)) as c:validate_execution(c,task['id'])
    return task

def recheck():
    import agent_plan
    task=agent_plan.active()
    if __import__('host_tool_steps').enabled() and task and __import__('completion_planner').internal(task):return __import__('completion_planner').prepare(task['id'])
    if not task or task['kind'] in INTERNAL:raise ValueError('仅当前行动任务可重新装配')
    return agent_plan.prompt(task['id'])

def validate_execution(c,task_id):
    state=bound_state(c)
    row=c.execute('SELECT t.status,t.skill,t.due_at,t.expires_at,v.execution_facts,v.needs_revalidation FROM service_tasks t JOIN service_task_versions v ON v.task_id=t.id WHERE t.id=? AND t.run_id=? AND v.uid=?',(task_id,state['run_id'],state['uid'])).fetchone()
    if not row or row[0] not in ('running','awaiting_delivery') or row[5] or not row[4]:raise ValueError('任务执行授权已失效')
    if not row[2]<=state['as_of']<=row[3]:raise ValueError('任务不在有效执行时间')
    if constraint_blocks(c,state,row[1],task_id):raise ValueError('任务处于家庭明确暂停范围')
    if digest(facts(c,state,[row[1]])) != digest(json.loads(row[4])):raise ValueError('新消息、学情、联系约束或Skill使旧执行失效')
    if c.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched')").fetchone():raise ValueError('先处理当前家长消息')

def bind_delivery(c,key,task_id=None,passive=False):
    if not enabled():return
    state=bound_state(c)
    if task_id is None:
        row=c.execute("SELECT value FROM metadata WHERE key='active_service_task'").fetchone()
        task_id=json.loads(row[0]) if row else None
    if not task_id:raise ValueError('主动发送必须来自已规划事项')
    if not passive:validate_execution(c,task_id)
    else:
        import service_content
        task=next(r for r in task_rows(c,state) if r['id']==task_id)
        if task['needs_revalidation'] or task['skill_version']!=service_content.bundle_digest(task['skill']) or state['as_of']>task['expires_at']:
            raise ValueError('原任务规则或有效期已变化，先重新核验')
        if constraint_blocks(c,state,task['skill'],task_id):raise ValueError('原任务仍在暂停范围')
        c.execute('UPDATE service_task_versions SET execution_facts=? WHERE task_id=?',(s.encoded(facts(c,state,[task['skill']])),task_id))
    if c.execute('SELECT skill FROM service_tasks WHERE id=?',(task_id,)).fetchone()[0]=='weekly-learning-review':
        gate=__import__('weekly_materials').s10_delivery_gate(c)
        if gate:raise ValueError(gate['reason'])
    if c.execute('SELECT 1 FROM service_links l JOIN deliveries d ON d.action_key=l.action_key WHERE l.task_id=? AND l.action_key<>? AND d.status<>"superseded"',(task_id,key)).fetchone():raise ValueError('已有原交付，不创建第二次发送')
    c.execute('INSERT OR IGNORE INTO service_links VALUES(?,?)',(task_id,key))
    c.execute("UPDATE service_tasks SET status=?,result=? WHERE id=?",('awaiting_delivery' if passive else 'running',s.encoded({'delivery_key':key,'via_dialogue':passive}),task_id))

def authorize_part(c,key,operation_id):
    if not enabled():return
    state=bound_state(c)
    # Sender key is run_id:action_key[:text/image:n]. Resolve without splitting IDs.
    linked=rows(c,'SELECT l.task_id,l.action_key,t.result,v.revision FROM service_links l JOIN service_tasks t ON t.id=l.task_id JOIN service_task_versions v ON v.task_id=t.id WHERE t.run_id=?',(state['run_id'],))
    match=next((r for r in linked if key==state['run_id']+':'+r['action_key'] or key.startswith(state['run_id']+':'+r['action_key']+':text:') or key.startswith(state['run_id']+':'+r['action_key']+':image:') or key.startswith(state['run_id']+':'+r['action_key']+':emoji:')),None)
    if not match:
        if key.startswith(state['run_id']+':reply:'):return
        raise ValueError('缺少事项发送授权')
    result=json.loads(match['result'] or '{}')
    if not result.get('via_dialogue'):validate_execution(c,match['task_id'])
    else:
        row=c.execute('SELECT t.status,t.skill,v.execution_facts,v.needs_revalidation FROM service_tasks t JOIN service_task_versions v ON v.task_id=t.id WHERE t.id=?',(match['task_id'],)).fetchone()
        if row[0]!='awaiting_delivery' or row[3] or digest(facts(c,state,[row[1]]))!=digest(json.loads(row[2])):
            raise ValueError('被动履约发送授权已失效，核对原交付，不能盲目重发')
    c.execute('INSERT INTO service_send_authorizations VALUES(?,?,?,?,?,?,?)',(operation_id,match['action_key'],match['task_id'],match['revision'],s.encoded(facts(c,state)),s.now(),'provider submission authorized; accepted requests cannot be recalled'))

def reconcile(c):
    if not enabled():return
    state=bound_state(c)
    for task_id, in c.execute('SELECT v.task_id FROM service_task_versions v JOIN service_tasks t ON t.id=v.task_id WHERE t.run_id=? AND v.uid=?',(state['run_id'],state['uid'])).fetchall():
        delivery_rows=c.execute('SELECT d.status,d.source FROM service_links l JOIN deliveries d ON d.action_key=l.action_key WHERE l.task_id=? AND d.status<>"superseded"',(task_id,)).fetchall()
        task=c.execute('SELECT status,result FROM service_tasks WHERE id=?',(task_id,)).fetchone()
        result=json.loads(task[1] or '{}') if task else {}
        if task and (task[0] in ('cancelled','expired','no_contact') or (task[0]=='blocked' and result.get('code')!='delivery_unconfirmed' and (result.get('outcome')=='blocked' or result.get('code') in ('human_confirmation_required','new_inbound_replan')))):continue
        statuses=[r[0] for r in delivery_rows]
        if not statuses:continue
        status=__import__('execution_contract').delivery_task_status([{'status':status,'source':source} for status,source in delivery_rows])
        c.execute('UPDATE service_tasks SET status=?,finished_at=? WHERE id=? AND status<>?',(status,s.now() if status=='done' else None,task_id,status))

def fulfill(request_id,context_token,task_id,revision,body,evidence,highlight_ids=None):
    """One passive logical delivery atomically owns the original task before sending."""
    import business
    task_id=business.original(task_id)
    if highlight_ids:highlight_ids=[business.original(x) for x in highlight_ids]
    initialize()
    import wechat_test
    from message_text import parts
    __import__('execution_contract').validate_body(body)
    parts(body)
    if not isinstance(request_id,str) or not 8<=len(request_id)<=160:raise ValueError('需要稳定请求ID')
    request_parts=[context_token,task_id,revision,body,evidence,highlight_ids]
    selected=__import__('emoji_assets').SELECTED.get()
    if selected:request_parts.append({'emoji_parts':selected})
    request_body=s.encoded(request_parts)
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');state=bound_state(c)
        prior=c.execute('SELECT body,result FROM service_workflow_commits WHERE run_id=? AND request_id=?',(state['run_id'],request_id)).fetchone()
        if prior:
            if prior[0]!=request_body:raise ValueError('请求ID内容冲突')
            return __import__('execution_contract').fulfillment_receipt(c,{**json.loads(prior[1]),'duplicate':True})
        state,who,data=check_context(c,context_token)
        if who['type']!='dialogue' or not evidence:raise ValueError('仅当前真实对话可履行任务，需说明完整覆盖依据')
        task=next((r for r in data['tasks'] if r['id']==task_id),None)
        if not task or task['revision']!=revision or task['status'] not in ('planned','deferred'):raise ValueError('任务版本已变化或已有交付')
        if task['kind']=='festival':raise ValueError('节日图文须完成既有生成和检查链，不能用纯文字回复替代')
        images=[]
        if task['kind']=='highlight':
            import proactive
            item=next((x for x in proactive.evaluate(c,state['as_of'][:10]) if x.get('id')==task['candidate_id']),None)
            if not item or item.get('delivery_owner')!='host_highlights' or item['status']!='eligible':raise ValueError('原高光不可交付')
            h=item['evidence']
            if highlight_ids!=[h['id']] or body!=h['share_txt']:raise ValueError('高光履行必须保留绑定原文原图')
            images=[h['img_url']]
        elif highlight_ids:raise ValueError('不能给文字任务附加无关高光')
        import agent_plan
        agent_plan.require_action_skills(task['skill'],'reply-style')
        key='reply:'+who['id']
        if c.execute('SELECT 1 FROM deliveries WHERE action_key=?',(key,)).fetchone():raise ValueError('回复已提交，不能事后绑定')
        c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,body,'queued',s.now(),'test_wechat_pending'))
        wechat_test.queue_text(c,key,body)
        wechat_test.queue_images(c,key,images)
        bind_delivery(c,key,task_id,True)
        # Reserve the same business dedupe identity as an active delivery.
        if task['candidate_id']:
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(task['dedupe_key'],task['candidate_id'],key,state['as_of']))
        import service_ledger
        service_ledger.timeline(c,task['case_id'],'dialogue_fulfillment',request_id,{'event_ids':who['event_ids'],'source_message_ids':who['source_message_ids'],'evidence':evidence,'action_key':key})
        receipt={'status':'bound','action_key':key,'task_id':task_id}
        c.execute('INSERT INTO service_workflow_commits VALUES(?,?,?,?,?)',(state['run_id'],request_id,request_body,s.encoded(receipt),s.now()))
    wechat_test.flush()
    with closing(sqlite3.connect(s.DB)) as c:
        return __import__('execution_contract').fulfillment_receipt(c,receipt)

def sync():
    initialize()
    import agent_plan,completion_planner,completion_events
    state=tm.state();day=state['as_of'][:10]
    with closing(sqlite3.connect(s.DB)) as c,c:
        reconcile(c)
        c.execute("UPDATE service_tasks SET status='expired',finished_at=? WHERE run_id=? AND status IN ('planned','deferred') AND expires_at<?",(s.now(),state['run_id'],state['as_of']))
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');__import__('s10_service').ensure_fixed(c,state)
        __import__('service_observed_wakes').ensure(c,state)
    agent_plan.insert_task('plan','当日服务规划','plan-today',day+'T06:30:00+08:00','仅内部唤醒；业务任务必须由模型提交',ident='service-'+state['run_id']+'-'+day+'-plan')
    completion_events.ingest_fixture();completion_planner.sync()
    return {'status':'ready','mode':MODE}

def prepare_plan(task_id):
    import agent_plan,observability
    skill=observability.invoke('service_skill',{'name':'plan-service-day'},lambda:agent_plan.load_skill('plan-service-day'),origin='host')
    data=observability.invoke('workflow_context',{},context,origin='host')
    return {'prompt':__import__('tool_surface').current_instructions('内部规划，不能发送。按以下规划契约和实时事实判断；catalog不包含业务Skill正文，需要时用service_skill读取，用workflow_commit原子提交。每个catalog Skill至少一项create/keep/reschedule/cancel/skip/unknown。没有适用服务仍保存全skip/unknown的空计划。不能预测未来完课。固定时点不能改成窗口；过时不补发。提交成功后service_task_finish。任务ID：')+task_id+'\n'+skill['body']+'\n'+s.encoded(data),'context_version':MODE}

def published(skill):
    """Called by the existing publication path, before it reports completion."""
    if not enabled():return {'affected':0}
    initialize()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');state=bound_state(c)
        count=c.execute("UPDATE service_task_versions SET needs_revalidation=1,revision=revision+1,updated_at=? WHERE task_id IN (SELECT id FROM service_tasks WHERE run_id=? AND skill=? AND status IN ('planned','deferred','running'))",(s.now(),state['run_id'],skill)).rowcount
    if count:
        import agent_plan
        agent_plan.request('plan')
    return {'affected':count}

def interrupt_for_dialogue():
    """Native steer gives the actual message batch priority; invalidate old execution."""
    if not enabled():return {'status':'disabled'}
    import time
    initialize()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');state=bound_state(c)
        if not c.execute("SELECT 1 FROM events WHERE kind='passive' AND status='dispatched'").fetchone():return {'status':'no_dialogue'}
        running=rows(c,"SELECT id FROM service_tasks WHERE run_id=? AND status='running'",(state['run_id'],))
        for row in running:
            linked=c.execute('SELECT 1 FROM service_links WHERE task_id=?',(row['id'],)).fetchone()
            c.execute('UPDATE service_tasks SET status=?,not_before_wall=? WHERE id=?',('awaiting_delivery' if linked else 'deferred',time.time()+121,row['id']))
            c.execute('UPDATE service_task_versions SET execution_token=NULL WHERE task_id=?',(row['id'],))
        c.execute("DELETE FROM metadata WHERE key IN ('active_service_task','native_service_content_task')")
    return {'status':'dialogue_priority','interrupted':[r['id'] for r in running]}


def preflight_contact(c,task,state):
    if task['kind'] in INTERNAL or not task['candidate_id']:return None
    if c.execute('SELECT 1 FROM service_links WHERE task_id=?',(task['id'],)).fetchone():return None
    candidate=c.execute('SELECT body FROM proactive_candidates WHERE id=?',(task['candidate_id'],)).fetchone()
    if not candidate:return None
    import proactive
    value=proactive.contact_spacing(c,[json.loads(candidate[0])],dt.datetime.fromisoformat(state['as_of']))
    retry=value['not_before']
    if not retry or retry<=state['as_of']:return None
    expired=retry>task['expires_at']
    result={'code':'contact_gap_preflight','reason':value['reason']+('；已超过有效期，不跨天补发' if expired else '；宿主在调用模型前延期，到点重新读取事实'),
            'retry_at':retry,'original_due_at':json.loads(task['result'] or '{}').get('original_due_at',task['due_at']),
            'causes':[value],'deferral_id':proactive.contact_deferral_id([task['candidate_id']],retry)}
    status='expired' if expired else 'deferred'
    c.execute('UPDATE service_tasks SET status=?,due_at=?,result=?,finished_at=?,not_before_wall=0 WHERE id=?',
              (status,task['due_at'] if expired else retry,s.encoded(result),s.now() if expired else None,task['id']))
    c.execute('UPDATE service_task_versions SET execution_token=NULL WHERE task_id=?',(task['id'],))
    now=s.now()
    c.execute('INSERT INTO tool_audit(id,run_id,business_time,started_at,finished_at,origin,name,input,output,status,duration_ms,task_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              (uuid.uuid4().hex,state['run_id'],state['as_of'],now,now,'host','contact_preflight',s.encoded({'task_id':task['id']}),s.encoded({'status':status,**result}),'ok',0,task['id']))
    return result


def defer_pending_contact(c,task_id):
    """Honor the contact gate's durable retry on the same unsent task."""
    state=bound_state(c)
    task=rows(c,"SELECT * FROM service_tasks WHERE id=? AND run_id=? AND status='running'",(task_id,state['run_id']))
    if not task:return None
    task=task[0]
    if task['kind'] in INTERNAL or not task['candidate_id']:return None
    if c.execute('SELECT 1 FROM service_links l JOIN deliveries d ON d.action_key=l.action_key WHERE l.task_id=? AND d.status<>"superseded"',(task_id,)).fetchone():return None
    pending=next((row for row in rows(c,"SELECT * FROM proactive_pending WHERE status='pending'")
                  if json.loads(row['candidate_ids'])==[task['candidate_id']]
                  and state['as_of']<row['retry_at']<=min(row['expires_at'],task['expires_at'])),None)
    if not pending:return None
    result={'code':'contact_gate_deferred','reason':'沿用联系门控的重试时点；到点刷新事实，不沿用旧正文。',
            'retry_at':pending['retry_at'],'original_due_at':json.loads(task['result'] or '{}').get('original_due_at',task['due_at'])}
    result['deferral_id']=__import__('proactive').contact_deferral_id([task['candidate_id']],pending['retry_at'])
    for call in c.execute('SELECT output FROM tool_audit WHERE task_id=? AND started_at>=? ORDER BY rowid DESC',(task_id,task['started_at'])):
        output=json.loads(call[0] or '{}')
        if isinstance(output,dict) and output.get('retry_at')==pending['retry_at'] and output.get('causes'):
            result.update({k:output[k] for k in ('reason','causes','deferral_id') if k in output});break
    c.execute("UPDATE service_tasks SET status='deferred',due_at=?,not_before_wall=0,finished_at=NULL,attempts=MAX(0,attempts-1),result=? WHERE id=?",
              (pending['retry_at'],s.encoded(result),task_id))
    c.execute('UPDATE service_task_versions SET execution_token=NULL WHERE task_id=?',(task_id,))
    c.execute("DELETE FROM metadata WHERE key IN ('active_service_task','native_service_content_task') AND value=?",(s.encoded(task_id),))
    return {'status':'deferred','task_id':task_id,**result}


def recover():
    """Resume the same durable task after host restart; never replay a delivery."""
    import time
    initialize()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');state=bound_state(c);reconcile(c)
        for task in rows(c,"SELECT * FROM service_tasks WHERE run_id=? AND status='running'",(state['run_id'],)):
            if defer_pending_contact(c,task['id']):continue
            if c.execute('SELECT 1 FROM service_links WHERE task_id=?',(task['id'],)).fetchone():continue
            committed=c.execute('SELECT 1 FROM completion_plans WHERE task_id=?',(task['id'],)).fetchone() if task['kind']=='completion_plan' else None
            if task['kind']=='plan':
                committed=c.execute("SELECT 1 FROM service_workflow_commits m JOIN service_workflow_contexts x ON x.id=json_extract(m.body,'$[0]') WHERE json_extract(x.owner,'$.id')=?",(task['id'],)).fetchone()
            status='done' if committed else 'deferred' if task['attempts']<3 else 'blocked'
            c.execute('UPDATE service_tasks SET status=?,not_before_wall=?,result=? WHERE id=?',(status,time.time()+15,s.encoded({'code':'host_restart_recovery','reason':'恢复原任务；已有提交沿用，无交付才重新复核。'}),task['id']))
            c.execute('UPDATE service_task_versions SET execution_token=NULL WHERE task_id=?',(task['id'],))
        c.execute("DELETE FROM metadata WHERE key IN ('active_service_task','native_service_content_task')")
    return {'status':'recovered','mode':MODE}

def settle_internal(task_id):
    """Missing model finish is technical recovery on the original planning key."""
    import time
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        task=rows(c,"SELECT * FROM service_tasks WHERE id=? AND status='running'",(task_id,))
        if not task:return {'status':'already_finished'}
        task=task[0]
        if task['kind'] not in ('plan','completion_plan'):raise ValueError('仅内部规划可恢复提交')
        committed=c.execute('SELECT 1 FROM completion_plans WHERE task_id=?',(task_id,)).fetchone()
        if task['kind']=='plan':committed=c.execute("SELECT 1 FROM service_workflow_commits m JOIN service_workflow_contexts x ON x.id=json_extract(m.body,'$[0]') WHERE json_extract(x.owner,'$.id')=?",(task_id,)).fetchone()
        status='done' if committed else 'deferred' if task['attempts']<3 else 'blocked'
        c.execute('UPDATE service_tasks SET status=?,not_before_wall=?,result=? WHERE id=?',(status,time.time()+15,s.encoded({'code':'planning_finish_recovery','reason':'沿用原规划键及既有3次技术尝试上限；不生成替代业务计划。'}),task_id))
        c.execute("DELETE FROM metadata WHERE key='active_service_task' AND value=?",(s.encoded(task_id),))
    return {'status':status,'task_id':task_id}

def highlight():
    """Reuse the exact interface caption and image for one model-planned asset."""
    import agent_plan,proactive,wechat_test
    task=agent_plan.active()
    if not enabled() or not task or task['kind']!='highlight':raise ValueError('仅已规划高光任务可交付')
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');validate_execution(c,task['id'])
        prior=c.execute('SELECT d.action_key,d.status FROM service_links l JOIN deliveries d ON d.action_key=l.action_key WHERE l.task_id=? AND d.status<>"superseded"',(task['id'],)).fetchone()
        if prior:return {'action_key':prior[0],'status':prior[1],'duplicate':True}
        item=next((x for x in proactive.evaluate(c,task['day']) if x.get('id')==task['candidate_id']),None)
        if not item or item['status']!='eligible':return {'status':'no_contact','reason':'高光已经交付、过期或不再适用'}
        gate=proactive.contact_gate(c,[item],dt.datetime.fromisoformat(tm.state()['as_of']))
        if gate:return gate
        h=item['evidence'];key='highlight:'+hashlib.sha256(item['dedupe_key'].encode()).hexdigest()[:32]
        key=__import__('message_hooks').draft_key(c,key)
        if not h.get('share_txt') or not h.get('img_url'):raise ValueError('缺少高光原文或原图')
        bind_delivery(c,key,task['id'])
        c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,h['share_txt'],'queued',tm.state()['as_of'],'test_wechat_pending'))
        __import__('message_hooks').register_external(c,key,'highlight_original')
        __import__('service_hours').register_draft(c,key)
        wechat_test.queue_images(c,key,[h['img_url']])
        c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(item['dedupe_key'],item['id'],key,tm.state()['as_of']))
        c.execute("UPDATE proactive_pending SET status='done' WHERE id=?",('resume-'+hashlib.sha256(item['id'].encode()).hexdigest()[:20],))
    wechat_test.flush()
    return {'action_key':key,'status':s.read('SELECT status FROM deliveries WHERE action_key=?',(key,))[0]['status']}

def wall_clock():
    state=tm.state()
    if enabled(state) and not state.get('frozen',True):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM service_tasks WHERE run_id=? AND status='running'",(state['run_id'],)).fetchone():return state
            state['as_of']=dt.datetime.now(tm.TZ).isoformat(timespec='seconds')
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
    return state

if __name__=='__main__':
    import sys
    command=sys.argv[1]
    if command=='wall-clock':result=wall_clock()
    elif command=='dialogue':result=interrupt_for_dialogue()
    elif command=='wake':
        wall_clock();result=sync() if enabled() else {'status':'disabled'}
    elif command=='scheduled':
        result=s.read('SELECT id,kind,due_at,status FROM service_tasks WHERE run_id=?',(tm.state()['run_id'],))
    else:raise ValueError('Unknown command')
    print(s.encoded(result))

