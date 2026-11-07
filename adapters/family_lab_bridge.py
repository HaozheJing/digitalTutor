"""Private family-host adapter. Control authority is never a public MCP tool."""
import contextlib,datetime as dt,hashlib,json,re,sqlite3,sys,time
from pathlib import Path
import sql_tools as s
from family_lab import Lab,encode


def configuration():
    p=s.R/'config/family-lab.json'
    if not p.exists():return None
    v=json.loads(p.read_text())
    if Path(v['workspace']).resolve()!=s.R.resolve():raise ValueError('family_workspace_mismatch')
    return v


def connection():
    cfg=configuration()
    if not cfg:return None,None
    lab=Lab(cfg['control_db'])
    rows=lab.read('SELECT * FROM families WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id']))
    if len(rows)!=1 or Path(rows[0]['workspace']).resolve()!=s.R.resolve():raise ValueError('family_registry_mismatch')
    return cfg,lab


def verify():
    cfg,lab=connection()
    if not cfg:return None
    import test_mode as tm
    state=tm.state()
    if not state or state['uid']!=cfg['family_id'] or state['run_id']!=cfg['business_run_id']:raise ValueError('family_business_binding_mismatch')
    return {'enabled':True,'family_uid':cfg['family_id'],'delivery_mode':'coordinated_lab','run_id':cfg['run_id']}


def status():
    cfg,lab=connection()
    if not cfg:return None
    r=lab.current()
    f=lab.read('SELECT * FROM families WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id']))[0]
    return {'run':r,'family':f,'dispatch':r['id']==cfg['run_id'] and not(r['paused'] or f['held']) and f['projected_seq']==f['inbox_seq'] and f['applied_clock']==r['epoch']}


def project_human_handled(c,lab,cfg):
    key='human_handled:'+cfg['run_id']+':'+cfg['family_id']
    watermark=lab.read('SELECT value FROM settings WHERE key=?',(key,))
    if not watermark:return
    ids={x['id'] for x in lab.read("SELECT id FROM ingress WHERE run_id=? AND family_id=? AND kind='message' AND seq<=?",(cfg['run_id'],cfg['family_id'],int(watermark[0]['value'])))}
    if not ids:return
    import message_hooks as hooks
    hooks.tables(c)
    c.executemany('INSERT INTO message_hook_inbound(scope,source_id,received_at,handled) VALUES(?,?,?,1) ON CONFLICT(scope,source_id) DO UPDATE SET handled=1',[(hooks.scope(c),sid,time.time()) for sid in ids])
    for event_id,raw in c.execute("SELECT id,payload FROM events WHERE kind='passive' AND status IN ('pending','dispatched','needs_attention')").fetchall():
        p=json.loads(raw);refs=set(p.get('source_message_ids',[]) or [p.get('source_message_id')])
        if refs & ids:
            # A mixed batch is rebuilt from the remaining unhandled messages.
            c.execute('UPDATE events SET status=?,completed_at=? WHERE id=?',('human_handled' if refs<=ids else 'superseded',s.now(),event_id))


def receive():
    cfg,lab=connection()
    if not cfg:return None
    import test_mode as tm,live_runtime,wechat_test as w
    state=tm.state();run=lab.current()
    if run['id']!=cfg['run_id']:raise ValueError('inactive_family_run')
    rows=lab.read('SELECT * FROM ingress WHERE run_id=? AND family_id=? AND projected=0 ORDER BY seq',(cfg['run_id'],cfg['family_id']))
    for row in rows:
        body=json.loads(row['body']);sid=row['id']
        if row['kind']=='completion':
            from completion_events import accept
            accept(body,state['run_id'],'workbench')
        elif row['kind']=='highlight':
            from highlight_events import ingest
            ingest(body)
        with contextlib.closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('CREATE TABLE IF NOT EXISTS lab_ingress(id TEXT PRIMARY KEY,seq INTEGER,business_at TEXT,occurred_at TEXT,received REAL)')
            if c.execute('SELECT 1 FROM lab_ingress WHERE id=?',(sid,)).fetchone():continue
            if row['kind']=='message':
                text=body.get('text','');payload=body.get('provider_payload')
                if payload and payload.get('messageType')==2:
                    import voice_understanding as voice
                    text=voice.marker(voice.register(c,s.R,state,sid,payload=payload))
                elif payload and payload.get('messageType')==6:
                    import image_inputs
                    text=image_inputs.marker(image_inputs.register(c,s.R,state,sid,payload=payload))
                c.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,1)',(sid,'user',text,dt.datetime.fromtimestamp(row['received'],dt.timezone.utc).isoformat(),'lab_student_inbound',hashlib.sha256(text.encode()).hexdigest()))
                live_runtime.enqueue(c,'inbound:'+sid,'passive',{'source_message_id':sid})
            elif row['kind']=='note':
                pass  # Teacher-only; no model-visible projection.
            elif row['kind']=='answer':
                c.execute('CREATE TABLE IF NOT EXISTS lab_learning_facts(id TEXT PRIMARY KEY,body TEXT,business_at TEXT)')
                c.execute('INSERT OR IGNORE INTO lab_learning_facts VALUES(?,?,?)',(sid,row['body'],row['business_at']))
            c.execute('INSERT INTO lab_ingress VALUES(?,?,?,?,?)',(sid,row['seq'],row['business_at'],row['occurred_at'],row['received']))
    # Only terminal/unknown provider facts project back; locally queued is not provider accepted.
    parts=lab.read('SELECT * FROM outbox WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id']))
    with contextlib.closing(sqlite3.connect(s.DB)) as c,c:
        project_human_handled(c,lab,cfg)
        for part in parts:
            if part['kind']=='human' and part['status']=='sent':
                c.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,1)',('teacher:'+part['id'],'assistant',part['body'],dt.datetime.fromtimestamp(part['updated'],dt.timezone.utc).isoformat(),'human_teacher_sent',hashlib.sha256(part['body'].encode()).hexdigest()))
            mapped={'queued':'accepted','submitting':'unknown','cancelled':'failed','expired':'failed'}.get(part['status'],part['status'])
            c.execute("UPDATE test_outbox SET status=CASE WHEN status='sent' THEN status ELSE ? END,request_id=?,error=?,updated_at=? WHERE id=?",(mapped,part['request_id'],part['error'],s.now(),part['id']))
            for key, in c.execute('SELECT action_key FROM test_outbox_parts WHERE operation_id=?',(part['id'],)).fetchall():w.reconcile_bundle(c,key)
    f=lab.read('SELECT * FROM families WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id']))[0]
    seq=rows[-1]['seq'] if rows else f['projected_seq']
    lab.acknowledge(cfg['run_id'],cfg['family_id'],seq,f['applied_clock'])
    return None


def tick(busy=False):
    cfg,lab=connection()
    if not cfg:return None
    verify();receive()
    if not busy:lab.finish_turn(cfg['run_id'],cfg['family_id'])
    state=__import__('test_mode').state();r=lab.current()
    if not busy:
        if state['as_of']!=r['as_of']:
            jumps=lab.read('SELECT source,target FROM transitions WHERE run_id=? AND epoch=?',(cfg['run_id'],r['epoch']))
            if jumps:
                jump=jumps[0]
                with contextlib.closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute("UPDATE service_tasks SET status='expired',finished_at=?,result=? WHERE status IN ('planned','deferred') AND kind NOT IN ('plan','completion_plan') AND due_at>? AND due_at<?",(s.now(),encode({'code':'clock_jump_skipped','reason':'手动跳时越过触发点，本次主动事项不补发；逐个体验请使用下一事件。'}),jump['source'],jump['target']))
            __import__('test_mode').advance(r['as_of'])
        # Clock is applied locally before accepting newly occurred learning facts.
        # The planner sees the complete projection before it can claim work.
        from family_lab_scenario import publish_due
        if publish_due(lab,cfg,__import__('test_mode').state()['as_of']):receive()
        f=lab.read('SELECT * FROM families WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id']))[0]
        lab.acknowledge(cfg['run_id'],cfg['family_id'],f['projected_seq'],r['epoch'])
    # Project existing risk alerts into the teacher queue without changing their evidence.
    if s.read("SELECT 1 FROM sqlite_master WHERE name='operator_alerts'"):
        for a in s.read("SELECT * FROM operator_alerts WHERE status NOT IN ('dismissed','resolved')"):
            if lab.read('SELECT 1 FROM cases WHERE run_id=? AND family_id=? AND EXISTS(SELECT 1 FROM json_each(cases.evidence) WHERE value=?)',(cfg['run_id'],cfg['family_id'],a['id'])):continue
            problem='human:'+a.get('category','needs_teacher')
            lab.case(cfg['run_id'],cfg['family_id'],problem,[a['id']],a.get('reason','需要老师查看原话'),60)
    return status()


def learning_overlay(data,state):
    if not configuration():return data
    with contextlib.closing(sqlite3.connect(s.DB)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='lab_learning_facts'").fetchone():return data
        for raw, in c.execute('SELECT body FROM lab_learning_facts WHERE business_at<=? ORDER BY business_at',(state['as_of'],)):
            record=json.loads(raw);key='expression_assignments' if record.pop('answer_kind')=='expression' else 'quiz_attempts'
            data[key].append(record)
    return data


def begin(token,owner_type,owner_id):
    cfg,lab=connection()
    if not cfg:return
    lab.begin(cfg['run_id'],cfg['family_id'],token,owner_type,owner_id)


def enqueue(key,operation,body,message_type,action_key=None,human=False,fixed=False):
    cfg,lab=connection()
    if not cfg:raise ValueError('lab_scope_required')
    if not human and not fixed and message_type==7:body=body.replace('\\n','\n')
    # Address changes and story text do not create a handoff. Explicit human
    # requests and the central takeover, route and receipt gates still apply.
    token=None
    with contextlib.closing(sqlite3.connect(s.DB)) as c:
        if action_key:
            row=c.execute('SELECT h.token,g.owner_type FROM message_hook_deliveries h JOIN message_hook_generations g ON g.token=h.token WHERE h.action_key=?',(action_key,)).fetchone()
            if row and row[1] not in ('highlight_original','weekly_material_original','operator'):token=row[0]
        # Host fixed media still goes through every central control and route gate.
        kind='human' if human else 'host' if fixed or not token else 'ai'
        group=action_key or re.sub(r':(?:text|image|emoji):\d+$','',key)
        task=c.execute('SELECT t.skill,t.expires_at,t.candidate_id FROM service_links l JOIN service_tasks t ON t.id=l.task_id WHERE l.action_key=? LIMIT 1',(group,)).fetchone()
    if task and task[0]=='plan-weekend-makeup':
        lab.case(cfg['run_id'],cfg['family_id'],'parent:S09:'+__import__('test_mode').state()['as_of'][:10],[group],'家长专属补读沟通；请老师处理，不向学生索取家长授权',50)
        raise ValueError('parent_only_service_routed_to_teacher')
    match=re.search(r':(text|image|emoji):(\d+)$',key)
    ordinal=(int(match[2])+{'text':0,'image':100,'emoji':200}[match[1]]) if match else 0
    state=__import__('test_mode').state()
    semantic=(task[0]+':'+state['as_of'][:10]+':'+str(ordinal)) if task else ('manual:'+operation if human else None)
    item=lab.enqueue(cfg['run_id'],cfg['family_id'],operation,group,ordinal,body,message_type,token,kind,task[1] if task else None,semantic)
    return {'errcode':0,'requestId':'queued:'+item['id'],'queued':True}


def parent_task(task):
    cfg,lab=connection()
    if not cfg:return False
    parent=task.get('skill')=='plan-weekend-makeup';label='S09';reason='核对本周实际缺课，与家长约定补读；学生没有家长授权。'
    if task.get('skill')=='weekly-learning-review':
        snap=__import__('test_mode').snapshot();day=dt.date.fromisoformat(task['day']);start=(day-dt.timedelta(days=day.weekday())).isoformat()
        parent=not any(x['status']=='completed' and start<=x['scheduled_date']<=task['day'] for x in snap['lessons_as_of'])
        label='S10';reason='本周课程零完课：当周 PDF 仍按正式链路交付；家长沟通时间和零完课原因由老师处理，不能向学生索取家长授权。'
    if not parent:return False
    lab.case(cfg['run_id'],cfg['family_id'],'parent:'+label+':'+task['day'],[task['id']],reason,50)
    with contextlib.closing(sqlite3.connect(s.DB)) as c,c:
        c.execute("UPDATE service_tasks SET status='blocked',result=? WHERE id=?",(encode({'code':'parent_only_teacher_workbench'}),task['id']))
        c.execute("DELETE FROM metadata WHERE key='active_service_task'")
    return True


def model_ticket(model):
    cfg,lab=connection()
    if not cfg:return None
    f=lab.read('SELECT * FROM families WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id']))[0]
    g=lab.read('SELECT * FROM generations WHERE token=?',(f['turn_token'],))
    owner=g[0]['owner_id'] if g else 'maintenance'
    kind='interactive' if g and g[0]['owner_type']=='event' else 'background' if g else 'maintenance'
    return lab,lab.model_enqueue(cfg['run_id'],cfg['family_id'],owner,kind,model)


if __name__=='__main__':
    command=sys.argv[1]
    if command=='tick':result=tick(len(sys.argv)>2 and sys.argv[2]=='true')
    elif command=='parent-task':result=parent_task(json.loads(sys.argv[2]))
    else:result={'verify':verify,'status':status,'receive':receive}[command]()
    print(encode(result))
