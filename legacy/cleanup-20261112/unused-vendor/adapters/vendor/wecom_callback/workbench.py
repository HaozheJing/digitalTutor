"""Loopback-only prompt editor. Publish immutable candidates with an atomic pointer."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'candidates/service-wecom-v1'
STATE = ROOT/'var/wecom-autoreply'
EDIT = STATE/'workbench'
LOCK = threading.Lock()


def atomic(path, value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)


def active():
    p=EDIT/'active.json'
    return json.loads(p.read_text()) if p.exists() else {'version':'initial','candidate':str(BASE)}


def prompt(candidate):
    p=Path(candidate)
    cfg=json.loads((p/'agent/config/prompt-profile.json').read_text())
    raw=(p/'agent/prompts'/cfg['service_system_file']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=cfg['service_system_sha256']:
        raise ValueError('当前提示词校验失败')
    return raw.decode('utf8')


def publish(text, expected, extra=None):
    if not isinstance(text,str) or not text.strip() or len(text.encode())>240000:
        raise ValueError('提示词不能为空，且须小于240KB')
    current=active()
    if current['version']!=expected:
        raise ValueError('生效版本已变化，请刷新后再装配')
    digest=hashlib.sha256(text.encode()).hexdigest()
    version=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'-'+digest[:8]
    target=EDIT/'versions'/version
    target.mkdir(parents=True)
    # Only agent prompts differ; backend and provider settings are shared read-only.
    shutil.copytree(BASE/'agent',target/'agent')
    for name in ('backend','config','vendor'):
        (target/name).symlink_to((BASE/name).resolve(),target_is_directory=True)
    cfgpath=target/'agent/config/prompt-profile.json'
    cfg=json.loads(cfgpath.read_text())
    (target/'agent/prompts'/cfg['service_system_file']).write_text(text,encoding='utf8')
    cfg['service_system_sha256']=digest
    cfgpath.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf8')
    manifest=json.loads((BASE/'source-manifest.json').read_text())
    for p in (target/'agent').rglob('*'):
        if p.is_file():manifest[str(p.relative_to(target))]=hashlib.sha256(p.read_bytes()).hexdigest()
    (target/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
    assert prompt(target)==text
    value={'version':version,'candidate':str(target),'sha256':digest,
           'published_at':datetime.now(timezone.utc).isoformat(),**(extra or {})}
    atomic(EDIT/'active.json',value)
    atomic(EDIT/'draft.json',{'text':text,'base_version':version,**(extra or {})})
    return value


def state():
    import test_context
    session=test_context.current()
    value=active();text=prompt(value['candidate'])
    draft=EDIT/'draft.json'
    result={'active':value,'text':text,'draft':json.loads(draft.read_text()) if draft.exists() else {'text':text,'base_version':value['version']},'messages':[]}
    result['context']={'id':session['id'],'started_at':session['started_at'],'reset_pending':test_context.pending() is not None}
    for name in ('status','last-prompt'):
        p=STATE/(name+'.json')
        result[name]=json.loads(p.read_text()) if p.exists() else None
    if result['last-prompt'] and result['last-prompt'].get('context_id','initial')!=session['id']:result['last-prompt']=None
    dbpath=Path(session['path'])/'business.sqlite'
    if dbpath.exists():
        db=sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True)
        try:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone()
            rows=db.execute("SELECT data FROM records WHERE kind='message' ORDER BY rowid DESC LIMIT 20").fetchall() if exists else []
            result['messages']=[{k:v.get(k) for k in ('body','direction','occurred_at')} for v in [json.loads(r[0]) for r in reversed(rows)]]
        finally:db.close()
    if value.get('engine')=='replyer':
        import replyer
        result['messages']=replyer.messages()
        result['fixtures']=replyer.fixtures()
    return result


def replyer_input(data):
    from replyer import selected_fixture,system_text
    if 'tone' not in data:raise ValueError('编辑器已升级，请刷新工作台后分别编辑职责与语气')
    parts={k:data[k] for k in ('common','replyer','tone')}
    if any(not isinstance(v,str) or not v.strip() or len(v.encode())>90000 for v in parts.values()):
        raise ValueError('通用、职责、语气均不能为空，单段须小于90KB')
    fixture=selected_fixture(data['fixture_id'])
    text=system_text(parts)+'\n'
    if len(text.encode())>240000:raise ValueError('提示词总长度须小于240KB')
    return text,{'engine':'replyer','parts':parts,'fixture_id':fixture['id']}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def output(self,status,value,kind='application/json; charset=utf-8'):
        data=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(data)
    def allowed(self):
        return self.headers.get('Host') in {'127.0.0.1:9011','localhost:9011','127.0.0.1:18997','localhost:18997'}
    def do_GET(self):
        if not self.allowed():return self.output(403,{'error':'本机访问限定'})
        if self.path=='/':return self.output(200,Path(__file__).with_name('workbench.html').read_bytes(),'text/html; charset=utf-8')
        if self.path=='/api/state':
            with LOCK:return self.output(200,state())
        self.output(404,{'error':'not found'})
    def do_POST(self):
        if (not self.allowed() or self.headers.get('X-Workbench')!='1'
            or self.headers.get('Origin') not in {'http://'+self.headers.get('Host','')}
            or self.headers.get('Content-Type')!='application/json'):
            return self.output(403,{'error':'仅支持工作台同源操作'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<256000:raise ValueError('请求过大或为空')
            data=json.loads(self.rfile.read(size))
            with LOCK:
                if self.path=='/api/reset-context':
                    import test_context
                    cfg=json.loads((ROOT/'config/wecom-callback.json').read_text())
                    return self.output(200,test_context.request(cfg['database'],data.get('context_id')))
                if self.path=='/api/draft':
                    if 'common' in data:
                        text,extra=replyer_input(data)
                        if data.get('base_version')!=active()['version']:raise ValueError('版本已变化，请刷新')
                        atomic(EDIT/'draft.json',{'text':text,'base_version':data['base_version'],**extra})
                        return self.output(200,{'ok':True})
                    if not isinstance(data.get('text'),str):raise ValueError('缺少提示词')
                    if data.get('base_version')!=active()['version']:raise ValueError('版本已变化，请刷新')
                    atomic(EDIT/'draft.json',{'text':data['text'],'base_version':data['base_version']})
                    return self.output(200,{'ok':True})
                if self.path=='/api/assemble':
                    if 'common' in data:
                        text,extra=replyer_input(data)
                        return self.output(200,publish(text,data['base_version'],extra))
                    return self.output(200,publish(data['text'],data['base_version']))
                return self.output(404,{'error':'not found'})
        except (ValueError,KeyError,TypeError) as exc:self.output(400,{'error':str(exc)})
        except Exception:self.output(500,{'error':'装配失败，原生效版本保留，请检查服务日志'})


if __name__=='__main__':
    os.umask(0o077);EDIT.mkdir(parents=True,exist_ok=True)
    ThreadingHTTPServer(('127.0.0.1',9011),Handler).serve_forever()
