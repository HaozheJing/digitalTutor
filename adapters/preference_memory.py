"""Evidence-backed parent/child preferences and internal nightly consolidation.

Uses the existing database, event queue, native model and business clock. No sender,
model client, cron, inferred active profile or production configuration is added.
"""
import contextlib
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
import memory_protocol
import sqlite3
import sys
import uuid
from collections import Counter
import sql_tools as s

KEYS = {
    'address.name': None,
    'reply.length': ['brief', 'standard', 'detailed'],
    'reply.explanation': ['steps', 'examples', 'direct'],
    'media.emoji': ['allowed', 'disabled'],
    'media.images': ['allowed', 'disabled'],
    'contact.window': None,
    'reading.interest': None,
    'reading.routine': None,
    'service.context': None,
}
INTERNAL = 'memory_consolidation'
SUBJECTS = ('parent','child')
INSTRUCTION = Path(__file__).with_name('family-memory-policy.md').read_text(encoding='utf8')
FAST_INSTRUCTION = ('即时回复依据本次原话、真实对话和已有记忆。当前原话优先；旧记忆不能压过当前纠正。'
    '普通偏好、兴趣和服务观察由每天北京时间22点统一提取，本轮不整理、不写入、不声称已存为长期记忆。'
    '暂停、取消、恢复和改期等业务控制仍按当前原话通过业务工具即时执行，成功后再确认。'
    '用户明确要求忘记、删除或不再保存时，立即preference_context，然后preference_commit提交forget；明确允许或禁止图片/表情属于操作控制，可按当轮原话提交media.images/media.emoji的set，不能由夜间推断更改。'
    '遗忘按parent/child和具体key处理；不要把纠正或暂时兴趣当成删除。')



def fast_instruction():
    """Do not promise a nightly run when the optional scheduler is disabled."""
    if settings().get('slow_enabled') is True:
        return FAST_INSTRUCTION
    return FAST_INSTRUCTION.replace(
        '普通偏好、兴趣和服务观察由每天北京时间22点统一提取，本轮不整理、不写入、不声称已存为长期记忆。',
        '夜间整理未启用；普通偏好、兴趣和服务观察本轮不写入，不承诺稍后自动提取或已存为长期记忆。')


def settings():
    file = s.R / 'config/preference-memory.json'
    value = json.loads(file.read_text(encoding='utf8')) if file.exists() else {}
    if set(value) - {'enabled', 'slow_enabled', 'family_uid', 'contact_id'}:
        raise ValueError('unknown_memory_configuration')
    return value


def enabled():
    return settings().get('enabled') is True


def clock():
    return __import__('test_mode').clock()


def stamp(value):
    at = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not at.tzinfo: raise ValueError('memory_timezone_required')
    return at


def digest(value):
    return hashlib.sha256(s.encoded(value).encode()).hexdigest()


def tables(c):
    for sql in [
        'CREATE TABLE IF NOT EXISTS preference_scopes(scope TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS preference_bindings(namespace TEXT PRIMARY KEY,identity TEXT NOT NULL)',
        '''CREATE TABLE IF NOT EXISTS preference_versions(id TEXT PRIMARY KEY,scope TEXT,key TEXT,value TEXT,
          revision INTEGER,status TEXT,source_id TEXT,quote TEXT,source_hash TEXT,source_at TEXT,
          source_row INTEGER,valid_from TEXT,expires_at TEXT,created_at TEXT)''',
        'CREATE TABLE IF NOT EXISTS preference_tokens(token TEXT PRIMARY KEY,scope TEXT,revision INTEGER,cursor INTEGER,sources TEXT)',
        'CREATE TABLE IF NOT EXISTS preference_requests(scope TEXT,request_id TEXT,body_hash TEXT,result TEXT,PRIMARY KEY(scope,request_id))',
        'CREATE TABLE IF NOT EXISTS preference_forgotten(scope TEXT,source_id TEXT,PRIMARY KEY(scope,source_id))',
        'CREATE TABLE IF NOT EXISTS memory_journal(seq INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,kind TEXT,source_id TEXT,hash TEXT,body TEXT,recorded_at TEXT)',
        'CREATE TABLE IF NOT EXISTS memory_seen(scope TEXT,kind TEXT,source_id TEXT,hash TEXT,PRIMARY KEY(scope,kind,source_id))',
        'CREATE TABLE IF NOT EXISTS memory_checkpoints(scope TEXT PRIMARY KEY,seq INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS memory_clock(scope TEXT PRIMARY KEY,next_due TEXT NOT NULL)',
        '''CREATE TABLE IF NOT EXISTS memory_runs(event_id TEXT PRIMARY KEY,scope TEXT,day TEXT,from_seq INTEGER,to_seq INTEGER,
          reviewed_seq INTEGER,revision INTEGER,result_hash TEXT,result TEXT,UNIQUE(scope,day))''',
        'CREATE TABLE IF NOT EXISTS memory_candidates(id TEXT PRIMARY KEY,scope TEXT,event_id TEXT,key TEXT,note TEXT,evidence TEXT,status TEXT)',
        'CREATE TABLE IF NOT EXISTS memory_summaries(event_id TEXT PRIMARY KEY,scope TEXT,entries TEXT)',
    ]: c.execute(sql)
    for table in ('preference_versions','memory_candidates'):
        if 'subject' not in {r[1] for r in c.execute('PRAGMA table_info('+table+')')}:
            c.execute("ALTER TABLE "+table+" ADD COLUMN subject TEXT NOT NULL DEFAULT 'parent'")
    for key,kind in {'applicability':"TEXT NOT NULL DEFAULT 'persistent'",'reason':"TEXT NOT NULL DEFAULT '旧版明确偏好'"}.items():
        if key not in {r[1] for r in c.execute('PRAGMA table_info(preference_versions)')}:
            c.execute('ALTER TABLE preference_versions ADD COLUMN '+key+' '+kind)
    __import__('memory_maintenance').migrate(c)



def migrate_family_scope(c,old,new):
    """Same bound family only; existing v1 contact preferences become parent data."""
    if not c.execute('SELECT 1 FROM preference_scopes WHERE scope=?',(old,)).fetchone():return
    if c.execute('SELECT 1 FROM preference_scopes WHERE scope=?',(new,)).fetchone():raise ValueError('memory_scope_migration_conflict')
    for table in ('preference_scopes','preference_versions','preference_tokens','preference_requests','preference_forgotten',
                  'memory_journal','memory_seen','memory_checkpoints','memory_clock','memory_runs','memory_candidates','memory_summaries',
                  'memory_prepared','memory_history'):
        c.execute('UPDATE '+table+' SET scope=? WHERE scope=?',(new,old))
    for request,raw in c.execute('SELECT request_id,result FROM preference_requests WHERE scope=?',(new,)).fetchall():
        result=json.loads(raw);result['scope']=new
        c.execute('UPDATE preference_requests SET result=? WHERE scope=? AND request_id=?',(s.encoded(result),new,request))
    for ident,raw in c.execute('SELECT e.id,e.payload FROM events e JOIN memory_runs r ON r.event_id=e.id WHERE r.scope=?',(new,)).fetchall():
        payload=json.loads(raw);payload['memory_scope']=new
        c.execute('UPDATE events SET payload=? WHERE id=?',(s.encoded(payload),ident))


def scope(c):
    cfg = settings()
    if cfg.get('enabled') is not True: raise ValueError('preference_memory_disabled')
    row = c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
    state = json.loads(row[0]) if row else None
    namespace = 'test:' + state['run_id'] if state else 'live'
    uid = state['uid'] if state else cfg.get('family_uid')
    contact = cfg.get('contact_id')
    if not uid or not contact: raise ValueError('trusted_memory_identity_required')
    if state and cfg.get('family_uid', uid) != uid: raise ValueError('memory_family_mismatch')
    identity = s.encoded([uid, contact])
    old = c.execute('SELECT identity FROM preference_bindings WHERE namespace=?', (namespace,)).fetchone()
    if old and old[0] != identity: raise ValueError('memory_database_binding_changed')
    c.execute('INSERT OR IGNORE INTO preference_bindings VALUES(?,?)', (namespace, identity))
    name = s.encoded([namespace, uid])
    migrate_family_scope(c,s.encoded([namespace,uid,contact]),name)
    c.execute('INSERT OR IGNORE INTO preference_scopes(scope) VALUES(?)', (name,))
    c.execute('INSERT OR IGNORE INTO memory_checkpoints(scope) VALUES(?)', (name,))
    due=clock().astimezone(dt.timezone(dt.timedelta(hours=8))).replace(hour=22,minute=0,second=0,microsecond=0)
    c.execute('INSERT OR IGNORE INTO memory_clock VALUES(?,?)',(name,due.isoformat()))
    return name


@contextlib.contextmanager
def database():
    with contextlib.closing(sqlite3.connect(s.DB, timeout=15)) as c, c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN IMMEDIATE')
        tables(c)
        yield c, scope(c)


def cursor(c):
    return c.execute("SELECT COALESCE(MAX(rowid),0) FROM messages WHERE speaker='user' AND visible=1").fetchone()[0]


def revision(c, sc):
    return c.execute('SELECT revision FROM preference_scopes WHERE scope=?', (sc,)).fetchone()[0]


def forgotten_keys(c,sc,subject='parent'):
    return [r[0] for r in c.execute("SELECT key FROM preference_versions p WHERE scope=? AND subject=? AND status='forgotten' AND NOT EXISTS(SELECT 1 FROM memory_candidates m WHERE m.scope=p.scope AND m.subject=p.subject AND m.key=p.key AND m.status='reported' AND m.revision>p.revision) AND revision=(SELECT MAX(revision) FROM preference_versions WHERE scope=p.scope AND key=p.key AND subject=p.subject)",(sc,subject))]


def grouped(items):
    return {subject:[x for x in items if x['subject']==subject] for subject in SUBJECTS}


def active(c, sc, keys=None,subject=None):
    now = clock()
    result = []
    query=c.execute("SELECT * FROM preference_versions WHERE scope=? AND status='active' ORDER BY key", (sc,))
    for raw in query:
        row=dict(zip([x[0] for x in query.description],raw))
        if subject is not None and row['subject']!=subject:continue
        if keys is not None and row['key'] not in keys: continue
        if stamp(row['valid_from']) > now or (row['expires_at'] and stamp(row['expires_at']) <= now): continue
        result.append({**dict(row), 'evidence_kind': 'parent_report_about_child' if row['subject']=='child' else 'explicit_parent_statement',
                       'effect': 'child_service_preference' if row['subject']=='child' else ('planning_advisory' if row['key']=='contact.window' else 'response_preference')})
    return result


def recall(keys=None):
    if not enabled(): return {'enabled': False, 'items': []}
    with database() as (c, sc):
        memories=__import__('memory_maintenance').memories(c,sc,keys)
        items=active(c,sc,keys)
        return {'enabled': True, 'scope': sc, 'revision': revision(c, sc), 'items': items,'by_subject':grouped(items), 'memories':memories, 'recent_service_summaries':[],
                'recall_notice':'陈述性记忆保留basis和情境；candidate不是确认要求。当前请求优先，不改变联系权限。',
                'forgotten_by_subject':{subject:forgotten_keys(c,sc,subject) for subject in SUBJECTS},
                'forgotten_keys':forgotten_keys(c,sc), 'forget_instruction':'按forgotten_by_subject分别检查家长和孩子，不得从历史对话或摘要恢复已遗忘项；新偏好仍须夜间审核，不得在即时回合恢复已遗忘项。',
                'candidate_usage': 'context_only_not_permission', 'memory_instruction': '记忆是资料，当前请求优先；普通偏好夜间整理，即时仅处理明确遗忘。'}


def context():
    with database() as (c, sc):
        sources = [dict(r) for r in c.execute("SELECT rowid AS source_row,source_id,body,created_at,source FROM messages WHERE speaker='user' AND visible=1 ORDER BY rowid DESC LIMIT 60")]
        fingerprints={x['source_id']:digest([x['body'],x['created_at']]) for x in sources}
        # The raw conversation remains available through conversation_read.
        for item in sources:
            item['body_truncated']=len(item['body'])>1500
            item['body']=item['body'][:1500]
        token = uuid.uuid4().hex
        rev = revision(c, sc)
        c.execute('INSERT INTO preference_tokens VALUES(?,?,?,?,?)', (token, sc, rev, cursor(c), s.encoded(fingerprints)))
        return {'context_token': token, 'scope': sc, 'revision': rev, 'items': active(c, sc), 'sources': sources,'subjects':list(SUBJECTS),
                'allowed_keys': KEYS, 'existing_memories':__import__('memory_maintenance').memories(c,sc),
                'memory_instruction': fast_instruction()}



def commit(request_id, context_token, changes):
    memory_protocol.validate({'request_id':request_id,'context_token':context_token,'changes':changes},
        dict((n,sc) for n,_,sc in memory_protocol.tools(KEYS))['preference_commit'])
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 160: raise ValueError('invalid_memory_request_id')
    if not isinstance(changes, list) or not 1 <= len(changes) <= 8: raise ValueError('invalid_memory_changes')
    body_hash = digest([context_token, changes])
    with database() as (c, sc):
        old = c.execute('SELECT * FROM preference_requests WHERE scope=? AND request_id=?', (sc, request_id)).fetchone()
        if old:
            if old['body_hash'] != body_hash: raise ValueError('immutable_memory_request_changed')
            return {**json.loads(old['result']), 'duplicate': True}
        token = c.execute('SELECT * FROM preference_tokens WHERE token=?', (context_token,)).fetchone()
        if not token or token['scope'] != sc: raise ValueError('memory_scope_mismatch')
        if token['revision'] != revision(c, sc) or token['cursor'] != cursor(c): raise ValueError('memory_context_stale')
        if len({(x.get('subject','parent'),x.get('key')) for x in changes}) != len(changes): raise ValueError('duplicate_memory_key')
        new_revision = token['revision'] + 1
        ids = []
        for change in changes:
            if set(change) - {'key','action','value','source_message_id','quote','expires_at','subject','basis','applicability','reason','replaces_memory_ids'}: raise ValueError('unknown_memory_change_field')
            subject=change.get('subject','parent')
            if subject not in SUBJECTS:raise ValueError('invalid_memory_subject')
            key, action = change.get('key'), change.get('action')
            if key not in KEYS or action not in ('set','forget'): raise ValueError('unsupported_memory_change')
            source_id, quote = change.get('source_message_id'), change.get('quote')
            if source_id not in json.loads(token['sources']): raise ValueError('memory_evidence_outside_context')
            src = c.execute("SELECT rowid AS source_row,* FROM messages WHERE source_id=? AND speaker='user' AND visible=1", (source_id,)).fetchone()
            if not src or not isinstance(quote,str) or not quote.strip() or quote not in src['body']: raise ValueError('exact_user_quote_required')
            if json.loads(token['sources'])[source_id]!=digest([src['body'],src['created_at']]):raise ValueError('memory_source_changed')
            if c.execute('SELECT 1 FROM preference_forgotten WHERE scope=? AND source_id=?',(sc,source_id)).fetchone(): raise ValueError('forgotten_evidence_cannot_be_reused')
            previous = c.execute('SELECT * FROM preference_versions WHERE scope=? AND key=? AND subject=? ORDER BY revision DESC LIMIT 1', (sc,key,subject)).fetchone()
            if previous and (stamp(src['created_at']),src['source_row']) <= (stamp(previous['source_at']),previous['source_row']):
                raise ValueError('older_or_same_evidence_cannot_replace_memory')
            expires = change.get('expires_at')
            value = change.get('value')
            contextual=action=='set' and change['applicability']=='temporary' and not expires
            if action == 'set':
                if not isinstance(value,str) or not value.strip() or len(value)>300: raise ValueError('invalid_memory_value')
                if KEYS[key] and value not in KEYS[key]: raise ValueError('invalid_memory_enum')
                if change['applicability']=='persistent' and expires:raise ValueError('persistent_preference_has_no_expiry')
                if expires and stamp(expires)<=clock():raise ValueError('memory_expiry_must_be_future')
            elif value is not None or expires is not None: raise ValueError('forget_has_no_value_or_expiry')
            if action=='forget' and not re.search(r'忘记|忘掉|删除|删掉|清除|抹掉|(?:不要|别|不用|不再).{0,12}(?:记住|记着|记下来|保存|存着|存下|留着|保留|留存)',quote):
                raise ValueError('forget_requires_explicit_removal_request; correction uses set and temporary without invented expiry')
            if subject=='child' and key=='contact.window':raise ValueError('contact_window_belongs_to_parent')
            __import__('memory_maintenance').check_text(change['reason'],'reason')
            __import__('memory_maintenance').retract_from_fast(c,sc,change.get('replaces_memory_ids',[]),new_revision,key,subject,change['reason'])
            if not contextual:
                c.execute("UPDATE preference_versions SET status='superseded' WHERE scope=? AND key=? AND subject=? AND status='active'", (sc,key,subject))
            else:
                collect(c,sc)
                __import__('memory_maintenance').write_contextual(c,sc,change,new_revision)
            if action == 'forget':
                forgotten = [r[0] for r in c.execute('SELECT DISTINCT source_id FROM preference_versions WHERE scope=? AND key=? AND subject=?',(sc,key,subject))]
                if key=='address.name' and subject=='parent' and c.execute("SELECT 1 FROM sqlite_master WHERE name='memories'").fetchone():
                    forgotten.extend(r[0] for r in c.execute("SELECT source_message_id FROM memories WHERE memory_key='speaker.self_reported_name'"))
                    c.execute("UPDATE memories SET value='',quote='',status='forgotten' WHERE memory_key='speaker.self_reported_name'")
                # Slow-only facts must be forgotten even if never promoted to an
                # operational preference. Include past revisions, not just head.
                refs=set();removed_ids=set()
                for raw in c.execute('SELECT id,evidence FROM memory_candidates WHERE scope=? AND key=? AND subject=?',(sc,key,subject)):
                    removed_ids.add(raw[0]);refs.update(json.loads(raw[1]))
                for raw in c.execute('SELECT memory_id,before_json,after_json FROM memory_history WHERE scope=?',(sc,)):
                    for blob in raw[1:]:
                        item=json.loads(blob)
                        if item and item.get('key')==key and item.get('subject')==subject:
                            removed_ids.add(raw[0])
                            refs.update(json.loads(item.get('evidence','[]')))
                for seq in refs:
                    row=c.execute("SELECT source_id FROM memory_journal WHERE scope=? AND seq=? AND kind='message'",(sc,seq)).fetchone()
                    if row:forgotten.append(row[0])
                for ident in forgotten:
                    c.execute('INSERT OR IGNORE INTO preference_forgotten VALUES(?,?)',(sc,ident))
                    c.execute("UPDATE memory_journal SET body=? WHERE scope=? AND kind='message' AND source_id=?",(s.encoded({'source_id':ident,'redacted':True}),sc,ident))
                    c.execute('UPDATE preference_versions SET quote=NULL,source_hash=NULL WHERE scope=? AND source_id=?',(sc,ident))
                # Remove target records and notes that depend on erased sources;
                # unrelated family memories must survive a scoped forget.
                erased=set(forgotten)
                for row in c.execute('SELECT id,evidence FROM memory_candidates WHERE scope=?',(sc,)):
                    for seq in json.loads(row['evidence']):
                        source=c.execute('SELECT source_id FROM memory_journal WHERE scope=? AND seq=?',(sc,seq)).fetchone()
                        # Raw shared evidence is redacted, but unrelated scoped facts survive.
                        # The redacted journal cannot be used for fresh inference.
                        if source and source[0] in erased:break
                c.execute('DELETE FROM memory_summaries WHERE scope=?',(sc,))
                c.execute('DELETE FROM memory_prepared WHERE scope=?',(sc,))
                for mid in removed_ids:
                    c.execute('DELETE FROM memory_candidates WHERE scope=? AND id=?',(sc,mid))
                    c.execute('DELETE FROM memory_history WHERE scope=? AND memory_id=?',(sc,mid))
                c.execute("UPDATE preference_versions SET value=NULL,quote=NULL,source_hash=NULL,reason='已遗忘内容',status='forgotten' WHERE scope=? AND key=? AND subject=?",(sc,key,subject))
            ident = 'pref-' + uuid.uuid4().hex
            c.execute('INSERT INTO preference_versions(id,scope,key,value,revision,status,source_id,quote,source_hash,source_at,source_row,valid_from,expires_at,created_at,subject,applicability,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (ident,sc,key,value,new_revision,'contextual' if contextual else ('active' if action=='set' else 'forgotten'),source_id,
                       quote if action=='set' else None,digest(src['body']) if action=='set' else None,
                       src['created_at'],src['source_row'],clock().isoformat(),expires,s.now(),subject,change['applicability'],change['reason'] if action=='set' else '用户要求遗忘'))
            ids.append(ident)
        c.execute('UPDATE preference_scopes SET revision=? WHERE scope=?',(new_revision,sc))
        result = {'status':'committed','scope':sc,'revision':new_revision,'memory_ids':ids}
        c.execute('INSERT INTO preference_requests VALUES(?,?,?,?)',(sc,request_id,body_hash,s.encoded(result)))
        return result


def guard_media(c, message_type):
    if not enabled() or message_type not in (5,6): return
    tables(c); sc=scope(c)
    key='media.emoji' if message_type==5 else 'media.images'
    if any(x['value']=='disabled' for x in active(c,sc,[key],subject='parent')): raise ValueError('media_blocked_by_explicit_preference')


def collect(c, sc):
    """Capture changed source projections, including late final receipts as new entries."""
    specs=[('message','messages','source_id',['source_id','speaker','body','created_at','source','visible']),
           ('task','service_tasks','id',['id','kind','skill','status','day','run_at','result','run_id','uid']),
           ('delivery','deliveries','action_key',['action_key','body','status','created_at','source']),
           ('receipt','test_outbox','id',['id','status','message_type','updated_at'])]
    for kind,table,pk,wanted in specs:
        if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone(): continue
        columns={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
        selected=[x for x in wanted if x in columns]
        for row in c.execute('SELECT '+','.join(selected)+' FROM '+table).fetchall():
            value=dict(row);ident=str(value[pk])
            value['evidence_environment']='experiment_simulation' if (s.R/'config/experiment.json').is_file() else 'bound_runtime'
            if 'source' in value:value['evidence_origin']=value['source']
            if kind=='task' and value.get('run_id') and json.loads(sc)[0]!='test:'+value['run_id']:continue
            if kind=='message':
                if not value.get('visible'):continue
                if c.execute('SELECT 1 FROM preference_forgotten WHERE scope=? AND source_id=?',(sc,ident)).fetchone():
                    value={'source_id':ident,'redacted':True}
            hashed=digest(value)
            old=c.execute('SELECT hash FROM memory_seen WHERE scope=? AND kind=? AND source_id=?',(sc,kind,ident)).fetchone()
            if old and old[0]==hashed:continue
            c.execute('INSERT INTO memory_journal(scope,kind,source_id,hash,body,recorded_at) VALUES(?,?,?,?,?,?)',(sc,kind,ident,hashed,s.encoded(value),clock().isoformat()))
            c.execute('INSERT OR REPLACE INTO memory_seen VALUES(?,?,?,?)',(sc,kind,ident,hashed))


def tick():
    if not enabled() or settings().get('slow_enabled') is not True: return {'status':'disabled'}
    now=clock().astimezone(dt.timezone(dt.timedelta(hours=8)))
    # A missed 22:00 run is recoverable with its actual catch-up time.
    day=(now.date() if now.hour>=22 else now.date()-dt.timedelta(days=1)).isoformat()
    with database() as (c,sc):
        next_due=stamp(c.execute('SELECT next_due FROM memory_clock WHERE scope=?',(sc,)).fetchone()[0])
        if c.execute("SELECT 1 FROM memory_runs r JOIN events e ON e.id=r.event_id WHERE r.scope=? AND e.status<>'done'",(sc,)).fetchone():return {'status':'pending'}
        if c.execute('SELECT 1 FROM memory_runs WHERE scope=? AND day=?',(sc,day)).fetchone():return {'status':'already_scheduled'}
        if now<next_due:return {'status':'not_due'}
        collect(c,sc)
        start=c.execute('SELECT seq FROM memory_checkpoints WHERE scope=?',(sc,)).fetchone()[0]
        end=c.execute('SELECT COALESCE(MAX(seq),0) FROM memory_journal WHERE scope=?',(sc,)).fetchone()[0]
        ident='memory-night:'+digest([sc,day])[:32]
        c.execute('INSERT INTO memory_runs(event_id,scope,day,from_seq,to_seq,reviewed_seq,revision,result_hash,result) VALUES(?,?,?,?,?,?,?,?,?)',(ident,sc,day,start,end,start,revision(c,sc),None,None))
        next_due=now.replace(hour=22,minute=0,second=0,microsecond=0)+(dt.timedelta(days=1) if now.hour>=22 else dt.timedelta())
        c.execute('UPDATE memory_clock SET next_due=? WHERE scope=?',(next_due.isoformat(),sc))
        __import__('live_runtime').enqueue(c,ident,INTERNAL,{'memory_scope':sc,'day':day,'cutoff_at':now.isoformat(),'from_seq':start,'to_seq':end})
        return {'status':'queued','event_id':ident,'from_seq':start,'to_seq':end,'day':day,'cutoff_at':now.isoformat()}


def internal(c=None):
    if not enabled():return False
    if c is None:
        with contextlib.closing(sqlite3.connect(s.DB)) as conn:return internal(conn)
    return bool(c.execute("SELECT 1 FROM events WHERE kind=? AND status='dispatched'",(INTERNAL,)).fetchone())


def pause():
    if not enabled():return {'paused':False}
    with database() as (c,sc):
        c.execute("UPDATE events SET status='pending',attempts=MAX(0,attempts-1) WHERE kind=? AND status='dispatched' AND id IN (SELECT event_id FROM memory_runs WHERE scope=?)",(INTERNAL,sc))
        return {'paused':True}


def retry(event_id):
    """Trusted operator recovery only; deliberately not exposed as a model tool."""
    with database() as (c,sc):
        row=c.execute('SELECT e.status FROM memory_runs r JOIN events e ON e.id=r.event_id WHERE r.scope=? AND r.event_id=?',(sc,event_id)).fetchone()
        if not row or row['status']!='needs_attention':raise ValueError('memory_run_not_retryable')
        c.execute("UPDATE events SET status='pending',attempts=0,retry_after=NULL WHERE id=?",(event_id,))
        return {'status':'pending','event_id':event_id}


def run_row(c,sc,event_id):
    row=c.execute('SELECT r.*,e.status FROM memory_runs r JOIN events e ON e.id=r.event_id WHERE r.event_id=? AND r.scope=?',(event_id,sc)).fetchone()
    if not row or row['status']!='dispatched':raise ValueError('memory_run_not_owned')
    if c.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched')").fetchone():raise ValueError('dialogue_has_priority')
    return row


def consolidation_context(event_id, after_sequence=None):
    with database() as (c,sc):
        row=run_row(c,sc,event_id)
        # Zero is an explicit first-page sentinel, never an absolute rewind.
        after=row['from_seq'] if after_sequence is None or type(after_sequence) is int and after_sequence==0 else after_sequence
        if not isinstance(after,int) or not row['from_seq']<=after<=row['reviewed_seq']:raise ValueError('memory_page_gap')
        items=[];size=0
        for source in c.execute('SELECT * FROM memory_journal WHERE scope=? AND seq>? AND seq<=? ORDER BY seq LIMIT 100',(sc,after,row['to_seq'])):
            if items and size+len(source['body'])>40000:break
            items.append(dict(source));size+=len(source['body'])
        for item in items:item['body']=json.loads(item['body'])
        through=items[-1]['seq'] if items else row['to_seq']
        if after==row['from_seq'] and row['revision']!=revision(c,sc):
            c.execute('UPDATE memory_runs SET revision=?,reviewed_seq=? WHERE event_id=?',(revision(c,sc),row['from_seq'],event_id))
        c.execute('UPDATE memory_runs SET reviewed_seq=MAX(reviewed_seq,?) WHERE event_id=?',(through,event_id))
        latest={}
        for source in c.execute("SELECT kind,source_id,body FROM memory_journal WHERE scope=? AND seq>? AND seq<=? AND kind IN ('delivery','receipt') ORDER BY seq",(sc,row['from_seq'],row['to_seq'])):
            latest[(source['kind'],source['source_id'])]=json.loads(source['body'])
        statistics={kind+'_status_counts':dict(Counter(body.get('status','unknown') for (k,_),body in latest.items() if k==kind)) for kind in ['delivery','receipt']}
        return {'event_id':event_id,'scope':sc,'revision':revision(c,sc),'items':items,'next_sequence':through,
                'window_statistics':statistics,'statistics_basis':'本次窗口内各source_id的最新状态；receipt是分段，不等于逻辑delivery数量。计数以此字段为准。',
                'delivery_evidence_notice':'当前出口为隔离模拟，不能写成真实微信送达。' if (s.R/'config/experiment.json').is_file() else '区分接口受理、未知与最终回执；摘要不替代账本。',
                'has_more':through<row['to_seq'],'from_sequence':row['from_seq'],'through_sequence':row['to_seq'],
                'existing_memories':__import__('memory_maintenance').memories(c,sc),
                'allowed_keys':KEYS,'repair_of':row['repair_of'],
                'repair_reason':json.loads(c.execute('SELECT payload FROM events WHERE id=?',(event_id,)).fetchone()[0]).get('reason') if row['repair_of'] else None,
                'active_preferences':active(c,sc),'forgotten_keys':forgotten_keys(c,sc),
                'forgotten_by_subject':{subject:forgotten_keys(c,sc,subject) for subject in SUBJECTS},
                'rules':INSTRUCTION}


def consolidation_prepare(**kwargs):return __import__('memory_maintenance').prepare(**kwargs)


def consolidation_commit(event_id,prepared_token):return __import__('memory_maintenance').commit(event_id,prepared_token)


def evidence_read(event_id,sequences):return __import__('memory_maintenance').read_evidence(event_id,sequences)


def prompt(event_id):
    return {'prompt':INSTRUCTION+'\n内部22点记忆整理任务，event_id='+event_id+
            '。读取context全部页，审查旧记忆后prepare完整变更，再commit返回的token。无需对客汇报。'}



def status():
    if not enabled():return {'enabled':False}
    with database() as (c,sc):
        items=active(c,sc)
        return {'enabled':True,'scope':sc,'revision':revision(c,sc),'preferences':items,'by_subject':grouped(items),
                'runs':[dict(r) for r in c.execute('SELECT r.*,e.status FROM memory_runs r JOIN events e ON e.id=r.event_id WHERE scope=? ORDER BY day DESC LIMIT 10',(sc,))],
                'candidates':[dict(r) for r in c.execute('SELECT * FROM memory_candidates WHERE scope=?',(sc,))],
                'summaries':[dict(r) for r in c.execute('SELECT * FROM memory_summaries WHERE scope=? ORDER BY rowid DESC LIMIT 10',(sc,))]}


if __name__=='__main__':
    action=sys.argv[1]
    functions={'tick':tick,'pause':pause,'status':status,'context':context,'recall':recall,'retry':lambda:retry(sys.argv[2]),'repair':lambda:__import__('memory_maintenance').repair(sys.argv[2],sys.argv[3]),'prompt':lambda:prompt(sys.argv[2])}
    print(s.encoded(functions[action]()))

