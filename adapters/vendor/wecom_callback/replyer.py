"""Text-only reply sandbox: synthetic Service packets, real allowlisted chat transport."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[2]


def fixtures():
    return json.loads(Path(__file__).with_name('replyer-fixtures.json').read_text(encoding='utf8'))


def selected_fixture(name):
    for fixture in fixtures():
        if fixture['id']==name:return fixture
    raise ValueError('未知模拟场景')


def enabled():
    from workbench import active
    return active().get('engine')=='replyer'


def connect():
    from test_context import current
    path=Path(current()['path'])/'replyer.sqlite'
    db=sqlite3.connect(path,timeout=5)
    db.row_factory=sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY, callback_id INTEGER, recipient TEXT, body TEXT, at TEXT,
        status TEXT, reply TEXT, operation TEXT, request TEXT, version TEXT, fixture TEXT, error TEXT)''')
    db.commit()
    return db


def channel():
    from test_context import current
    from digital_tutor.wecom_transport import WecomChannel
    return WecomChannel(Path(current()['path'])/'replyer.channel.sqlite')


def receive(payload,route,rid):
    from digital_tutor.wecom_transport import family_id
    identity=json.dumps([route,payload['messageId']],sort_keys=True)
    key='replyer-'+hashlib.sha256(identity.encode()).hexdigest()
    at=datetime.fromtimestamp(float(payload['timestamp'])/1000,timezone.utc).isoformat()
    with closing(connect()) as db,db:
        db.execute('INSERT OR IGNORE INTO turns(id,callback_id,recipient,body,at,status) VALUES(?,?,?,?,?,?)',
            (key,rid,'wecom:'+family_id(route),payload['payload'].get('pureText') or payload['payload']['text'],at,'queued'))


def has_work():
    with closing(connect()) as db:
        return db.execute("SELECT 1 FROM turns WHERE status IN ('queued','prepared') LIMIT 1").fetchone() is not None


def observe_receipt(payload):
    c=channel()
    try:
        matched=c.observe_receipt(payload)
        reconcile(c)
        return matched
    finally:c.close()


def reconcile(c):
    with closing(connect()) as db,db:
        for row in db.execute("SELECT id FROM turns WHERE status IN ('prepared','accepted','unknown')").fetchall():
            stage=c.query(row['id'])
            if stage in ('accepted','sent','unknown','failed'):
                db.execute('UPDATE turns SET status=? WHERE id=?',(stage,row['id']))


def system_text(parts):
    # Legacy versions without a separate tone part remain readable for rollback.
    return '\n\n'.join(parts[k].strip() for k in ('common','replyer','tone') if parts.get(k))


def compose_request(parts,fixture,history,body):
    messages=[{'role':'system','content':system_text(parts)}]
    # Free chat has no Service packet at all, not even an empty/synthetic placeholder.
    if fixture['id']!='free_chat':
        messages.append({'role':'user','content':'【测试宿主提供的模拟 Service 输出；不是实际查询或执行结果】\n'+json.dumps(fixture,ensure_ascii=False)})
    return [*messages,*history,{'role':'user','content':body}]


def generate(messages):
    from digital_tutor.provider_routes import resolve_connection
    from media import http_json
    cfg=resolve_connection(ROOT/'candidates/service-wecom-v1')
    result=http_json(cfg['base_url'].rstrip('/')+'/chat/completions',{
        'model':cfg['model'],'messages':messages,'stream':False,'max_tokens':1200,
        **cfg.get('generation_options',{}),
        **({'thinking':{'type':'disabled'}} if cfg['name']=='arkplan' else {})},
        {'Authorization':'Bearer '+cfg['api_key']})
    choice=result['choices'][0]
    text=choice['message'].get('content')
    if choice.get('finish_reason') not in (None,'stop') or not isinstance(text,str) or not text.strip() or len(text)>5000:
        raise ValueError('INVALID_REPLY')
    return text.strip()


def run_once():
    from workbench import active,atomic,STATE
    from test_context import current
    chosen=active();session=current()
    with closing(connect()) as db:
        row=db.execute("SELECT * FROM turns WHERE status IN ('queued','prepared') ORDER BY callback_id LIMIT 1").fetchone()
        if not row:return
        if row['status']=='queued':
            history=[]
            previous=db.execute('SELECT * FROM turns WHERE recipient=? AND callback_id<? ORDER BY callback_id DESC LIMIT 20',
                (row['recipient'],row['callback_id'])).fetchall()
            for old in reversed(previous):
                history.append({'role':'user','content':old['body']})
                if old['status']=='sent' and old['reply']:
                    history.append({'role':'assistant','content':old['reply']})
            fixture=selected_fixture(chosen['fixture_id'])
            request=compose_request(chosen['parts'],fixture,history,row['body'])
            atomic(STATE/'last-prompt.json',{'version':chosen['version'],'context_id':session['id'],
                'engine':'replyer','fixture_id':fixture['id'],'at':datetime.now(timezone.utc).isoformat()})
            with db:db.execute('UPDATE turns SET request=?,version=?,fixture=? WHERE id=?',
                (json.dumps(request,ensure_ascii=False),chosen['version'],fixture['id'],row['id']))
            try:text=generate(request)
            except Exception as exc:
                with db:db.execute("UPDATE turns SET status='failed',error=? WHERE id=?",(type(exc).__name__,row['id']))
                return
            operation={'id':row['id'],'recipient':row['recipient'],'body':text,'action_kind':'reply',
                'reply_to':[{'message_id':row['id']}]}
            with db:db.execute("UPDATE turns SET status='prepared',reply=?,operation=? WHERE id=?",
                (text,json.dumps(operation),row['id']))
        else:operation=json.loads(row['operation'])
        # This exact operation is persisted before dispatch. Restarts reuse its identity and payload.
        c=channel()
        try:
            stage=c.send(operation)
            with db:db.execute('UPDATE turns SET status=? WHERE id=?',(stage,row['id']))
            reconcile(c)
        finally:c.close()


def messages():
    with closing(connect()) as db:
        rows=db.execute('SELECT * FROM turns ORDER BY callback_id DESC LIMIT 20').fetchall()
    result=[]
    for row in reversed(rows):
        result.append({'direction':'inbound','body':row['body'],'occurred_at':row['at'],'status':row['status']})
        if row['reply']:
            result.append({'direction':'outbound','body':row['reply'],'occurred_at':row['at'],'status':row['status']})
    return result
