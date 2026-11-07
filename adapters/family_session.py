"""Persistent host-only family binding. Message speaker never changes ownership."""
import json,sqlite3
from contextlib import closing
import sql_tools as s
import test_mode as tm

def bind(session_id):
    state=tm.state()
    if not state or not session_id:raise ValueError('family_session_requires_bound_run')
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('CREATE TABLE IF NOT EXISTS family_session_bindings(session_id TEXT PRIMARY KEY,run_id TEXT,uid TEXT,profile TEXT,created_at TEXT)')
        row=c.execute('SELECT run_id,uid,profile FROM family_session_bindings WHERE session_id=?',(session_id,)).fetchone()
        if row:
            if (row[0],row[1])!=(state['run_id'],state['uid']):raise ValueError('family_session_binding_conflict')
        else:
            profile=s._source_family_profile()
            if profile.get('uid')!=state['uid']:raise ValueError('family_profile_uid_mismatch')
            c.execute('INSERT INTO family_session_bindings VALUES(?,?,?,?,?)',(session_id,state['run_id'],state['uid'],s.encoded(profile),s.now()))
    return current(session_id)

def current(session_id=None):
    if session_id is None:
        path=s.R/'runtime/service-session.json'
        if not path.exists():return None
        session_id=json.loads(path.read_text()).get('sessionId')
    state=tm.state()
    if not state:return None
    with closing(sqlite3.connect('file:'+str(s.DB)+'?mode=ro',uri=True)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='family_session_bindings'").fetchone():return None
        row=c.execute('SELECT run_id,uid,profile,created_at FROM family_session_bindings WHERE session_id=?',(session_id,)).fetchone()
    if not row:return None
    if (row[0],row[1])!=(state['run_id'],state['uid']):raise ValueError('family_session_binding_conflict')
    return {'session_id':session_id,'family_uid':row[1],'profile':json.loads(row[2]),'bound_at':row[3],'binding':'host_owned_immutable_session','speaker_note':'绑定家庭不等于当前消息发言人；称呼依本轮原话证据。'}

if __name__=='__main__':
    import sys
    print(s.encoded(bind(sys.argv[1])))
