"""Public tool input/output and operator actions; never stores model reasoning."""
import json
import re
import sqlite3
import time
import uuid
from contextlib import closing
import sql_tools as s

PRIVATE = re.compile(r'^(authorization|api[_-]?key|access[_-]?token|password|secret|expected_token|token)$', re.I)


def clean(value, depth=0):
    if depth > 14:
        return '[内容层级过深]'
    if isinstance(value, dict):
        return {str(k): '[已隐藏]' if PRIVATE.match(str(k)) else clean(v, depth+1)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        limit=366 if len(value)<=366 and all(isinstance(x,dict) and set(x)=={'date','has_lesson','lesson_no'} for x in value) else 200
        return [clean(v, depth+1) for v in value[:limit]]
    if isinstance(value, str):
        value = re.sub(r'(?i)([?&](?:token|key|api_key|access_token)=)[^&\s"\']+', r'\1[已隐藏]', value)
        value = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~-]+', 'Bearer [已隐藏]', value)
        return value[:24000] + ('…[已截断]' if len(value)>24000 else '')
    return value


def initialize():
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('''CREATE TABLE IF NOT EXISTS tool_audit(
            id TEXT PRIMARY KEY,run_id TEXT,business_time TEXT,started_at TEXT,
            finished_at TEXT,origin TEXT,name TEXT,input TEXT,output TEXT,status TEXT,duration_ms INTEGER)''')
        c.execute('CREATE INDEX IF NOT EXISTS tool_audit_run ON tool_audit(run_id,started_at)')


def invoke(name, args, operation, origin='model'):
    import test_mode as tm
    state=tm.state()
    if not state:
        return operation()
    initialize()
    ident=uuid.uuid4().hex;start=time.monotonic()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('INSERT INTO tool_audit(id,run_id,business_time,started_at,finished_at,origin,name,input,output,status,duration_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (ident,state['run_id'],state['as_of'],s.now(),None,origin,name,
                   s.encoded(clean(args)),None,'running',None))
        if 'task_id' in [r[1] for r in c.execute('PRAGMA table_info(tool_audit)')]:
            row=c.execute("SELECT value FROM metadata WHERE key='active_service_task'").fetchone()
            if row:c.execute('UPDATE tool_audit SET task_id=? WHERE id=?',(json.loads(row[0]),ident))
            else:
                events=c.execute("SELECT id FROM events WHERE status='dispatched' AND kind='passive'").fetchall()
                if len(events)==1:c.execute('UPDATE tool_audit SET task_id=? WHERE id=?',('event:'+events[0][0],ident))
    try:
        result=operation()
    except Exception as exc:
        # Exception messages can contain credentials/URLs; keep the type only.
        from agent_plan import ServiceValidationError
        error={'error':type(exc).__name__}
        if isinstance(exc,ServiceValidationError):error['reason']=str(exc)
        finish(ident,error,'error',start)
        raise
    finish(ident,result,'ok',start)
    return result


def finish(ident,result,status,start):
    encoded=s.encoded(clean(result))
    if len(encoded)>100000:
        encoded=s.encoded({'truncated':True,'preview':encoded[:90000]})
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('UPDATE tool_audit SET finished_at=?,output=?,status=?,duration_ms=? WHERE id=?',
                  (s.now(),encoded,status,int((time.monotonic()-start)*1000),ident))


def records(run_id):
    if not s.read("SELECT 1 FROM sqlite_master WHERE name='tool_audit'"):
        return []
    rows=s.read('SELECT * FROM tool_audit WHERE run_id=? ORDER BY started_at DESC,rowid DESC LIMIT 100',(run_id,))
    for row in rows:
        for key in ('input','output'):
            row[key]=json.loads(row[key]) if row[key] else None
    return rows
