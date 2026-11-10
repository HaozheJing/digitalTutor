"""Authenticated teacher/admin workbench plus sole real-message gateway."""
import contextlib,datetime as dt,hashlib,hmac,json,mimetypes,re,sqlite3,subprocess,sys,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlencode
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
from family_lab import Lab,encode,TZ


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*a,**k):return None


class Gateway:
    def __init__(self,home):
        self.home=Path(home);self.lab=Lab(self.home/'control.sqlite')
        from family_lab_contacts import initialize
        initialize(self.home,self.lab)
        self.private=json.loads((self.home/'gateway.private.json').read_text());self.stopped=threading.Event()
        self.errors=[]

    def callbacks(self):
        lab=self.lab
        cursor=int(lab.read("SELECT value FROM settings WHERE key='callback_cursor'")[0]['value'])
        with contextlib.closing(sqlite3.connect(Path(self.private['database']).as_uri()+'?mode=ro',uri=True)) as c:
            c.row_factory=sqlite3.Row
            rows=[dict(r) for r in c.execute('SELECT * FROM callbacks WHERE id>? ORDER BY id LIMIT 200',(cursor,))]
        run=lab.current();bindings=lab.read("SELECT * FROM bindings WHERE run_id=? AND enabled=1 AND mode='wechat'",(run['id'],))
        for row in rows:
            p={k.strip():v for k,v in json.loads(row['body_json']).items()}
            if row['status']=='received_token_verified':
                if row['kind']=='send_result':
                    candidates=lab.read('SELECT * FROM outbox WHERE id=? OR request_id=?',(p.get('externalRequestId'),p.get('requestId')))
                    if len(candidates)==1:
                        part=candidates[0];route=json.loads(part['route']) if part['mode']=='wechat' else {}
                        if all(route.get(k)==p.get(k) for k in ['orgId','imBotId','imContactId']):
                            if p.get('sendCode')==0 and p.get('errorCode',0)==0 and p.get('messageId'):
                                lab.receipt('wechat:'+str(row['id']),part['id'],'sent',part['route'],p.get('requestId'),str(p['messageId']),p)
                            elif p.get('sendCode') or p.get('errorCode'):
                                lab.receipt('wechat:'+str(row['id']),part['id'],'failed',part['route'],p.get('requestId'),raw=p)
                elif row['kind']=='message':
                    for b in bindings:
                        route=json.loads(b['route'])
                        personal=('isSelf' not in p and p.get('contactType')==1 and p.get('externalUserId') and p.get('externalUserId')==p.get('customerExternalUserId') and p.get('imContactId')!=p.get('imBotId'))
                        allowed=all(p.get(k)==route[k] for k in route) and not p.get('imRoomId') and (p.get('isSelf') is False or personal) and p.get('source') not in (1,2,3,4,5,6,7,8,9,11,12,13,14,15)
                        if allowed and p.get('messageId'):
                            body=p.get('payload',{}).get('pureText') or p.get('payload',{}).get('text') or '[收到媒体，等待解析]'
                            lab.ingest(run['id'],b['family_id'],'wechat:'+str(p['messageId']),'message',{'text':body,'provider_payload':p,'provenance':'verified_wechat_callback'})
            with lab.transaction() as c:c.execute("UPDATE settings SET value=? WHERE key='callback_cursor'",(str(row['id']),))

    def deliver(self,part):
        if part['mode']=='simulation':
            request='sim:'+part['id'];self.lab.transport_result(part['id'],'accepted',request)
            self.lab.receipt('sim-receipt:'+part['id'],part['id'],'sent',part['route'],request,'sim-message:'+part['id'])
            return
        route=json.loads(part['route'])
        from family_lab_contacts import authorized
        if not authorized(self.lab,route):
            self.lab.transport_result(part['id'],'failed',error='recipient_not_registered_by_teacher');return
        base=self.private['api_base_url'];parsed=urlsplit(base)
        if parsed.scheme!='https' or parsed.username or parsed.query or parsed.fragment:raise ValueError('invalid_provider_origin')
        kind=part['message_type'];body=part['body']
        payload=json.loads(body) if kind==1 else {'imageUrl':body} if kind==5 else {'url':body} if kind==6 else {'text':body}
        request=Request(base.rstrip('/')+'/api/v2/message/send?'+urlencode({'token':self.private['expected_token']}),encode({'externalRequestId':part['id'],'imBotId':route['imBotId'],'imContactId':route['imContactId'],'messageType':kind,'payload':payload}).encode(),{'Content-Type':'application/json'})
        try:
            with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=20) as r:result=json.loads(r.read(65536))
            if result.get('errcode')==0 and result.get('requestId'):self.lab.transport_result(part['id'],'accepted',str(result['requestId']))
            else:self.lab.transport_result(part['id'],'failed',error='provider_rejected')
        except Exception as exc:self.lab.transport_result(part['id'],'unknown',error=type(exc).__name__)

    def poll(self):
        while not self.stopped.wait(.5):
            try:self.callbacks();self.lab.receipt_timeouts()
            except Exception as exc:self.error('callbacks',exc)

    def sends(self):
        while not self.stopped.wait(.3):
            try:
                part=self.lab.claim_send()
                if part:self.deliver(part)
            except Exception as exc:self.error('send',exc)

    def clock(self):
        anchor=None;mono=None
        while not self.stopped.wait(.5):
            try:
                r=self.lab.current();key=(r['id'],r['epoch'],r['anchor_wall'])
                if r['paused'] or r['clock_mode']!='realtime':anchor=None;continue
                if anchor!=key:anchor=key;mono=time.monotonic()
                target=dt.datetime.fromisoformat(r['anchor_business'])+dt.timedelta(seconds=int(time.monotonic()-mono))
                with self.lab.transaction() as c:
                    if target.date()>dt.date(2025,11,9):
                        c.execute("UPDATE runs SET clock_mode='frozen',paused=1 WHERE id=?",(r['id'],))
                    else:c.execute("UPDATE runs SET as_of=? WHERE id=? AND epoch=? AND clock_mode='realtime' AND paused=0",(target.isoformat(timespec='seconds'),r['id'],r['epoch']))
            except Exception as exc:self.error('clock',exc)

    def error(self,where,exc):
        self.errors=(self.errors+[{'at':time.time(),'where':where,'error':type(exc).__name__}])[-20:]

    def start(self):
        self.threads=[threading.Thread(target=f,daemon=True) for f in [self.poll,self.sends,self.clock]]
        for t in self.threads:t.start()


class Workbench:
    def __init__(self,home):
        self.home=Path(home);self.lab=Lab(self.home/'control.sqlite');self.gateway=Gateway(home)
        from family_lab_contacts import Contacts
        self.contacts=Contacts(home,self.lab)
        self.token=json.loads((self.home/'workbench.private.json').read_text())['token']

    def family(self,fid):
        run=self.lab.current();rows=self.lab.read('SELECT * FROM families WHERE run_id=? AND family_id=?',(run['id'],fid))
        if len(rows)!=1:raise ValueError('family_not_in_run')
        return rows[0]

    def detail(self,fid):
        family=self.family(fid);workspace=Path(family['workspace']);db=workspace/'runtime/live.sqlite'
        with contextlib.closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as c:
            c.row_factory=sqlite3.Row
            tables={x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            c.execute('BEGIN')
            session = json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
            context_id = session.get('conversation_id','')
            def rows(sql):return [dict(x) for x in c.execute(sql)]
            data={'family':family,'context_id':context_id,'messages':rows('SELECT * FROM messages WHERE visible=1 ORDER BY rowid DESC LIMIT 100')[::-1],
              'tasks':rows('SELECT * FROM service_tasks ORDER BY due_at DESC LIMIT 100'),
              'plans':rows('SELECT * FROM service_plan_versions ORDER BY created_at DESC LIMIT 8'),
              'learning_events':rows('SELECT * FROM completion_events ORDER BY completed_at') if 'completion_events' in tables else [],
              'alerts':rows('SELECT * FROM operator_alerts ORDER BY created_at DESC') if 'operator_alerts' in tables else []}
        data['learning']=json.loads(subprocess.check_output([sys.executable,'-B','-c','import test_mode,json;print(json.dumps(test_mode.snapshot(),ensure_ascii=False))'],cwd=workspace/'adapters',text=True,timeout=20))
        data['role_card']=json.loads((workspace/'role-card.json').read_text())
        inbound=self.lab.read("SELECT id,body FROM ingress WHERE run_id=? AND family_id=? AND kind='message'",(family['run_id'],fid))
        originals={x['id']:json.loads(x['body']).get('provider_payload',{}) for x in inbound}
        for message in data['messages']:
            original=originals.get(message['source_id'],{});payload=original.get('payload') or {};kind=original.get('messageType')
            url=payload.get('voiceUrl') if kind==2 else (payload.get('url') or payload.get('imageUrl')) if kind==6 else None
            if isinstance(url,str) and urlsplit(url).scheme=='https' and urlsplit(url).hostname and not urlsplit(url).username:
                message['media']={'type':kind,'url':url}
        with contextlib.closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as c:
            if 'voice_inputs' in tables:
                voices={x[0]:json.loads(x[1]) for x in c.execute('SELECT source_id,result FROM voice_inputs WHERE result IS NOT NULL')}
                for message in data['messages']:
                    if message['source_id'] in voices:message['voice_understanding']=voices[message['source_id']]
        data['outbox']=self.lab.read('SELECT * FROM outbox WHERE run_id=? AND family_id=? AND rowid>? ORDER BY created',(family['run_id'],fid,session.get('conversation_outbox_cursor',0)))
        data['notes']=self.lab.read("SELECT * FROM ingress WHERE run_id=? AND family_id=? AND kind='note' ORDER BY received",(family['run_id'],fid))
        data['cases']=self.lab.read('SELECT c.*,d.ai_body,d.edited_body,d.revision AS draft_revision,d.family_epoch AS draft_epoch FROM cases c LEFT JOIN drafts d ON d.case_id=c.id WHERE c.run_id=? AND c.family_id=? ORDER BY c.first_wait',(family['run_id'],fid))
        return data

    def action(self,path,v):
        run=self.lab.current()
        if v.get('run_id')!=run['id']:raise ValueError('轮次已变化，请刷新')
        fid=v.get('family_id','F01');family=self.family(fid)
        if path=='clear-context':
            from family_context_reset import reset
            return reset(self,family,v)
        if path=='contacts-refresh':return self.contacts.refresh()
        if path=='contacts-bind':return self.contacts.apply(run['id'],v.get('mapping'),v.get('versions'))
        if v.get('case_id'):
            if not self.lab.read('SELECT 1 FROM cases WHERE id=? AND run_id=? AND family_id=?',(v['case_id'],run['id'],fid)):raise ValueError('case_family_mismatch')
        if path=='new-round':
            if v['mode'] not in ('simulation','wechat'):raise ValueError('invalid_mode')
            if v['mode']=='wechat' and not self.lab.read("SELECT 1 FROM settings WHERE key='real_channel_owned' AND value='true'"):raise ValueError('微信通道尚未完成独占切换')
            if not run['paused']:raise ValueError('请先暂停实验，再新建轮次')
            if any(f['busy'] for f in self.lab.snapshot()['families']) or self.lab.read("SELECT 1 FROM model_attempts WHERE run_id=? AND status IN ('queued','running')",(run['id'],)):raise ValueError('请等待当前模型结束')
            if self.lab.read("SELECT 1 FROM outbox WHERE run_id=? AND status IN ('submitting','accepted','unknown')",(run['id'],)):raise ValueError('先核对在途和未知回执；不会清除或重放')
            from family_lab import stamp
            target=stamp(v['as_of'])
            if not '2025-10-13'<=target[:10]<='2025-11-09':raise ValueError('outside_twenty_lesson_month')
            with self.lab.transaction() as c:
                old=c.execute("SELECT value FROM settings WHERE key='new_round_request'").fetchone()
                if old and json.loads(old[0])['status'] in ('queued','preparing'):raise ValueError('已有新建轮次请求')
                request={'id':uuid.uuid4().hex,'status':'queued','mode':v['mode'],'as_of':target,'from_run':run['id']}
                c.execute("INSERT OR REPLACE INTO settings VALUES('new_round_request',?)",(encode(request),))
            return request
        if path=='control':
            action=v['action'];value=v['value']
            if type(value) is not bool:raise ValueError('boolean_required')
            self.lab.control(run['id'],fid,action,value,v.get('reason','teacher_workbench'))
            if action=='takeover' and not value:self.release_legacy_alerts(family,'老师明确恢复 AI；事项是否结束由老师另外决定')
            if action=='takeover' and value and not self.lab.read("SELECT 1 FROM cases WHERE run_id=? AND family_id=? AND status='open'",(run['id'],fid)):
                self.lab.case(run['id'],fid,'teacher_requested',[],'老师主动接管',60)
            return {'status':'updated'}
        if path in ('advance','next-event'):
            from family_lab import stamp
            pending=self.lab.read("SELECT id FROM transitions WHERE run_id=? AND status<>'settled' ORDER BY created DESC LIMIT 1",(run['id'],))
            if pending:
                current=self.lab.transition_status(pending[0]['id'])
                if current['status']!='settled':
                    if path!='next-event':raise ValueError('本次事件尚未处理完，请先继续处理当前事件，稍后再推进')
                    if run['paused']:self.lab.control(run['id'],fid,'experiment_pause',False,'老师点击继续处理当前事件')
                    return {'transition_id':current['id'],'target':current['target'],'continued':True}
            # Finish already-due work before selecting a later date; otherwise an
            # unstarted daily planner has no future tasks for next_event to see.
            if path=='next-event' and any(a['due_count'] for a in self.activities(self.lab.snapshot())):
                return {'transition_id':self.lab.advance(run['id'],run['as_of'],auto_execute=True,current_event=True),'target':run['as_of'],'continued':True}
            upcoming=self.next_event(run)
            target=upcoming if path=='next-event' else stamp(v['target'])
            if run['mode']=='wechat' and target>upcoming:raise ValueError('真实微信请逐个触发点推进；下一事件：'+upcoming)
            return {'transition_id':self.lab.advance(run['id'],target,auto_execute=True),'target':target}
        if path=='clock-mode':self.lab.clock_mode(run['id'],v['mode']);return {'mode':v['mode']}
        if path=='message':
            b=self.lab.read('SELECT mode FROM bindings WHERE run_id=? AND family_id=?',(run['id'],fid))[0]
            if b['mode']!='simulation':raise ValueError('请使用真实微信发送，不能向实发家庭伪造学生入站')
            return self.lab.ingest(run['id'],fid,v['request_id'],'message',{'text':v['body'],'provenance':'simulated_student'})
        if path=='completion':
            number=int(v['lesson_no']);lesson='L2-'+str(number).zfill(3)
            facts=json.loads((Path(family['workspace'])/'runtime/fixtures/mock_uid_00001_first_month.json').read_text())
            selected=next((x for x in facts['learning_daily'] if x['id']==lesson),None)
            if not selected or selected['date']+'T07:00:00+08:00'>run['as_of']:raise ValueError('该课尚未解锁')
            payload={'schema_version':1,'event_type':'lesson.completed','event_id':v['request_id'],'completion_id':v['request_id'],
              'uid':fid,'enrollment_id':'enrollment-'+fid+'-L2','lesson_id':lesson,'completed_at':run['as_of']}
            return self.lab.ingest(run['id'],fid,v['request_id'],'completion',payload)
        if path=='answer':
            number=int(v['lesson_no']);facts=json.loads((Path(family['workspace'])/'runtime/fixtures/mock_uid_00001_first_month.json').read_text())
            lesson=next((x for x in facts['learning_daily'] if x['lesson_no']==number),None)
            if not lesson or lesson['date']+'T07:00:00+08:00'>run['as_of']:raise ValueError('该课尚未解锁')
            if v.get('answer_kind') not in ('quiz','expression') or not v.get('body','').strip() or len(v['body'])>3000:raise ValueError('invalid_answer')
            payload={'id':v['request_id'],'uid':fid,'lesson_id':lesson['id'],'lesson_no':number,'scheduled_date':lesson['date'],'title':lesson['title'],'question':v.get('question','体验者自由表达'),'answer':v['body'],'submitted_at':run['as_of'],'selection_recorded_at':None,'source':'external_simulated_learning_event','grading_status':'answer_key_not_connected','answer_kind':v['answer_kind']}
            return self.lab.ingest(run['id'],fid,v['request_id'],'answer',payload)
        if path=='test-highlight':
            # The test producer declares achievement; delivery retains these exact bytes.
            raw=(Path(__file__).resolve().parents[1]/'assets/family-lab'/(fid+'.png')).read_bytes();digest=hashlib.sha256(raw).hexdigest()
            target=self.home/'public-media'/(digest+'.png')
            if not target.exists():target.write_bytes(raw)
            event={'schema_version':1,'event_id':v['request_id'],'achievement_id':v['request_id'],'uid':fid,'enrollment':{'class_type':'L2','cohort':'61'},'achievement_type':'expressive_answer','achieved_at':run['as_of'],'expires_at':run['as_of'][:10]+'T21:59:59+08:00','caption':v.get('body') or family['student']+'，你认真分享了自己的阅读发现，老师把这份小小的进步记录下来了。','image':{'url':'https://82.156.162.52/five-family/media/'+digest+'.png'},'evidence':'测试管理员声明本次模拟表达高光已达成；原文原图由测试事件生产器提供，交付时不调用模型。'}
            return self.action('highlight',{**v,'event':event})
        if path=='highlight':
            if family['applied_clock']!=run['epoch']:raise ValueError('请等待该家庭时钟同步')
            check=subprocess.run([sys.executable,'-B','-c','import sys,json,highlight_events;print(json.dumps(highlight_events.ingest(json.load(sys.stdin),dry_run=True)))'],input=encode(v['event']),cwd=Path(family['workspace'])/'adapters',text=True,capture_output=True,timeout=10)
            if check.returncode:raise ValueError('高光校验失败：请核对家庭、班级、事件时间、原文和允许的原图地址')
            return self.lab.ingest(run['id'],fid,v['request_id'],'highlight',v['event'],v['event']['achieved_at'])
        if path=='note':return self.lab.ingest(run['id'],fid,v['request_id'],'note',{'text':v['body'],'method':v.get('method','internal_note')})
        if path=='draft':return self.lab.save_draft(v['case_id'],v['body'],int(v['revision']))
        if path=='close-case':
            if type(v.get('expected_seq')) is not int:raise ValueError('请刷新工作台后核对消息，再结束事项')
            self.lab.close_case(v['case_id'],v['expected_seq'])
            return {'status':'closed','ai_resumed':False}
        if path=='human-reply':
            body=v['body'].strip()
            if not body or len(body)>600:raise ValueError('请输入1至600字人工回复')
            # Explicit teacher send is one immutable request; browsing does not take over.
            if not family['held']:raise ValueError('请先接管，再发送人工回复')
            group='teacher:'+v['request_id'];operation='teacher-'+hashlib.sha256((run['id']+fid+group).encode()).hexdigest()[:40]
            result=self.lab.enqueue(run['id'],fid,operation,group,0,body,7,kind='human',semantic_key=operation)
            with self.lab.transaction() as c:self.lab.audit(c,'teacher_reply',{'operation_id':operation,'case_id':v.get('case_id'),'does_not_close_case':True},run['id'],fid)
            return result
        raise ValueError('unknown_action')

    def next_event(self,run):
        targets=[]
        from family_lab_scenario import next_time
        completion=next_time(self.lab,run)
        if completion:targets.append(completion)
        for f in self.lab.snapshot()['families']:
            if f['held'] or f['paused']:continue
            with contextlib.closing(sqlite3.connect(Path(f['workspace'])/'runtime/live.sqlite')) as c:
                targets.extend(x[0] for x in c.execute("SELECT due_at FROM service_tasks WHERE due_at>? AND status IN ('planned','deferred')",(run['as_of'],)))
        opening=dt.datetime.fromisoformat(run['as_of']).replace(hour=6,minute=30,second=0)
        if opening.isoformat(timespec='seconds')<=run['as_of']:opening+=dt.timedelta(days=1)
        return min(targets+[opening.isoformat(timespec='seconds')])

    def activities(self,snapshot):
        from family_lab_progress import activities
        return activities(self.lab,snapshot)

    def state(self):
        result=self.lab.snapshot()
        result['activities']=self.activities(result)
        latest=self.lab.read('SELECT id FROM transitions WHERE run_id=? ORDER BY created DESC LIMIT 1',(result['run']['id'],))
        result['transition']=self.lab.transition_status(latest[0]['id']) if latest else None
        return {**result,'gateway_errors':self.gateway.errors,'round_request':self.lab.read("SELECT value FROM settings WHERE key='new_round_request'")}

    def release_legacy_alerts(self,family,reason):
        # Compatibility projection only. Central cases and controls stay authoritative.
        with contextlib.closing(sqlite3.connect(Path(family['workspace'])/'runtime/live.sqlite')) as c,c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='operator_alerts'").fetchone():
                c.execute("UPDATE operator_alerts SET status='dismissed',resolution_reason=? WHERE status='pending'",(reason,))

    def handler(self):
        owner=self;root=Path(__file__).resolve().parent
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def send(self,status,body,mime='application/json; charset=utf-8'):
                raw=body if isinstance(body,bytes) else encode(body).encode()
                self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(raw)
            def auth(self):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.token)
            def do_GET(self):
                try:
                    url=urlsplit(self.path);path=url.path
                    static={'/five-family/':('family-workbench.html','text/html; charset=utf-8'),'/five-family/app.js':('family-workbench.js','text/javascript; charset=utf-8'),'/five-family/style.css':('family-workbench.css','text/css; charset=utf-8')}
                    if path in static:
                        name,mime=static[path];return self.send(200,(root/name).read_bytes(),mime)
                    if re.fullmatch(r'/five-family/media/[a-f0-9]{64}\.(png|jpg|pdf)',path):
                        p=owner.home/'public-media'/path.rsplit('/',1)[1]
                        if p.exists() and not p.is_symlink():return self.send(200,p.read_bytes(),mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
                        return self.send(404,{'error':'media_not_found'})
                    if not self.auth():return self.send(401,{'error':'请使用原测试工作台的访问密钥连接'})
                    if path=='/five-family/api/state':return self.send(200,owner.state())
                    if path=='/five-family/api/contacts':return self.send(200,owner.contacts.snapshot())
                    if path=='/five-family/api/family':return self.send(200,owner.detail(parse_qs(url.query).get('id',[''])[0]))
                    if path=='/five-family/api/transition':return self.send(200,owner.lab.transition_status(parse_qs(url.query).get('id',[''])[0]))
                    if path=='/five-family/api/report':
                        rid=parse_qs(url.query).get('run_id',[''])[0]
                        runs=owner.lab.read('SELECT * FROM runs WHERE id=?',(rid,))
                        if not runs:raise ValueError('round_missing')
                        from family_lab_cost import summary
                        facts=owner.lab.read('SELECT id,family_id,kind,business_at,occurred_at,received,body FROM ingress WHERE run_id=? ORDER BY received',(rid,))
                        for fact in facts:
                            body=json.loads(fact['body']);body.pop('provider_payload',None);fact['body']=body
                        return self.send(200,{'run':runs[0],'costs':summary(owner.lab,rid),'model_requests':owner.lab.read('SELECT * FROM model_attempts WHERE run_id=? ORDER BY created',(rid,)),'outbox':owner.lab.read('SELECT * FROM outbox WHERE run_id=? ORDER BY created',(rid,)),'cases':owner.lab.read('SELECT * FROM cases WHERE run_id=?',(rid,)),'facts':facts,'audit':owner.lab.read('SELECT * FROM audit WHERE run_id=? ORDER BY id',(rid,))})
                    return self.send(404,{'error':'not_found'})
                except Exception as exc:self.send(409,{'error':str(exc)[:240] if isinstance(exc,ValueError) else type(exc).__name__})
            def do_POST(self):
                if not self.auth():return self.send(401,{'error':'unauthorized'})
                if self.headers.get('X-Workbench')!='1' or not self.headers.get('Content-Type','').startswith('application/json'):return self.send(403,{'error':'workbench_only'})
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=32768:raise ValueError('invalid_body_size')
                    v=json.loads(self.rfile.read(size))
                    if not self.path.startswith('/five-family/api/'):raise ValueError('invalid_route')
                    self.send(200,owner.action(self.path.rsplit('/',1)[1],v))
                except Exception as exc:self.send(409,{'error':str(exc)[:240] if isinstance(exc,ValueError) else type(exc).__name__})
        return Handler
