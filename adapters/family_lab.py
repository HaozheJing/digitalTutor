"""Durable five-family control plane. Host-only: never exposed as model tools.

SQLite transactions are the linearization point for control and submission.
No network request runs under a transaction. Submitted/unknown is never retried.
"""
import contextlib
import datetime as dt
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
TERMINAL = ('sent', 'failed', 'cancelled', 'expired')


def encode(v): return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
def ident(): return uuid.uuid4().hex
def stamp(v):
    d = dt.datetime.fromisoformat(v.replace('Z', '+00:00'))
    if d.tzinfo is None: raise ValueError('timezone_required')
    return d.astimezone(TZ).isoformat(timespec='seconds')


def night_passive_binding(c, run, family):
    return c.execute("""SELECT 1 FROM bindings b JOIN settings s
        ON s.key='night_passive:'||b.run_id||':'||b.family_id
        AND s.value=json_quote(b.id||':'||b.version)
        WHERE b.run_id=? AND b.family_id=? AND b.enabled=1 AND b.mode='wechat'""",
        (run, family)).fetchone() is not None


def service_open(value):
    return '06:30' <= stamp(value)[11:16] < '22:00'


class ModelSlot:
    """Per physical API request, including native compaction and voice calls."""
    def __init__(self,workspace,model,kind=None):
        self.lab=None;self.request=None;self.model=model;self.usage=None;self.error=None
        path=Path(workspace)/'config/family-lab.json' if workspace else None
        if not path or not path.exists():return
        cfg=json.loads(path.read_text());self.lab=Lab(cfg['control_db']);self.cfg=cfg
        f=self.lab.read('SELECT * FROM families WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id']))[0]
        g=self.lab.read('SELECT * FROM generations WHERE token=?',(f['turn_token'],))
        self.token=f['turn_token'];self.owner=g[0]['owner_id'] if g else 'native-maintenance'
        self.kind=kind or ('interactive' if g and g[0]['owner_type']=='event' else 'background' if g else 'maintenance')

    def __enter__(self):
        if not self.lab:return self
        self.request=self.lab.model_enqueue(self.cfg['run_id'],self.cfg['family_id'],self.owner,self.kind,self.model)
        deadline=time.monotonic()+180
        try:
            while time.monotonic()<deadline:
                with self.lab.transaction() as c:
                    r,f=self.lab.scope(c,self.cfg['run_id'],self.cfg['family_id'])
                    if r['paused'] or f['held'] or (f['paused'] and self.kind not in ('interactive','voice')):raise ValueError('model_request_controlled')
                    if self.token:self.lab.validate(c,self.token)
                if self.lab.model_claim(self.request):return self
                time.sleep(.1)
            raise TimeoutError('model_queue_deadline')
        except BaseException as exc:
            self.lab.model_finish(self.request,error=type(exc).__name__+':before_provider');raise

    def __exit__(self,typ,value,tb):
        if self.lab and self.request:
            self.lab.model_finish(self.request,self.usage,self.error or (typ.__name__ if typ else None))


class Lab:
    def __init__(self, path): self.path = str(Path(path).resolve())

    @contextlib.contextmanager
    def transaction(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.execute('PRAGMA foreign_keys=ON')
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback(); raise
        finally: c.close()

    def read(self, sql, args=()):
        with contextlib.closing(sqlite3.connect('file:'+self.path+'?mode=ro', uri=True)) as c:
            c.row_factory = sqlite3.Row
            return [dict(x) for x in c.execute(sql, args)]

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(self.path)) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,mode TEXT NOT NULL,as_of TEXT NOT NULL,
              epoch INTEGER NOT NULL DEFAULT 1,paused INTEGER NOT NULL DEFAULT 1,clock_mode TEXT NOT NULL DEFAULT 'frozen',
              anchor_wall REAL,anchor_business TEXT,created REAL NOT NULL,active INTEGER NOT NULL DEFAULT 1);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(active) WHERE active=1;
            CREATE TABLE IF NOT EXISTS families(run_id TEXT,family_id TEXT,student TEXT,workspace TEXT,
              epoch INTEGER NOT NULL DEFAULT 1,inbox_seq INTEGER NOT NULL DEFAULT 0,projected_seq INTEGER NOT NULL DEFAULT 0,
              held INTEGER NOT NULL DEFAULT 0,paused INTEGER NOT NULL DEFAULT 0,busy INTEGER NOT NULL DEFAULT 0,
              turn_token TEXT,heartbeat REAL,applied_clock INTEGER NOT NULL DEFAULT 0,last_model REAL DEFAULT 0,
              last_send REAL DEFAULT 0,PRIMARY KEY(run_id,family_id));
            CREATE TABLE IF NOT EXISTS bindings(id TEXT PRIMARY KEY,run_id TEXT,family_id TEXT,mode TEXT,
              nickname TEXT,route TEXT,enabled INTEGER DEFAULT 0,version INTEGER DEFAULT 1,
              UNIQUE(run_id,family_id));
            CREATE UNIQUE INDEX IF NOT EXISTS active_contact ON bindings(route) WHERE enabled=1 AND mode='wechat';
            CREATE TABLE IF NOT EXISTS ingress(id TEXT PRIMARY KEY,run_id TEXT,family_id TEXT,seq INTEGER,
              kind TEXT,body TEXT,occurred_at TEXT,business_at TEXT,received REAL,projected INTEGER DEFAULT 0,
              UNIQUE(run_id,family_id,seq));
            CREATE TABLE IF NOT EXISTS generations(token TEXT PRIMARY KEY,run_id TEXT,family_id TEXT,
              family_epoch INTEGER,clock_epoch INTEGER,owner_type TEXT,owner_id TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS model_attempts(id TEXT PRIMARY KEY,run_id TEXT,family_id TEXT,owner_id TEXT,
              kind TEXT,status TEXT,created REAL,started REAL,finished REAL,lease_until REAL,model TEXT,
              usage TEXT,error TEXT,price_version TEXT,cost REAL);
            CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,run_id TEXT,family_id TEXT,group_key TEXT,
              ordinal INTEGER,body TEXT,message_type INTEGER,kind TEXT,generation TEXT,family_epoch INTEGER,
              clock_epoch INTEGER,binding_id TEXT,binding_version INTEGER,route TEXT,mode TEXT,status TEXT,
              request_id TEXT,provider_message_id TEXT,created REAL,submitted REAL,updated REAL,error TEXT,
              business_at TEXT,expires_at TEXT,semantic_key TEXT,UNIQUE(run_id,family_id,group_key,ordinal));
            CREATE INDEX IF NOT EXISTS send_queue ON outbox(status,created);
            CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,operation_id TEXT,body TEXT,received REAL);
            CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,run_id TEXT,family_id TEXT,problem_key TEXT,
              priority INTEGER,status TEXT,first_wait REAL,updated REAL,evidence TEXT,next_step TEXT,
              UNIQUE(run_id,family_id,problem_key));
            CREATE TABLE IF NOT EXISTS drafts(case_id TEXT PRIMARY KEY,ai_body TEXT,edited_body TEXT,
              revision INTEGER,family_epoch INTEGER,updated REAL);
            CREATE TABLE IF NOT EXISTS constraints_scope(run_id TEXT,family_id TEXT,service TEXT,until_at TEXT,
              reason TEXT,PRIMARY KEY(run_id,family_id,service));
            CREATE TABLE IF NOT EXISTS transitions(id TEXT PRIMARY KEY,run_id TEXT,source TEXT,target TEXT,
              epoch INTEGER,status TEXT,watermark TEXT,created REAL,finished REAL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL,run_id TEXT,family_id TEXT,kind TEXT,body TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            ''')

    def audit(self, c, kind, body, run=None, family=None):
        c.execute('INSERT INTO audit(at,run_id,family_id,kind,body) VALUES(?,?,?,?,?)',
                  (time.time(),run,family,kind,encode(body)))

    def current(self):
        rows = self.read('SELECT * FROM runs WHERE active=1')
        if not rows: raise ValueError('no_active_run')
        return rows[0]

    def create_run(self, families, as_of, mode='simulation'):
        as_of = stamp(as_of)
        if not '2025-10-13' <= as_of[:10] <= '2025-11-09': raise ValueError('outside_twenty_lesson_month')
        if mode not in ('simulation','wechat'): raise ValueError('invalid_mode')
        if len(families)!=5 or len({x['family_id'] for x in families})!=5: raise ValueError('five_distinct_families_required')
        run = ident()
        with self.transaction() as c:
            if c.execute('SELECT 1 FROM runs WHERE active=1').fetchone(): raise ValueError('archive_current_run_first')
            c.execute('INSERT INTO runs(id,mode,as_of,created) VALUES(?,?,?,?)',(run,mode,as_of,time.time()))
            for f in families:
                c.execute('INSERT INTO families(run_id,family_id,student,workspace) VALUES(?,?,?,?)',
                          (run,f['family_id'],f['student'],f['workspace']))
                c.execute('INSERT INTO bindings(id,run_id,family_id,mode,nickname,route,enabled) VALUES(?,?,?,?,?,?,1)',
                          (ident(),run,f['family_id'],'simulation',f['student'],'simulation:'+f['family_id']))
            self.audit(c,'run_created',{'mode':mode,'as_of':as_of},run)
        return run

    def scope(self,c,run,family):
        r=c.execute('SELECT * FROM runs WHERE id=? AND active=1',(run,)).fetchone()
        f=c.execute('SELECT * FROM families WHERE run_id=? AND family_id=?',(run,family)).fetchone()
        if not r or not f: raise ValueError('inactive_or_foreign_scope')
        return dict(r),dict(f)

    def bind(self,run,family,nickname,route):
        if set(route)!={'orgId','imBotId','imContactId','chatId'} or not all(route.values()): raise ValueError('complete_verified_route_required')
        with self.transaction() as c:
            registered=c.execute("SELECT value FROM settings WHERE key='contact_authorized_routes'").fetchone()
            if not registered or route not in json.loads(registered[0]):raise ValueError('contact_not_authorized')
            r,f=self.scope(c,run,family)
            if r['mode']!='wechat' or not r['paused'] or f['busy']: raise ValueError('binding_requires_paused_wechat_run')
            if c.execute("SELECT 1 FROM outbox WHERE run_id=? AND family_id=? AND status NOT IN ('sent','failed','cancelled','expired')",(run,family)).fetchone(): raise ValueError('binding_has_outstanding_delivery')
            c.execute("UPDATE bindings SET nickname=?,route=?,mode='wechat',version=version+1 WHERE run_id=? AND family_id=?",(nickname,encode(route),run,family))
            c.execute('UPDATE families SET epoch=epoch+1 WHERE run_id=? AND family_id=?',(run,family))
            self.audit(c,'binding_changed',{'nickname':nickname,'route_sha256':hashlib.sha256(encode(route).encode()).hexdigest()},run,family)

    def ingest(self,run,family,event_id,kind,body,occurred_at=None):
        if kind not in ('message','completion','highlight','answer','note','pause'): raise ValueError('invalid_event_kind')
        with self.transaction() as c:
            r,f=self.scope(c,run,family)
            occurred=stamp(occurred_at or r['as_of'])
            if kind not in ('message','note') and occurred>r['as_of']: raise ValueError('future_event_not_yet_visible')
            key=run+':'+family+':'+event_id
            old=c.execute('SELECT * FROM ingress WHERE id=?',(key,)).fetchone()
            if old:
                if old['body']!=encode(body) or old['kind']!=kind: raise ValueError('immutable_event_changed')
                return dict(old)
            seq=f['inbox_seq']+1
            c.execute('INSERT INTO ingress VALUES(?,?,?,?,?,?,?,?,?,0)',(key,run,family,seq,kind,encode(body),occurred,r['as_of'],time.time()))
            c.execute('UPDATE families SET inbox_seq=?,epoch=epoch+1 WHERE run_id=? AND family_id=?',(seq,run,family))
            self.audit(c,'ingress',{'id':key,'kind':kind,'seq':seq},run,family)
            if kind=='message':
                c.execute("UPDATE cases SET updated=?,evidence=json_insert(evidence,'$[#]',?) WHERE run_id=? AND family_id=? AND status='open'",(time.time(),key,run,family))
                if f['held'] and not c.execute("SELECT 1 FROM cases WHERE run_id=? AND family_id=? AND status='open'",(run,family)).fetchone():
                    now=time.time();case_id=ident()
                    c.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?,?)',(case_id,run,family,'held_inbound:'+key,70,'open',now,now,encode([key]),'接管期间收到新消息，请老师继续处理；结束事项不会自动恢复 AI。'))
                    self.audit(c,'held_inbound_case',{'case_id':case_id,'message_id':key},run,family)
            return dict(c.execute('SELECT * FROM ingress WHERE id=?',(key,)).fetchone())

    def acknowledge(self,run,family,seq,clock_epoch):
        with self.transaction() as c:
            r,f=self.scope(c,run,family)
            if seq>f['inbox_seq'] or clock_epoch>r['epoch']: raise ValueError('invalid_projection_ack')
            c.execute('UPDATE families SET projected_seq=MAX(projected_seq,?),applied_clock=?,heartbeat=? WHERE run_id=? AND family_id=?',(seq,clock_epoch,time.time(),run,family))
            c.execute('UPDATE ingress SET projected=1 WHERE run_id=? AND family_id=? AND seq<=?',(run,family,seq))

    def begin(self,run,family,token,owner_type,owner_id):
        with self.transaction() as c:
            r,f=self.scope(c,run,family)
            if r['paused'] or f['held'] or (f['paused'] and owner_type!='event'): raise ValueError('service_controlled')
            if f['applied_clock']!=r['epoch'] or f['projected_seq']!=f['inbox_seq']: raise ValueError('projection_not_current')
            if f['busy']: raise ValueError('family_turn_already_active')
            c.execute('INSERT INTO generations VALUES(?,?,?,?,?,?,?,?)',(token,run,family,f['epoch'],r['epoch'],owner_type,owner_id,time.time()))
            c.execute('UPDATE families SET busy=1,turn_token=?,heartbeat=? WHERE run_id=? AND family_id=?',(token,time.time(),run,family))
            return {'token':token,'family_epoch':f['epoch'],'clock_epoch':r['epoch']}

    def finish_turn(self,run,family,token=None):
        with self.transaction() as c:
            self.scope(c,run,family)
            c.execute('UPDATE families SET busy=0,turn_token=NULL,heartbeat=? WHERE run_id=? AND family_id=? AND (? IS NULL OR turn_token=?)',(time.time(),run,family,token,token))

    def validate(self,c,token):
        g=c.execute('SELECT * FROM generations WHERE token=?',(token,)).fetchone()
        if not g: raise ValueError('host_generation_required')
        r,f=self.scope(c,g['run_id'],g['family_id'])
        if r['paused'] or f['held'] or (f['paused'] and g['owner_type']!='event') or f['epoch']!=g['family_epoch'] or r['epoch']!=g['clock_epoch']:
            raise ValueError('generation_revoked')
        if f['projected_seq']!=f['inbox_seq']: raise ValueError('new_unprojected_input')
        return dict(g),r,f

    def model_enqueue(self,run,family,owner,kind,model):
        if kind not in ('interactive','background','maintenance','voice'): raise ValueError('invalid_model_kind')
        request=ident()
        with self.transaction() as c:
            self.scope(c,run,family)
            c.execute('INSERT INTO model_attempts(id,run_id,family_id,owner_id,kind,status,created,model) VALUES(?,?,?,?,?,?,?,?)',(request,run,family,owner,kind,'queued',time.time(),model))
        return request

    def model_claim(self,request,limit=3,background_limit=2,now=None):
        now=time.time() if now is None else now
        with self.transaction() as c:
            row=c.execute('SELECT * FROM model_attempts WHERE id=?',(request,)).fetchone()
            if not row or row['status']!='queued': return False
            r,f=self.scope(c,row['run_id'],row['family_id'])
            if r['paused'] or f['held'] or (f['paused'] and row['kind'] not in ('interactive','voice')): return False
            active=c.execute("SELECT * FROM model_attempts WHERE status='running'").fetchall()
            if len(active)>=limit or any(x['run_id']==row['run_id'] and x['family_id']==row['family_id'] for x in active): return False
            if row['kind'] in ('background','maintenance') and sum(x['kind'] in ('background','maintenance') for x in active)>=background_limit:return False
            if row['kind']=='maintenance' and any(x['kind']=='maintenance' for x in active):return False
            eligible=[]
            for x in c.execute("SELECT a.*,f.last_model FROM model_attempts a JOIN families f ON f.run_id=a.run_id AND f.family_id=a.family_id JOIN runs r ON r.id=a.run_id WHERE a.status='queued' AND r.active=1 AND r.paused=0 AND f.held=0 AND (f.paused=0 OR a.kind IN ('interactive','voice'))"):
                if any(y['run_id']==x['run_id'] and y['family_id']==x['family_id'] for y in active):continue
                if x['kind'] in ('background','maintenance') and sum(y['kind'] in ('background','maintenance') for y in active)>=background_limit:continue
                if x['kind']=='maintenance' and any(y['kind']=='maintenance' for y in active):continue
                eligible.append(x)
            # Aging improves background priority, but never consumes the interactive reserve.
            eligible.sort(key=lambda x:(0 if x['kind'] in ('interactive','voice') or now-x['created']>90 else 1,x['last_model'],x['created']))
            if not eligible or eligible[0]['id']!=request:return False
            c.execute("UPDATE model_attempts SET status='running',started=?,lease_until=? WHERE id=?",(now,now+210,request))
            c.execute('UPDATE families SET last_model=? WHERE run_id=? AND family_id=?',(now,row['run_id'],row['family_id']))
            return True

    def model_finish(self,request,usage=None,error=None,cost=None,price_version=None):
        with self.transaction() as c:
            now=time.time()
            row=c.execute('SELECT * FROM model_attempts WHERE id=?',(request,)).fetchone()
            if not row or row['status'] in ('finished','failed'):return
            from family_lab_cost import estimate
            if cost is None:cost,price_version=estimate(row['model'],usage,now)
            c.execute("UPDATE model_attempts SET status=?,finished=?,usage=?,error=?,cost=?,price_version=? WHERE id=?",('failed' if error else 'finished',time.time(),encode(usage) if usage is not None else None,error,cost,price_version,request))

    def enqueue(self,run,family,operation,group,ordinal,body,message_type,token=None,kind='ai',expires_at=None,semantic_key=None):
        if message_type not in (1,5,6,7) or kind not in ('ai','host','human'): raise ValueError('invalid_delivery')
        with self.transaction() as c:
            r,f=self.scope(c,run,family)
            old=c.execute('SELECT * FROM outbox WHERE id=?',(operation,)).fetchone()
            if old:
                if (old['run_id'],old['family_id'],old['body'],old['message_type'],old['group_key'],old['ordinal'])!=(run,family,body,message_type,group,ordinal): raise ValueError('immutable_operation_changed')
                return dict(old)
            if kind=='ai':self.validate(c,token)
            elif kind=='host' and (r['paused'] or f['held'] or f['paused']):raise ValueError('host_send_blocked')
            b=c.execute('SELECT * FROM bindings WHERE run_id=? AND family_id=? AND enabled=1',(run,family)).fetchone()
            if not b: raise ValueError('unbound_recipient')
            if semantic_key and b['mode']=='wechat' and c.execute("SELECT 1 FROM outbox WHERE route=? AND semantic_key=? AND status IN ('submitting','accepted','sent','unknown')",(b['route'],semantic_key)).fetchone():raise ValueError('cross_run_delivery_already_reserved')
            now=time.time()
            c.execute('''INSERT INTO outbox(id,run_id,family_id,group_key,ordinal,body,message_type,kind,generation,
              family_epoch,clock_epoch,binding_id,binding_version,route,mode,status,created,updated,business_at,expires_at,semantic_key)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?)''',
              (operation,run,family,group,ordinal,body,message_type,kind,token,f['epoch'],r['epoch'],b['id'],b['version'],b['route'],b['mode'],now,now,r['as_of'],expires_at,semantic_key))
            self.audit(c,'delivery_queued',{'operation_id':operation,'kind':kind,'message_type':message_type},run,family)
            return dict(c.execute('SELECT * FROM outbox WHERE id=?',(operation,)).fetchone())

    def claim_send(self,now=None,wall_at=None,min_interval=2):
        now=time.time() if now is None else now
        wall_at=wall_at or dt.datetime.fromtimestamp(now,TZ).isoformat()
        with self.transaction() as c:
            # One actual HTTP submit per bot. Accepted releases this slot immediately.
            if c.execute("SELECT 1 FROM outbox WHERE status='submitting'").fetchone():return None
            last=c.execute('SELECT MAX(submitted) FROM outbox').fetchone()[0]
            if last is not None and now-last<min_interval:return None
            rows=c.execute("SELECT o.*,f.last_send FROM outbox o JOIN families f ON f.run_id=o.run_id AND f.family_id=o.family_id WHERE o.status='queued' ORDER BY CASE o.kind WHEN 'human' THEN 0 ELSE 1 END,f.last_send,o.created,o.ordinal").fetchall()
            for row in rows:
                r,f=self.scope(c,row['run_id'],row['family_id'])
                if r['paused']:continue
                b=c.execute('SELECT * FROM bindings WHERE id=?',(row['binding_id'],)).fetchone()
                reason=None
                if not b or not b['enabled'] or b['version']!=row['binding_version']:reason='binding_changed'
                generation=c.execute('SELECT owner_type,owner_id FROM generations WHERE token=?',(row['generation'],)).fetchone()
                interactive=bool(generation and generation[0]=='event')
                if row['kind']!='human' and (f['held'] or (f['paused'] and not interactive)):reason='family_controlled'
                if row['kind']!='human' and (row['family_epoch']!=f['epoch'] or row['clock_epoch']!=r['epoch']):reason='facts_or_control_changed'
                if row['expires_at'] and r['as_of']>row['expires_at']:reason='expired'
                if row['kind']!='human' and row['business_at'][:10]!=r['as_of'][:10]:reason='prior_day'
                night_reply = (row['kind']=='ai' and interactive and row['mode']=='wechat'
                               and generation[1].startswith(('message-batch:', 'inbound:')) and row['group_key']=='reply:'+generation[1]
                               and night_passive_binding(c,row['run_id'],row['family_id']))
                if not night_reply and row['kind']!='human' and row['mode']=='wechat' and not service_open(wall_at):reason='real_night_no_late_send'
                if reason:
                    c.execute('UPDATE outbox SET status=?,error=?,updated=? WHERE id=?',('expired' if reason in ('expired','prior_day') else 'cancelled',reason,now,row['id']));continue
                if not night_reply and row['kind']!='human' and (not service_open(r['as_of']) or (row['mode']=='wechat' and not service_open(wall_at))):continue
                if c.execute("SELECT 1 FROM outbox WHERE run_id=? AND family_id=? AND group_key=? AND ordinal<? AND status IN ('failed','cancelled','expired')",(row['run_id'],row['family_id'],row['group_key'],row['ordinal'])).fetchone():
                    c.execute("UPDATE outbox SET status='cancelled',error='previous_part_not_delivered',updated=? WHERE id=?",(now,row['id']));continue
                if c.execute("SELECT 1 FROM outbox WHERE run_id=? AND family_id=? AND id<>? AND ((group_key=? AND ordinal<? AND status<>'sent') OR (group_key<>? AND created<? AND status NOT IN ('sent','failed','cancelled','expired')))",(row['run_id'],row['family_id'],row['id'],row['group_key'],row['ordinal'],row['group_key'],row['created'])).fetchone():continue
                if row['mode']=='wechat' and row['kind']=='ai' and row['message_type']==7 and row['ordinal']>0:
                    previous=c.execute(
                        "SELECT updated FROM outbox WHERE run_id=? AND family_id=? AND group_key=? "
                        "AND ordinal<? AND status='sent' ORDER BY ordinal DESC LIMIT 1",
                        (row['run_id'],row['family_id'],row['group_key'],row['ordinal'])
                    ).fetchone()
                    if previous:
                        characters=sum(not ch.isspace() for ch in row['body'])
                        if now < previous[0] + characters * 0.5:
                            continue
                c.execute("UPDATE outbox SET status='submitting',submitted=?,updated=? WHERE id=?",(now,now,row['id']))
                c.execute('UPDATE families SET last_send=? WHERE run_id=? AND family_id=?',(now,row['run_id'],row['family_id']))
                self.audit(c,'submission_committed',{'operation_id':row['id']},row['run_id'],row['family_id'])
                return dict(c.execute('SELECT * FROM outbox WHERE id=?',(row['id'],)).fetchone())
        return None

    def receipt_timeouts(self,now=None):
        now=time.time() if now is None else now
        with self.transaction() as c:
            pending=c.execute("SELECT * FROM outbox WHERE status='accepted' AND updated<?",(now-120,)).fetchall()
            for row in pending:
                c.execute("UPDATE outbox SET status='unknown',error='receipt_timeout_no_retry',updated=? WHERE id=?",(now,row['id']))
                self.audit(c,'receipt_timeout_unknown',{'operation_id':row['id']},row['run_id'],row['family_id'])
        for row in pending:
            if self.current()['id']==row['run_id']:self.case(row['run_id'],row['family_id'],'delivery:'+row['group_key'],[row['id']],'发送结果未知：核对平台回执，禁止直接重发；其他家庭可继续互动。',90)
        return len(pending)

    def transport_result(self,operation,status,request_id=None,error=None):
        if status not in ('accepted','failed','unknown'):raise ValueError('transport_cannot_prove_sent')
        with self.transaction() as c:
            old=c.execute('SELECT * FROM outbox WHERE id=?',(operation,)).fetchone()
            if not old:raise ValueError('unknown_operation')
            if old['status'] in ('sent','failed'):return dict(old)
            c.execute('UPDATE outbox SET status=?,request_id=COALESCE(request_id,?),error=?,updated=? WHERE id=?',(status,request_id,error,time.time(),operation))
            return dict(c.execute('SELECT * FROM outbox WHERE id=?',(operation,)).fetchone())

    def receipt(self,event_id,operation,status,route,request_id=None,message_id=None,raw=None):
        if status not in ('sent','failed'):raise ValueError('invalid_receipt')
        with self.transaction() as c:
            row=c.execute('SELECT * FROM outbox WHERE id=?',(operation,)).fetchone()
            if not row or row['route']!=route:return False
            if row['request_id'] and request_id and row['request_id']!=request_id:raise ValueError('receipt_request_conflict')
            if status=='sent' and not message_id:raise ValueError('receipt_message_id_required')
            body=encode(raw or {'status':status,'request_id':request_id,'message_id':message_id})
            old=c.execute('SELECT * FROM receipts WHERE id=?',(event_id,)).fetchone()
            if old:
                if old['operation_id']!=operation or old['body']!=body:raise ValueError('receipt_event_conflict')
                return True
            c.execute('INSERT INTO receipts VALUES(?,?,?,?)',(event_id,operation,body,time.time()))
            if row['status']=='sent' and status!='sent':
                self.audit(c,'receipt_conflict',{'operation_id':operation,'incoming':status},row['run_id'],row['family_id']);return True
            c.execute('UPDATE outbox SET status=?,request_id=COALESCE(request_id,?),provider_message_id=COALESCE(provider_message_id,?),updated=? WHERE id=?',(status,request_id,message_id,time.time(),operation))
            return True

    def control(self,run,family,action,value=True,reason='teacher'):
        if action not in ('takeover','service_pause','experiment_pause'):raise ValueError('invalid_control')
        with self.transaction() as c:
            r,f=self.scope(c,run,family)
            if action=='experiment_pause':
                c.execute("UPDATE runs SET paused=?,epoch=epoch+1,clock_mode='frozen' WHERE id=?",(int(value),run))
                c.execute('UPDATE families SET epoch=epoch+1 WHERE run_id=?',(run,))
            else:
                col='held' if action=='takeover' else 'paused'
                c.execute('UPDATE families SET '+col+'=?,epoch=epoch+1 WHERE run_id=? AND family_id=?',(int(value),run,family))
            self.audit(c,action,{'value':bool(value),'reason':reason},run,family)

    def case(self,run,family,problem,evidence,next_step='',priority=50):
        with self.transaction() as c:
            self.scope(c,run,family)
            row=c.execute('SELECT * FROM cases WHERE run_id=? AND family_id=? AND problem_key=?',(run,family,problem)).fetchone()
            if row and row['status']=='closed':
                if set(evidence).issubset(set(json.loads(row['evidence']))):return row['id']
                c.execute('UPDATE cases SET problem_key=? WHERE id=?',(problem+':closed:'+row['id'],row['id']))
                row=None
            if row:
                refs=list(dict.fromkeys(json.loads(row['evidence'])+evidence))
                c.execute("UPDATE cases SET evidence=?,updated=?,next_step=?,priority=MAX(priority,?),status='open' WHERE id=?",(encode(refs),time.time(),next_step,priority,row['id']));return row['id']
            key=ident();now=time.time()
            c.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?,?)',(key,run,family,problem,priority,'open',now,now,encode(evidence),next_step))
            return key

    def save_draft(self,case_id,body,revision,ai=False):
        with self.transaction() as c:
            case=c.execute('SELECT * FROM cases WHERE id=?',(case_id,)).fetchone()
            if not case:raise ValueError('case_missing')
            r,f=self.scope(c,case['run_id'],case['family_id'])
            old=c.execute('SELECT * FROM drafts WHERE case_id=?',(case_id,)).fetchone()
            if ai and old and old['edited_body'] is not None:return dict(old)
            if old and old['revision']!=revision and not ai:raise ValueError('draft_revision_conflict')
            c.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET ai_body=excluded.ai_body,edited_body=excluded.edited_body,revision=excluded.revision,family_epoch=excluded.family_epoch,updated=excluded.updated',
                      (case_id,body if ai else old['ai_body'] if old else '',None if ai else body,(old['revision'] if old else 0)+1,f['epoch'],time.time()))
            return dict(c.execute('SELECT * FROM drafts WHERE case_id=?',(case_id,)).fetchone())

    def close_case(self,case_id,expected_seq=None):
        with self.transaction() as c:
            row=c.execute('SELECT * FROM cases WHERE id=?',(case_id,)).fetchone()
            if not row:raise ValueError('case_missing')
            r,f=self.scope(c,row['run_id'],row['family_id'])
            if row['status']=='closed':return
            if expected_seq is not None and expected_seq!=f['inbox_seq']:
                raise ValueError('收到新消息，请核对后再结束事项')
            c.execute("UPDATE cases SET status='closed',updated=? WHERE id=?",(time.time(),case_id))
            handled_seq=None
            if not c.execute("SELECT 1 FROM cases WHERE run_id=? AND family_id=? AND status='open'",(row['run_id'],row['family_id'])).fetchone():
                handled_seq=f['inbox_seq']
                key='human_handled:'+row['run_id']+':'+row['family_id']
                c.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(handled_seq)))
                c.execute('UPDATE families SET epoch=epoch+1 WHERE run_id=? AND family_id=?',(row['run_id'],row['family_id']))
            self.audit(c,'case_closed_ai_unchanged',{'case_id':case_id,'human_handled_through_seq':handled_seq},row['run_id'],row['family_id'])

    def advance(self,run,target,auto_execute=False,current_event=False):
        target=stamp(target)
        previous=self.read("SELECT id FROM transitions WHERE run_id=? AND status<>'settled' ORDER BY created DESC LIMIT 1",(run,))
        if previous and self.transition_status(previous[0]['id'])['status']!='settled':
            raise ValueError('clock_waits_for_previous_transition')
        with self.transaction() as c:
            r=c.execute('SELECT * FROM runs WHERE id=? AND active=1',(run,)).fetchone()
            if not r or not (r['as_of']<target or (current_event and r['as_of']==target)) or target[:10]>'2025-11-09':raise ValueError('invalid_clock_advance')
            if c.execute('SELECT 1 FROM families WHERE run_id=? AND (busy=1 OR projected_seq<>inbox_seq)',(run,)).fetchone():raise ValueError('clock_waits_for_turns_and_ingress')
            if c.execute("SELECT 1 FROM model_attempts WHERE run_id=? AND status IN ('queued','running')",(run,)).fetchone():raise ValueError('clock_waits_for_models')
            if c.execute("SELECT 1 FROM outbox WHERE run_id=? AND status IN ('queued','submitting','accepted','unknown')",(run,)).fetchone():raise ValueError('clock_waits_for_receipts')
            transition=ident();epoch=r['epoch']+1
            watermark={x['family_id']:x['inbox_seq'] for x in c.execute('SELECT * FROM families WHERE run_id=?',(run,))}
            c.execute('INSERT INTO transitions VALUES(?,?,?,?,?,?,?,?,NULL)',(transition,run,r['as_of'],target,epoch,'applying',encode(watermark),time.time()))
            c.execute("UPDATE runs SET as_of=?,epoch=?,clock_mode='frozen',paused=CASE WHEN ? THEN 0 ELSE paused END WHERE id=?",(target,epoch,int(auto_execute),run))
            c.execute('UPDATE families SET epoch=epoch+1 WHERE run_id=?',(run,))
            self.audit(c,'clock_commit',{'transition_id':transition,'target':target,'mode':'jump_no_backfill','auto_execute':auto_execute,'current_event':current_event},run)
            return transition

    def transition_status(self,transition):
        with self.transaction() as c:
            row=c.execute('SELECT * FROM transitions WHERE id=?',(transition,)).fetchone()
            if not row:raise ValueError('transition_missing')
            if row['status']=='settled':return {**dict(row),'waiting_families':[],'pending_parts':0,'due_tasks':[]}
            waiting=[x['family_id'] for x in c.execute('SELECT * FROM families WHERE run_id=? AND (applied_clock<? OR busy=1)',(row['run_id'],row['epoch']))]
            pending=c.execute("SELECT COUNT(*) FROM outbox WHERE run_id=? AND status IN ('queued','submitting','accepted','unknown')",(row['run_id'],)).fetchone()[0]
            models=c.execute("SELECT COUNT(*) FROM model_attempts WHERE run_id=? AND status IN ('queued','running')",(row['run_id'],)).fetchone()[0]
            due=[]
            for f in c.execute('SELECT * FROM families WHERE run_id=?',(row['run_id'],)):
                if f['held'] or f['paused']:continue
                db=Path(f['workspace'])/'runtime/live.sqlite'
                if not db.exists():continue
                with contextlib.closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as local:
                    if local.execute("SELECT 1 FROM sqlite_master WHERE name='service_tasks'").fetchone():
                        due.extend({'family_id':f['family_id'],'task_id':t[0],'status':t[1]} for t in local.execute("SELECT id,status FROM service_tasks WHERE due_at<=? AND expires_at>=? AND status IN ('planned','running','deferred','awaiting_delivery')",(row['target'],row['target'])))
            status='settled' if not waiting and not pending and not due and not models else 'processing'
            finished=time.time() if status=='settled' else None
            c.execute('UPDATE transitions SET status=?,finished=? WHERE id=?',(status,finished,transition))
            return {**dict(row),'status':status,'finished':finished,'waiting_families':waiting,'pending_parts':pending,'pending_models':models,'due_tasks':due}

    def clock_mode(self,run,mode):
        if mode not in ('frozen','realtime'):raise ValueError('invalid_clock_mode')
        with self.transaction() as c:
            r=c.execute('SELECT * FROM runs WHERE id=? AND active=1',(run,)).fetchone()
            if not r:raise ValueError('inactive_run')
            if r['paused'] and mode=='realtime':raise ValueError('resume_experiment_before_clock')
            c.execute('UPDATE runs SET clock_mode=?,anchor_wall=?,anchor_business=? WHERE id=?',(mode,time.time(),r['as_of'],run))
            self.audit(c,'clock_mode',{'mode':mode},run)

    def recover(self):
        with self.transaction() as c:
            # Only call under the exclusive supervisor process lock after child reaping.
            c.execute("UPDATE runs SET paused=1,clock_mode='frozen',epoch=epoch+1 WHERE active=1")
            c.execute('UPDATE families SET busy=0,turn_token=NULL,epoch=epoch+1')
            c.execute("UPDATE outbox SET status='unknown',error='supervisor_restart_during_submit' WHERE status='submitting'")
            c.execute("UPDATE model_attempts SET status='failed',error='supervisor_restart_usage_unknown',finished=? WHERE status IN ('queued','running')",(time.time(),))
            self.audit(c,'restart_paused',{})

    def archive(self,run):
        with self.transaction() as c:
            r=c.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
            if not r or not r['paused']:raise ValueError('pause_before_new_run')
            if c.execute('SELECT 1 FROM families WHERE run_id=? AND busy=1',(run,)).fetchone():raise ValueError('turn_still_active')
            if c.execute("SELECT 1 FROM outbox WHERE run_id=? AND status='submitting'",(run,)).fetchone():raise ValueError('send_still_in_flight')
            c.execute('UPDATE runs SET active=0 WHERE id=?',(run,))
            c.execute('UPDATE bindings SET enabled=0 WHERE run_id=?',(run,))
            c.execute("UPDATE outbox SET status='cancelled',error='run_archived' WHERE run_id=? AND status='queued'",(run,))
            self.audit(c,'run_archived_receipts_retained',{},run)

    def snapshot(self):
        r=self.current();run=r['id']
        from family_lab_cost import summary
        facts=self.read('SELECT kind,body,family_id FROM ingress WHERE run_id=? AND substr(business_at,1,10)=?',(run,r['as_of'][:10]))
        overview={'completion_events':0,'student_messages':0,'answers':0,'manual_notes':0,'feedback':0,'offline_phone':0}
        for fact in facts:
            key={'completion':'completion_events','message':'student_messages','answer':'answers'}.get(fact['kind'])
            if key:overview[key]+=1
            if fact['kind']=='note':
                overview['manual_notes']+=1
                method=json.loads(fact['body']).get('method')
                if method in ('feedback','offline_phone'):overview[method]+=1
        overview['failed_parts']=self.read("SELECT COUNT(*) n FROM outbox WHERE run_id=? AND status='failed'",(run,))[0]['n']
        return {'run':r,'overview':overview,'costs':summary(self,run),'rounds':self.read('SELECT * FROM runs ORDER BY created DESC'),'families':self.read('SELECT * FROM families WHERE run_id=?',(run,)),
          'bindings':[{k:v for k,v in x.items() if k!='route'} for x in self.read('SELECT * FROM bindings WHERE run_id=?',(run,))],
          'cases':self.read('SELECT * FROM cases WHERE run_id=? ORDER BY status,priority DESC,first_wait',(run,)),
          'models':self.read('SELECT * FROM model_attempts WHERE run_id=? ORDER BY created DESC LIMIT 80',(run,)),
          'outbox':self.read('SELECT * FROM outbox WHERE run_id=? ORDER BY created DESC LIMIT 200',(run,))}
