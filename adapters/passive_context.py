"""Experiment-only passive working materials; immutable audit and replaceable cache heads.

Learning is valid for one hour AND the same feed/signal revision. Knowledge uses
the registered publication revision. Neither grants send authority or fresh-read
credit to active tasks. The fixture adapter exposes local change markers here;
a real learning provider must supply its own revision/event watermark.
"""
from contextlib import closing
import datetime as dt
import hashlib
import json
import re
import sqlite3
import time
import sql_tools as s
import test_mode as tm

LEARNING = {'learning_query', 'learning_context'}
KNOWLEDGE = {'course_read', 'course_knowledge', 'knowledge_read'}
TTL = 3600
SIGNAL = re.compile(r'(?:刚|已经|已|今天|现在).{0,12}(?:完成|读完|学完|提交|打卡|答完)|(?:学情|记录|进度).{0,8}(?:不对|错误|变了|更新)|(?:请|麻烦|帮我|重新|再).{0,4}(?:查|看).{0,8}(?:最新|现在|学情|进度)|刷新.{0,6}(?:学情|进度|记录)|(?:还没|没有|没).{0,5}(?:学完|读完|完成|提交)')

def digest(value):
    return hashlib.sha256(s.encoded(value).encode()).hexdigest()

def database():
    c = sqlite3.connect(s.DB)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS passive_materials(id TEXT PRIMARY KEY, scope TEXT, tool TEXT, args TEXT, version TEXT, body TEXT, queried_at TEXT, wall_at REAL)')
    c.execute('CREATE TABLE IF NOT EXISTS passive_material_heads(scope TEXT, tool TEXT, args TEXT, id TEXT, PRIMARY KEY(scope,tool,args))')
    c.execute('CREATE TABLE IF NOT EXISTS passive_cache_audit(id INTEGER PRIMARY KEY, scope TEXT, at TEXT, tool TEXT, decision TEXT, material_id TEXT)')
    return c

def scope_data():
    state = tm.state()
    if not state: raise ValueError('bound_family_required')
    data = tm.fixture_data(state, include_events=False)
    if data['uid'] != state['uid']: raise ValueError('passive_family_binding_mismatch')
    import course_service
    identity = [state['run_id'], state['uid'], data['enrollments'], course_service.binding(state['uid'])]
    if state.get('conversation_id'): identity.append(state['conversation_id'])
    scope = digest(identity)
    return scope, state, data

def enabled():
    if not (__import__('experiment_runtime').verify() or __import__('manual_workbench').verify() or __import__('family_lab_bridge').verify()): return False
    if __import__('agent_plan').active(): return False
    return bool(s.read("SELECT 1 FROM events WHERE kind='passive' AND status='dispatched'"))

def learning_version(state, data):
    at = state['as_of']
    # Observe local ingress watermarks, not another learning-query/provider call.
    visible = []
    for collection, stamps in [('learning_daily', ('unlocked_at','attended_at','completed_at','checked_in_at')),
                               ('learning_events', ('occurred_at',)), ('quiz_attempts', ('submitted_at',)),
                               ('expression_assignments', ('submitted_at','selection_recorded_at')),
                               ('weekly_rankings', ('published_at',)), ('books', ('completed_at','shelf_lit_at'))]:
        for row in data.get(collection, []):
            markers = [(key, row[key]) for key in stamps if row.get(key) and row[key] <= at]
            if markers: visible.append([collection, row.get('id'), markers, digest(row)])
    import completion_events
    changes = [r for r in completion_events.saved(state) if r['completed_at'] <= at]
    signals = [[r['source_id'], r['body']] for r in s.read("SELECT source_id,body FROM messages WHERE speaker='user' AND visible=1 ORDER BY rowid") if SIGNAL.search(r['body'])]
    # Audio is derived evidence; a new interpretation can signal change but is not completion proof.
    with closing(sqlite3.connect(s.DB)) as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='voice_inputs'").fetchone():
            signals += [[i, v] for i,v in c.execute('SELECT id,result FROM voice_inputs WHERE run_id=? AND uid=? AND result IS NOT NULL', (state['run_id'],state['uid'])) if SIGNAL.search(v)]
    cfg,lab=__import__('family_lab_bridge').connection()
    lab_revision=lab.read('SELECT projected_seq FROM families WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id'])) if cfg else []
    return digest([at[:10], visible, changes, signals,lab_revision])

def version(tool, args, state, data):
    if tool in LEARNING: return learning_version(state, data)
    if tool == 'knowledge_read':
        import knowledge_search as knowledge
        item=next((x for x in knowledge.catalog()['documents'] if x['id']==args['document_id']),None)
        return digest([item,__import__('service_catalog').KNOWLEDGE_SKILL_ROUTES]) if item else None
    if tool == 'course_knowledge':
        import course_service
        item = course_service.entry(args['class_type'], args['lesson_no'])
        return digest(item) if item else None
    if tool == 'course_read':
        import course_navigation as nav
        item = nav.resolve(args['uri'])
        return digest([nav.catalog()['revision'], item, args.get('detail','overview')])
    return None

def normalized(tool, args, state):
    args = dict(args)
    if tool in LEARNING: args['day'] = args.get('day') or state['as_of'][:10]
    if tool == 'course_read': args.setdefault('detail','overview')
    return args

def valid(row, state, data):
    args = json.loads(row['args'])
    if row['version'] != version(row['tool'], args, state, data): return False
    if row['tool'] in LEARNING:
        age = (dt.datetime.fromisoformat(state['as_of']) - dt.datetime.fromisoformat(row['queried_at'])).total_seconds()
        if not 0 <= age < TTL or not 0 <= time.time()-row['wall_at'] < TTL: return False
    return True

def metadata(row, hit):
    return {'material_ref': row['id'], 'source_tool': row['tool'], 'source_args':json.loads(row['args']),
            'queried_at': row['queried_at'], 'version': row['version'], 'cache_hit': hit,
            'expires_at': (dt.datetime.fromisoformat(row['queried_at'])+dt.timedelta(seconds=TTL)).isoformat() if row['tool'] in LEARNING else None,
            'authority':False, 'fresh_fact_receipt':False,
            'guidance':('仅用于被动答疑；主动事实与发送许可须实时核验。evidence_read(ref={kind:material,id:material_ref})分页读取。' if __import__('tool_surface').enabled('knowledge') else '仅用于被动答疑；主动事实与发送许可须实时核验。material_read可分页读取完整资料。')}

def render(row, hit=True):
    value = json.loads(row['body'])
    if row['tool'] in LEARNING:
        day = json.loads(row['args'])['day']
        # Keep current lesson details plus aggregate progress. Historical rows remain paged.
        value = {k:value[k] for k in ('uid','as_of','profile','enrollments','course_policy','requested_day','summary_as_of','weekly_learning','course_knowledge') if k in value}
        original = json.loads(row['body'])
        value['lessons_as_of'] = [x for x in original.get('lessons_as_of',[]) if x.get('scheduled_date') == day]
        value['omitted_sections'] = [k for k in original if k not in value]
        value['history_available_via_material_read'] = True
    if hit and 'live_read' in value: value['live_read'] = False
    result = {**value, 'passive_cache': metadata(row, hit), 'material_ref':row['id']}
    if len(s.encoded(result)) > 14000:
        return {'status':'available_paged', 'passive_cache':metadata(row, hit), 'material_ref':row['id'], 'requires_material_read':True}
    return result

def query(tool, args, operation):
    if tool not in LEARNING|KNOWLEDGE or not enabled(): return operation()
    scope, state, data = scope_data()
    args = normalized(tool, args, state); encoded = s.encoded(args)
    ver = version(tool, args, state, data)
    with closing(database()) as c, c:
        row = c.execute('SELECT m.* FROM passive_material_heads h JOIN passive_materials m ON m.id=h.id WHERE h.scope=? AND h.tool=? AND h.args=?', (scope,tool,encoded)).fetchone()
        if row and valid(row,state,data):
            c.execute('INSERT INTO passive_cache_audit(scope,at,tool,decision,material_id) VALUES(?,?,?,?,?)',(scope,state['as_of'],tool,'hit',row['id']))
            return render(row)
    # learning_context aggregates volatile contact/receipt state; in passive use
    # learning_query facts instead. No contact state is retained under a one-hour TTL.
    value = tm.snapshot(args['day']) if tool == 'learning_context' else operation()
    if not isinstance(value,dict) or value.get('error') or value.get('status','ok') != 'ok': return value
    current_scope, current_state, current_data = scope_data()
    if current_scope != scope or current_state['as_of'] != state['as_of'] or version(tool,args,current_state,current_data) != ver:
        raise ValueError('passive_material_changed_during_read_retry')
    ident = 'passive-'+digest([scope,tool,encoded,ver,value,state['as_of'],time.time_ns()])
    with closing(database()) as c, c:
        c.execute('INSERT INTO passive_materials VALUES(?,?,?,?,?,?,?,?)',(ident,scope,tool,encoded,ver,s.encoded(value),state['as_of'],time.time()))
        c.execute('INSERT OR REPLACE INTO passive_material_heads VALUES(?,?,?,?)',(scope,tool,encoded,ident))
        c.execute('INSERT INTO passive_cache_audit(scope,at,tool,decision,material_id) VALUES(?,?,?,?,?)',(scope,state['as_of'],tool,'refresh' if row else 'miss',ident))
        return render(c.execute('SELECT * FROM passive_materials WHERE id=?',(ident,)).fetchone(),False)

def read(material_id, offset=0):
    if type(offset) is not int or offset<0: raise ValueError('invalid_material_offset')
    scope,state,data = scope_data()
    with closing(database()) as c:
        row = c.execute('SELECT * FROM passive_materials WHERE id=? AND scope=?',(material_id,scope)).fetchone()
    if not row: raise ValueError('material_not_in_current_family_run')
    if not valid(row,state,data):
        return {'status':'stale','material_ref':material_id,'source_tool':row['tool'],'source_args':json.loads(row['args']),'guidance':'资料已失效；需要时调用原查询工具刷新，旧快照不可当作当前事实。'}
    if offset>len(row['body']):raise ValueError('invalid_material_offset')
    end=min(offset+6000,len(row['body']))
    return {**metadata(row,True),'offset':offset,'next_offset':end if end<len(row['body']) else None,'json_fragment':row['body'][offset:end]}

def materials(include_bodies=False):
    scope,state,data=scope_data();result=[];size=0
    with closing(database()) as c:
        rows=c.execute('SELECT m.* FROM passive_material_heads h JOIN passive_materials m ON m.id=h.id WHERE h.scope=? ORDER BY m.rowid DESC LIMIT 12',(scope,)).fetchall()
    for row in rows:
        if not valid(row,state,data): continue
        meta=metadata(row,True)
        body=render(row)
        if include_bodies and size+len(s.encoded(body))<=14000:
            result.append(body);size+=len(s.encoded(body))
        else: result.append(meta)
    return result

def entry(day):
    scope,state,data=scope_data()
    import course_service
    identity={**data['profile'],**course_service.binding(state['uid']),'uid':state['uid']}
    if __import__('preference_memory').enabled():
        history=__import__('dialogue_context').window(day,max_chars=12000,max_rows=16)
    else:
        history={'recent_conversation':list(reversed(s.read('SELECT source_id,speaker,body,created_at FROM messages WHERE visible=1 ORDER BY rowid DESC LIMIT 16')))}
    voice=[];human=[];constraints=[];legacy=[]
    sources={r['source_id'] for r in history['recent_conversation']}
    with closing(sqlite3.connect(s.DB)) as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='voice_inputs'").fetchone():
            for ident,source,result in c.execute('SELECT id,source_id,result FROM voice_inputs WHERE run_id=? AND uid=? AND result IS NOT NULL',(state['run_id'],state['uid'])):
                if source in sources:
                    voice.append({'asset_id':ident,'source_message_id':source,'derived':True,
                                  'result':json.loads(result) if len(result)<=6000 else None,
                                  'guidance':('语音理解结果已由宿主提供；长资料使用host_voice_materials中的material_ref分页读取，原话纠正优先。' if __import__('host_tool_steps').enabled() else '语音理解结果可复用；原话纠正优先。需要完整结果时understand_audio会复用既有分析。')})
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='service_workflow_constraints'").fetchone():
            c.row_factory=sqlite3.Row
            constraints=[dict(r) for r in c.execute('SELECT * FROM service_workflow_constraints WHERE run_id=? AND uid=? AND ends_at>=?',(state['run_id'],state['uid'],state['as_of']))]
            c.row_factory=None
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='service_case_constraints'").fetchone():
            c.row_factory=sqlite3.Row
            legacy=[dict(r) for r in c.execute("SELECT * FROM service_case_constraints WHERE run_id=? AND uid=? AND status IN ('paused','stopped')",(state['run_id'],state['uid']))]
            c.row_factory=None
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='operator_alerts'").fetchone():
            human=[dict(zip(('id','status','category','reason'),r)) for r in c.execute("SELECT a.id,COALESCE(d.status,a.status),a.category,a.reason FROM operator_alerts a LEFT JOIN deliveries d ON d.action_key=a.action_key WHERE a.run_id=? AND a.uid=? AND COALESCE(d.status,a.status) NOT IN ('sent','resolved','dismissed')",(state['run_id'],state['uid']))]
        from constraint_evidence import for_conversation
        contact = for_conversation(c,state,s.read('SELECT * FROM contact_constraints WHERE day=?',(day,)))
        constraints = for_conversation(c,state,constraints)
    return {'business_day':day,'business_time':state['as_of'],'identity':identity,'enrollments':data['enrollments'],
            **history,'contact_constraints':contact,
            'voice_understanding':voice,'human_pending':human,'workflow_constraints':constraints,'legacy_constraints':legacy,
            'working_materials':materials(True),
            'material_policy':('当前学情按需learning_query查询；' if __import__('tool_surface').enabled('context') else '当前学情按需learning_query/learning_context查询；')+'1小时内且无变化信号可复用，过期/发生变化会退出本轮材料。知识按发布版本复用。不要从历史回合快照推断当前状态。旧对话需用conversation_read或conversation_window_read。安排变化用workflow_context实时查询，发送由宿主复核。'}

def checkpoint(event_id, request_id=None):
    state=tm.state();event=s.read('SELECT * FROM events WHERE id=? AND kind=?',(event_id,'passive'))
    if not event:raise ValueError('passive_event_missing')
    event=event[0];payload=json.loads(event['payload'])
    ids=list(dict.fromkeys(payload.get('source_message_ids',[])+[payload.get('source_message_id')]))
    messages=[m for ident in ids if ident for m in s.conversation_read(ident)]
    receipts=s.read('SELECT action_key,body,status,created_at FROM deliveries WHERE action_key=?',('reply:'+event_id,))
    return {'ready':True,'kind':'passive_dialogue_checkpoint','family_uid':state['uid'],'run_id':state['run_id'],
            'event_id':event_id,'event_status':event['status'],'closed_request_id':request_id,
            'parent_messages':messages,'deliveries':receipts,
            'guidance':'保留原话及实际交付状态；临时Skill包、学情和工具结果已退出。材料以新回合working_materials为准，引用失效时重新查询。未交付的同一事件重试先核对回执，unknown不得盲重发。'}
