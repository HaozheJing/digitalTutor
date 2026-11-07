"""Explicit operator controls; no model calls or transport operations."""
import json
import sqlite3
import sys
import uuid
import time
from contextlib import closing
from pathlib import Path
import sql_tools as s
import test_mode as tm


def idle(c, receipt_complete=False):
    checks=[
        ('events', "status IN ('pending','dispatched')", '微信消息仍在处理中，请等待完成后再操作'),
        ('service_tasks', "status='running'", '服务任务仍在执行，请等待完成后再操作'),
        ('deliveries', "status IN ('queued','unknown','partial')", '仍有待发送或结果待确认的微信消息，请先核对发送回执再操作'),
        ('test_outbox', "status IN ('queued','sending','unknown')", '仍有待发送或结果待确认的微信消息，请先核对发送回执再操作')]
    if receipt_complete:
        checks.extend((table,clause,'微信平台已受理但尚未确认送达，请等待最终回执后再切换或重测') for table,clause in [
            ('deliveries',"status='accepted'"),('test_outbox',"status='accepted'"),
            ('service_tasks',"status='awaiting_delivery'")])
    for table, clause, reason in checks:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            params=()
            if table=='service_tasks':
                # Task rows are retained across runs for inspection. They cannot
                # block the current run; physical outbox checks remain global.
                row=c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
                run_id=json.loads(row[0]).get('run_id') if row else None
                if not run_id:continue
                clause+=' AND run_id=?';params=(run_id,)
            if c.execute('SELECT 1 FROM '+table+' WHERE '+clause,params).fetchone():
                raise ValueError(reason)


def clear_context():
    """Archive first; preserve business receipts/plans and pause future execution."""
    ident=uuid.uuid4().hex
    archive=Path(s.DB).parent/'test-archives'/(ident+'.sqlite')
    archive.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(s.DB)) as c:
        idle(c)
        with closing(sqlite3.connect(archive)) as dest:c.backup(dest)
        with c:
            c.execute('BEGIN IMMEDIATE');idle(c)
            c.execute('UPDATE messages SET visible=0')
            c.execute("UPDATE memories SET status='archived' WHERE status='active'")
            state=tm.state();state['auto_highlights']=False
            state.pop('manual_service_task_id',None)
            state['context_reset_at']=s.now()
            state['context_delivery_rowid']=c.execute('SELECT COALESCE(MAX(rowid),0) FROM deliveries').fetchone()[0]
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='service_skill_reads'").fetchone():
                c.execute('DELETE FROM service_skill_reads WHERE run_id=?',(state['run_id'],))
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
            c.execute("DELETE FROM metadata WHERE key='active_service_task'")
            c.execute("DELETE FROM task_state WHERE id='chat-gate'")
    return {'status':'context_cleared','archive_id':ident,'as_of':state['as_of'],
            'run_id':state['run_id'],'auto_highlights':False}


def execute(task_id):
    """Authorize exactly one existing, due task, without enabling automatic mode."""
    state=tm.state()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE');idle(c)
        row=c.execute('SELECT * FROM service_tasks WHERE id=? AND run_id=? AND day=?',
                      (task_id,state['run_id'],state['as_of'][:10])).fetchone()
        if not row or row['status'] not in ('planned','deferred'):
            raise ValueError('任务已完成、已失效或不属于当前轮次')
        if not row['due_at']<=state['as_of']<=row['expires_at'] or row['attempts']>=3 or row['not_before_wall']>time.time():
            raise ValueError('任务尚未到执行时点或已停止重试')
        if state.get('task_scope') and row['kind'] not in state['task_scope']:
            raise ValueError('此轮测试范围不包含该动作，请开启新轮次')
        if state.get('manual_service_task_id'):
            raise ValueError('已有手动任务待执行，请等待完成')
        state['manual_service_task_id']=task_id
        state['auto_highlights']=False
        c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
    return {'status':'queued','task_id':task_id,'auto_highlights':False}


if __name__=='__main__':
    try:
        value=clear_context() if sys.argv[1]=='clear-context' else execute(sys.argv[2])
    except ValueError as e:value={'status':'rejected','error':str(e)}
    print(s.encoded(value))
