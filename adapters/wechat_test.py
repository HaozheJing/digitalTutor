"""Single allowlisted contact, verified callbacks and durable text/image outbox."""
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import sql_tools as s
import test_mode as tm


def config():
    lab=__import__('family_lab_bridge').configuration()
    if lab:return {'family_lab':True,'uid':lab['family_id']}
    simulated = __import__('experiment_runtime').simulation_config()
    if simulated:
        return simulated
    path = s.R/'config/test-wechat.json'
    return json.loads(path.read_text()) if path.exists() else None


def initialize():
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS test_callbacks(id INTEGER PRIMARY KEY,status TEXT);
        CREATE TABLE IF NOT EXISTS test_inbound_ids(id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS test_outbox(id TEXT PRIMARY KEY,body TEXT,status TEXT,
          request_id TEXT,error TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS test_delivery_bundles(action_key TEXT PRIMARY KEY,images TEXT);
        CREATE TABLE IF NOT EXISTS test_outbox_parts(action_key TEXT,operation_id TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS test_delivery_texts(action_key TEXT PRIMARY KEY,messages TEXT);
        CREATE TABLE IF NOT EXISTS test_delivery_emojis(action_key TEXT PRIMARY KEY,assets TEXT);
        ''')
        if 'message_type' not in [r[1] for r in c.execute('PRAGMA table_info(test_outbox)')]:
            c.execute('ALTER TABLE test_outbox ADD COLUMN message_type INTEGER NOT NULL DEFAULT 7')


def provider_config():
    settings = config()
    if settings.get('family_lab'):
        __import__('family_lab_bridge').verify()
        return settings, {'imBotId':'central-gateway','imContactId':settings['uid']}
    if settings.get('simulation'):
        __import__('experiment_runtime').verify()
        return settings, {'imBotId': 'simulation', 'imContactId': 'simulation'}
    if __import__('manual_workbench').configured():__import__('manual_workbench').verify()
    cfg = json.loads(Path(settings['provider_config']).read_text())
    routes = [r for r in cfg['allowed_routes'] if all(r.get(k)==settings['route'].get(k)
              for k in ('orgId','imBotId','chatId','imContactId'))]
    if len(routes)!=1 or routes[0]['orgId']!=cfg['expected_org_id']:
        raise ValueError('Bound test contact does not match provider allowlist')
    return cfg,routes[0]


def receive(skip_controls=False):
    if __import__('family_lab_bridge').configuration():return __import__('family_lab_bridge').receive()
    settings = config()
    if not settings or not tm.state():
        return None
    if settings.get('simulation'):
        result=__import__('experiment_runtime').receive()
        return None if skip_controls else result
    cfg,route = provider_config()
    checkpoint=s.read('SELECT MAX(id) AS id FROM test_callbacks')[0]['id']
    with closing(sqlite3.connect(Path(cfg['database']).as_uri()+'?mode=ro',uri=True)) as inbox:
        inbox.row_factory=sqlite3.Row
        controls=s.read("SELECT id FROM test_callbacks WHERE status='control_pending' ORDER BY id LIMIT 1") if not skip_controls else []
        if controls:
            row=inbox.execute('SELECT body_json FROM callbacks WHERE id=?',(controls[0]['id'],)).fetchone()
            if row:
                p=json.loads(row['body_json']);body=p.get('payload',{}).get('pureText') or p.get('payload',{}).get('text')
                return {'callback_id':controls[0]['id'],'message_id':'wechat:'+str(p['messageId']),'text':body.strip()}
        rows = inbox.execute('SELECT * FROM callbacks WHERE id>? ORDER BY id LIMIT 200',
                             (max(settings['after_callback_id'],checkpoint or 0),)).fetchall()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        for row in rows:
            if c.execute('SELECT 1 FROM test_callbacks WHERE id=?',(row['id'],)).fetchone():
                continue
            p={k.strip():v for k,v in json.loads(row['body_json']).items()}
            allowed=(row['status']=='received_token_verified' and
                     all(p.get(k)==route[k] for k in ('orgId','imBotId','imContactId')))
            if row['kind']=='send_result':
                if allowed:
                    observe(c,p)
                c.execute('INSERT INTO test_callbacks VALUES(?,?)',(row['id'],'receipt'))
                continue
            if settings.get('receipt_only'):
                c.execute('INSERT INTO test_callbacks VALUES(?,?)',(row['id'],'isolated_receipt_only'))
                continue
            personal=('isSelf' not in p and p.get('contactType')==1 and
                      bool(p.get('externalUserId')) and p['externalUserId']==p.get('customerExternalUserId')
                      and p.get('imContactId')!=p.get('imBotId'))
            allowed=(allowed and p.get('chatId')==route['chatId'] and not p.get('imRoomId')
                     and (p.get('isSelf') is False or personal)
                     and p.get('source') not in (1,2,3,4,5,6,7,8,9,11,12,13,14,15)
                     and p.get('messageId') and (p.get('messageType') in (2,6,7) or __import__('message_hooks').enabled()))
            body=p.get('payload',{}).get('pureText') or p.get('payload',{}).get('text')
            if allowed and p.get('messageType')!=7:
                body='[收到非文本消息，类型'+str(p.get('messageType'))+'；内容尚未解析，不得臆测。消息引用：'+str(p['messageId'])+']'
            if not allowed or not isinstance(body,str) or not body.strip() or len(body)>30000:
                c.execute('INSERT INTO test_callbacks VALUES(?,?)',(row['id'],'ignored'))
                continue
            message_id='wechat:'+str(p['messageId'])
            body=body.strip()
            if c.execute('SELECT 1 FROM test_inbound_ids WHERE id=?',(message_id,)).fetchone():
                c.execute('INSERT INTO test_callbacks VALUES(?,?)',(row['id'],'duplicate'))
                continue
            if p.get('messageType')==2:
                import voice_understanding as voice
                body=voice.marker(voice.register(c,s.R,tm.state(),message_id,payload=p))
            if p.get('messageType')==6:
                import image_inputs
                body=image_inputs.marker(image_inputs.register(c,s.R,tm.state(),message_id,payload=p))
            # Control commands are returned first; host waits for its agent to be idle.
            if body.startswith('测试日期') or body in ('测试状态','测试高光','测试重置','测试帮助'):
                if skip_controls:
                    c.execute('INSERT INTO test_callbacks VALUES(?,?)',(row['id'],'control_pending'))
                    continue
                return {'callback_id':row['id'],'message_id':message_id,'text':body}
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)',
                      (message_id,'user',body,row['first_received_at'],'bound_wechat_test',
                       hashlib.sha256(body.encode()).hexdigest()))
            c.execute('INSERT INTO test_inbound_ids VALUES(?)',(message_id,))
            import live_runtime
            live_runtime.enqueue(c,'inbound:'+message_id,'passive',{'source_message_id':message_id},received_at=row['first_received_at'])
            c.execute('INSERT INTO test_callbacks VALUES(?,?)',(row['id'],'ingested'))
            return {'ingested':True}
    return {'more':True} if len(rows)==200 else None


def observe(c,p):
    prior=c.execute('SELECT id,request_id,status FROM test_outbox WHERE id=? OR request_id=?',
                    (p.get('externalRequestId'),p.get('requestId'))).fetchall()
    if len(prior)!=1:
        return
    ident,request,status=prior[0]
    if p.get('externalRequestId') and p['externalRequestId']!=ident:
        return
    if request and p.get('requestId') and request!=p['requestId']:
        return
    if p.get('sendCode')==0 and p.get('errorCode',0)==0 and p.get('messageId'):
        c.execute("UPDATE test_outbox SET status='sent',error=NULL,updated_at=? WHERE id=?",(s.now(),ident))
    elif status!='sent' and (p.get('errorCode') or p.get('sendCode')):
        c.execute("UPDATE test_outbox SET status='failed',error='provider_receipt_failed',updated_at=? WHERE id=?",
                  (s.now(),ident))
    for row in c.execute('SELECT action_key FROM test_outbox_parts WHERE operation_id=?',(ident,)).fetchall():
        reconcile_bundle(c,row[0])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def http_send(cfg,route,ident,body,message_type=7):
    from message_text import LIMIT
    if message_type == 7 and len(body) > LIMIT:
        raise ValueError('单条文字超过50字')
    if cfg.get('simulation'):
        return __import__('experiment_runtime').simulated_send(ident,body,message_type)
    url=cfg['api_base_url'].rstrip('/')
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.query or parsed.fragment:
        raise ValueError('HTTPS provider required')
    request=Request(url+'/api/v2/message/send?'+urlencode({'token':cfg['expected_token']}),
                    json.dumps({'externalRequestId':ident,'imBotId':route['imBotId'],
                                'imContactId':route['imContactId'],'messageType':message_type,
                                'payload':json.loads(body) if message_type==1 else {'imageUrl':body} if message_type==5 else {'url':body} if message_type==6 else {'text':body}},ensure_ascii=False).encode(),
                    {'Content-Type':'application/json'})
    with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=20) as response:
        return json.loads(response.read(65536))


def send(key,body,message_type=7):
    """Unknown outcomes are never retried automatically, preventing duplicate sends."""
    if not config() or not tm.state():
        return {'status':'disabled'}
    if message_type not in (1,5,6,7):
        raise ValueError('Only approved emoji, text and existing highlight images are supported')
    if message_type==6:
        parsed=urlsplit(body)
        from festival_service import allowed_public_image
        existing=parsed.scheme=='https' and parsed.hostname=='coolcdn.igetcool.com' and not parsed.username and not parsed.fragment
        if not existing and not allowed_public_image(body) and not __import__('s01_reading_guides').registered(body) and not __import__('highlight_events').registered_image(body):
            raise ValueError('Image must be an existing highlight or a registered verified festival asset')
    __import__('message_hooks').refresh()
    cfg,route=provider_config()
    ident='dsh-'+hashlib.sha256(key.encode()).hexdigest()[:40]
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM test_outbox WHERE id=?',(ident,)).fetchone()
        if row:
            if row[1]!=body or row[7]!=message_type:
                raise ValueError('Immutable outbound payload changed')
            return {'status':row[2],'duplicate':True,'operation_id':ident,**({'simulation':True} if cfg.get('simulation') else {})}
        if message_type == 7 and len(body) > __import__('message_text').LIMIT:
            raise ValueError('每条最多50字，请按完整意思重写')
        human=__import__('operator_alerts').authorize_part(c,key)
        action_key=__import__('message_hooks').authorize(c,key)
        hours=__import__('service_hours')
        if not human and not hours.is_open(hours.now(c)) and not (action_key and action_key.startswith('reply:') and hours.night_passive_enabled()):
            raise __import__('service_hours').ClosedServiceHours('每日22:00至次日06:30停止自动外发')
        if message_type==6:__import__('s01_reading_guides').authorize(c,key,body)
        if message_type==5:
            __import__('emoji_assets').validate_part(c,key,body)
        __import__('preference_memory').guard_media(c,message_type)
        fixed=__import__('weekly_materials').authorize_part(c,key,ident,body) if message_type==1 else False
        if message_type==1 and (not fixed or not (cfg.get('simulation') or cfg.get('family_lab') or __import__('manual_workbench').verify())):raise ValueError('File requires a pinned host-owned authorized delivery')
        if not human and not fixed:
            __import__('operator_alerts').guard_model_delivery('reply_to_message',{})
            __import__('service_workflow').authorize_part(c,key,ident)
        c.execute('INSERT INTO test_outbox VALUES(?,?,?,?,?,?,?,?)',
                  (ident,body,'unknown',None,None,s.now(),s.now(),message_type))
        if action_key:
            c.execute('INSERT OR IGNORE INTO test_outbox_parts VALUES(?,?)',(action_key,ident))
    status,request,error='unknown',None,None
    try:
        result=__import__('family_lab_bridge').enqueue(key,ident,body,message_type,action_key,human,fixed) if cfg.get('family_lab') else http_send(cfg,route,ident,body,message_type) if message_type!=7 else http_send(cfg,route,ident,body)
        if result.get('errcode')==0 and result.get('requestId'):
            status,request='accepted',result['requestId']
        elif result.get('errcode') not in (None,0):
            status,error='failed','provider_rejected'
    except ValueError as exc:
        error='unconfirmed_transport'
        if cfg.get('family_lab'):
            _,lab=__import__('family_lab_bridge').connection()
            if not lab.read('SELECT 1 FROM outbox WHERE id=?',(ident,)):
                status,error='failed','blocked_before_transport:'+str(exc)[:100]
    except Exception:
        error='unconfirmed_transport'
    with closing(sqlite3.connect(s.DB)) as c,c:
        # A delivery callback can arrive while the HTTP request is still in flight.
        # Its terminal result outranks transport acceptance, rejection or timeout.
        c.execute("UPDATE test_outbox SET status=CASE WHEN status IN ('sent','failed') THEN status ELSE ? END,"
                  "request_id=?,error=CASE WHEN status IN ('sent','failed') THEN error ELSE ? END,updated_at=? WHERE id=?",
                  (status,request,error,s.now(),ident))
        status=c.execute('SELECT status FROM test_outbox WHERE id=?',(ident,)).fetchone()[0]
    return {'status':status,'operation_id':ident,**({'simulation':True} if cfg.get('simulation') else {})}


def queue_text(c,action_key,body):
    """Keep model-chosen text parts in the same transaction as one service delivery."""
    __import__('service_hours').register_draft(c,action_key)
    __import__('message_hooks').register(c,action_key)
    __import__('emoji_assets').queue(c,action_key)
    from message_text import parts
    messages=parts(body)
    if messages != [body]:
        c.execute('INSERT INTO test_delivery_texts VALUES(?,?)',(action_key,s.encoded(messages)))


def queue_images(c,action_key,images):
    """Persist the exact selected assets in the same transaction as the text."""
    if images:__import__('message_hooks').register(c,action_key)
    urls=list(dict.fromkeys(images))
    if not urls:
        return
    c.execute('INSERT INTO test_delivery_bundles VALUES(?,?)',(action_key,s.encoded(urls)))


def reply_with_highlights(event_id,body,highlight_ids):
    """The model selects current assets by ID, never arbitrary URLs or recipients."""
    if not 1<=len(highlight_ids)<=3 or len(set(highlight_ids))!=len(highlight_ids):
        raise ValueError('Select one to three distinct current highlights')
    available={h['id']:h for h in tm.snapshot()['available_highlights']}
    if any(ident not in available for ident in highlight_ids):
        raise ValueError('A requested highlight is unavailable at the selected test time')
    return s.mock_reply(event_id,body,[available[ident]['img_url'] for ident in highlight_ids])


def reconcile_bundle(c,action_key):
    bundle=c.execute('SELECT images FROM test_delivery_bundles WHERE action_key=?',(action_key,)).fetchone()
    text=c.execute('SELECT messages FROM test_delivery_texts WHERE action_key=?',(action_key,)).fetchone()
    emojis=c.execute('SELECT assets FROM test_delivery_emojis WHERE action_key=?',(action_key,)).fetchone()
    expected=(len(json.loads(text[0])) if text else 1)+(len(json.loads(bundle[0])) if bundle else 0)+(len(json.loads(emojis[0])) if emojis else 0)
    parts=c.execute('SELECT o.status FROM test_outbox_parts p JOIN test_outbox o ON o.id=p.operation_id WHERE p.action_key=?',
                    (action_key,)).fetchall()
    statuses=[x[0] for x in parts]
    if len(statuses)==expected and all(x=='sent' for x in statuses):
        status='sent'
    elif len(statuses)==expected and all(x in ('accepted','sent') for x in statuses):
        status='accepted'
    elif any(x in ('accepted','sent') for x in statuses):
        status='partial'
    else:
        status='failed' if 'failed' in statuses else 'unknown'
    c.execute('UPDATE deliveries SET status=? WHERE action_key=?',(status,action_key))
    __import__('message_hooks').delivery_status(c,action_key,status)
    __import__('service_workflow').reconcile(c)
    return status


def flush(limit=None):
    # Both host polling and a model tool may flush the same family. Hold an OS
    # lease across the whole manifest, including transport and final receipt
    # projection. A second sender must not turn an in-flight reservation into
    # a terminal failure while the first is still delivering its remaining parts.
    import fcntl
    with Path(str(s.DB)+'.outbound-flush.lock').open('a+b') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX)
        try:return _flush_locked(limit)
        finally:fcntl.flock(lease,fcntl.LOCK_UN)


def _flush_locked(limit=None):
    if not config() or not tm.state():
        return []
    __import__('service_hours').tick()
    results=[]
    pending=s.read("SELECT * FROM deliveries WHERE source='test_wechat_pending' ORDER BY rowid")
    def revoked(row,error):
        if isinstance(error,__import__('service_hours').ClosedServiceHours):
            with closing(sqlite3.connect(s.DB)) as c,c:return __import__('service_hours').stop_delivery(c,row['action_key'])
        hooks=__import__('message_hooks')
        if isinstance(error,hooks.StaleGeneration):return hooks.interrupt(None,row['action_key'])
        if not __import__('service_workflow').enabled():raise error
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE deliveries SET status='blocked',source='test_wechat' WHERE action_key=?",(row['action_key'],))
            __import__('service_workflow').reconcile(c)
        return {'status':'blocked','action_key':row['action_key'],'reason':'发送前授权失效；核对原交付，不自动补发剩余部分。'}
    for row in pending[:limit] if limit is not None else pending:
        key=tm.state()['run_id']+':'+row['action_key']
        text=s.read('SELECT messages FROM test_delivery_texts WHERE action_key=?',(row['action_key'],))
        with closing(sqlite3.connect(s.DB)) as c:file_payload=__import__('weekly_materials').payload_for(c,row['action_key'])
        messages=[s.encoded(file_payload)] if file_payload else json.loads(text[0]['messages']) if text else [row['body']]
        invalidated=False
        for i,body in enumerate(messages):
            try:result=send(key,body,1) if file_payload else send(key if i==0 else key+':text:'+str(i),body)
            except ValueError as error:
                results.append(revoked(row,error));invalidated=True;break
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute('INSERT OR IGNORE INTO test_outbox_parts VALUES(?,?)',(row['action_key'],result['operation_id']))
            if result['status'] not in ('accepted','sent'):
                break
        if invalidated:continue
        bundles=s.read('SELECT images FROM test_delivery_bundles WHERE action_key=?',(row['action_key'],))
        if result['status'] in ('accepted','sent') and bundles:
            for i,url in enumerate(json.loads(bundles[0]['images'])):
                try:part=send(key+':image:'+str(i),url,6)
                except ValueError as error:
                    results.append(revoked(row,error));invalidated=True;break
                with closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute('INSERT OR IGNORE INTO test_outbox_parts VALUES(?,?)',(row['action_key'],part['operation_id']))
                if part['status'] not in ('accepted','sent'):break
            if not invalidated:result=part
        if not invalidated and result['status'] in ('accepted','sent'):
            emojis=s.read('SELECT assets FROM test_delivery_emojis WHERE action_key=?',(row['action_key'],))
            for i,asset in enumerate(json.loads(emojis[0]['assets']) if emojis else []):
                try:part=send(key+':emoji:'+str(i),asset['url'],5)
                except ValueError as error:
                    results.append(revoked(row,error));invalidated=True;break
                with closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute('INSERT OR IGNORE INTO test_outbox_parts VALUES(?,?)',(row['action_key'],part['operation_id']))
                if part['status'] not in ('accepted','sent'):break
        if invalidated:continue
        with closing(sqlite3.connect(s.DB)) as c,c:
            result['status']=reconcile_bundle(c,row['action_key'])
            c.execute("UPDATE deliveries SET source='test_wechat' WHERE action_key=?",(row['action_key'],))
        results.append(result)
    return results


def trigger_highlights(manual=True,origin='operator'):
    """Compatibility entry: host-owned existing assets, never a model rewrite."""
    import host_highlights
    import observability as audit
    result=audit.invoke('host_highlights',{},host_highlights.queue,origin)
    if result['status']!='queued':return {**result,'status':'skipped' if result['status']=='idle' else result['status']}
    audit.invoke('wechat_flush',{},flush,origin)
    for item in result['items']:
        if item.get('action_key'):item['status']=s.read('SELECT status FROM deliveries WHERE action_key=?',(item['action_key'],))[0]['status']
    statuses={x['status'] for x in result['items']}
    result['status']='sent' if statuses=={'sent'} else 'accepted' if statuses<={'sent','accepted'} else 'partial' if statuses & {'sent','accepted','partial'} else 'unknown' if 'unknown' in statuses else 'failed'
    return result


def control(command):
    text=command['text']
    key='control:'+str(command['callback_id'])
    changed=False
    try:
        if text.startswith('测试日期'):
            match=re.fullmatch(r'测试日期\s+(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?',text)
            if not match:
                raise ValueError('格式：测试日期 2025-10-24 20:30')
            tm.activate(match[1]+' '+(match[2] or '20:30'))
            changed=True
        elif text=='测试重置':
            tm.activate(tm.state()['as_of']);changed=True
        if text=='测试高光':
            result=trigger_highlights()
            body=None if result['status'] in ('accepted','sent') else '高光检查：'+result['status']+'；'+result.get('reason','发送尚未全部确认，已保留分项回执；不会自动重发。')
        else:
            body=tm.status_text()
    except (ValueError,KeyError) as exc:
        body='未切换：'+str(exc)+'\n可选日期：2025-10-13 至 2025-11-16。'
    if body:
        send(key,body)
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('INSERT OR IGNORE INTO test_callbacks VALUES(?,?)',(command['callback_id'],'control'))
        c.execute("UPDATE test_callbacks SET status='control' WHERE id=?",(command['callback_id'],))
        c.execute('INSERT OR IGNORE INTO test_inbound_ids VALUES(?)',(command['message_id'],))
    return {'changed':changed,'test_mode':tm.state()}


if __name__=='__main__':
    initialize()
    command=sys.argv[1]
    result=receive() if command=='receive' else flush(int(sys.argv[2]) if len(sys.argv)>2 else None) if command=='flush' else trigger_highlights() if command=='highlights' else control(json.loads(sys.argv[2]))
    print(s.encoded(result))
