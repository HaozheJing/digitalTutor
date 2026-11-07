"""Versioned test contexts; old databases and native sessions are never reused."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
STATE=ROOT/'var/wecom-autoreply'

def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value),encoding='utf8');temp.replace(path)

@contextmanager
def locked():
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'context.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)

def current():
    p=STATE/'context.json'
    return json.loads(p.read_text()) if p.exists() else {'id':'initial','path':str(STATE),'cutoff_id':0,'started_at':None}

def pending():
    p=STATE/'context-reset.json'
    try:return json.loads(p.read_text())
    except FileNotFoundError:return None

def request(inbox, expected):
    with locked():
        if pending():return pending()
        if current()['id']!=expected:raise ValueError('测试会话已变化，请刷新后重试')
        with sqlite3.connect(inbox) as db:
            cutoff=db.execute('SELECT COALESCE(MAX(id),0) FROM callbacks').fetchone()[0]
        value={'id':uuid4().hex,'cutoff_id':cutoff,'started_at':datetime.now(timezone.utc).isoformat()}
        write(STATE/'context-reset.json',value)
        return value

def apply():
    # Called only by the bridge, after the current worker has exited.
    with locked():
        value=pending()
        if not value:return False
        folder=STATE/'contexts'/value['id'];folder.mkdir(parents=True,exist_ok=True)
        previous=current()
        archives=STATE/'context-archives';archives.mkdir(exist_ok=True)
        write(archives/(previous['id']+'.json'),previous)
        value['path']=str(folder)
        write(STATE/'context.json',value)
        (STATE/'context-reset.json').unlink()
        return True

def prior_message(session, callback_id, payload):
    if callback_id<=session['cutoff_id']:return True
    if session.get('started_at') and payload.get('timestamp') is not None:
        try:return float(payload['timestamp'])/1000 < datetime.fromisoformat(session['started_at']).timestamp()
        except (TypeError,ValueError,OverflowError):return False
    return False
