"""Family/run scoped immutable evidence. Cache reads never satisfy fresh-fact checks."""
from contextlib import closing
import hashlib,json,sqlite3
import sql_tools as s
import test_mode as tm
ALLOWED={'course_read','course_knowledge','understand_audio'}
def scope():
    state=tm.state()
    if not state:raise ValueError('bound_family_required')
    return state['run_id'],state['uid']
def initialize(c):
    c.execute('CREATE TABLE IF NOT EXISTS context_materials(id TEXT PRIMARY KEY,run_id TEXT,uid TEXT,tool TEXT,args TEXT,body TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
def capture(tool,args,value):
    if isinstance(value,dict) and value.get('passive_cache'):return value
    if tool not in ALLOWED or not isinstance(value,dict) or value.get('error') or value.get('status') in ('failed','unknown','pending'):return value
    run,uid=scope();body=s.encoded(value);a=s.encoded(args)
    ident=hashlib.sha256(s.encoded([run,uid,tool,a,body]).encode()).hexdigest()
    with closing(sqlite3.connect(s.DB)) as c,c:
        initialize(c);c.execute('INSERT OR IGNORE INTO context_materials(id,run_id,uid,tool,args,body) VALUES(?,?,?,?,?,?)',(ident,run,uid,tool,a,body))
    return {**value,'material_ref':ident,'material_note':('同版本资料使用evidence_read(ref={kind:material,id:material_ref})；历史缓存不授权，语音解释服从原话纠正。' if __import__('tool_surface').enabled('knowledge') else '同版本资料可用material_read复用；此缓存不是当前学情、回执或执行授权。语音解释须服从后续原话纠正。')}
def read(material_id,offset=0):
    if str(material_id).startswith('passive-'):return __import__('passive_context').read(material_id,offset)
    if type(offset) is not int or offset<0:raise ValueError('invalid_material_offset')
    run,uid=scope()
    with closing(sqlite3.connect(s.DB)) as c:
        initialize(c);row=c.execute('SELECT tool,args,body,created_at FROM context_materials WHERE id=? AND run_id=? AND uid=?',(material_id,run,uid)).fetchone()
    if not row:raise ValueError('material_not_in_current_family_run')
    tool,args,body,at=row
    if offset>len(body):raise ValueError('invalid_material_offset')
    end=min(offset+6000,len(body))
    return {'material_id':material_id,'source_tool':tool,'source_args':json.loads(args),'captured_at':at,'historical_snapshot':True,'authority':False,'offset':offset,'next_offset':end if end<len(body) else None,'json_fragment':body[offset:end]}
def index():
    run,uid=scope()
    with closing(sqlite3.connect(s.DB)) as c:
        initialize(c);rows=c.execute('SELECT id,tool,args,created_at FROM context_materials WHERE run_id=? AND uid=? ORDER BY rowid DESC LIMIT 12',(run,uid)).fetchall()
    return [{'material_id':i,'source_tool':t,'source_args':json.loads(a),'captured_at':at} for i,t,a,at in rows]
