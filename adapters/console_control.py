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


def family_boundary(c, state, request):
    """Validate ownership and take a consistent outgoing-message cutoff for this family."""
    import family_lab_bridge as bridge
    cfg, lab = bridge.connection()
    if (not cfg or cfg['run_id'] != request.get('lab_run_id')
            or state['run_id'] != request.get('run_id') or state['uid'] != request.get('family_id')):
        raise ValueError('家庭或轮次已变化，请刷新')
    scope = (cfg['run_id'], cfg['family_id'])
    with closing(sqlite3.connect(Path(cfg['control_db']).resolve().as_uri()+'?mode=ro', uri=True)) as control:
        control.execute('BEGIN')
        f = control.execute("""SELECT busy, inbox_seq, projected_seq FROM families JOIN runs
            ON runs.id=families.run_id WHERE runs.active=1 AND run_id=? AND family_id=?""", scope).fetchone()
        if not f or f[0] or f[1] != f[2] or f[1] != request.get('expected_seq'):
            raise ValueError('有新来信或消息正在处理，请刷新后再试')
        for table, condition, reason in [
            ('model_attempts',"status IN ('queued','running')",'请等当前模型回复完成'),
            ('outbox',"status IN ('queued','submitting','accepted','unknown')",'请先等当前消息取得最终回执'),
            ('cases',"status='open'",'请先处理当前家庭的待办人工事项')]:
            if control.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND family_id=? AND '+condition+' LIMIT 1', scope).fetchone():
                raise ValueError(reason)
        return control.execute('SELECT COALESCE(MAX(rowid),0) FROM outbox WHERE run_id=? AND family_id=?',scope).fetchone()[0]


def clear_context(request=None):
    import re
    request = request or {}
    scoped = 'lab_run_id' in request
    ident = request.get('request_id') if scoped else uuid.uuid4().hex
    if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9-]{32,36}',ident):
        raise ValueError('清理请求编号无效')
    archive = Path(s.DB).parent/'test-archives'/(ident+'.sqlite')
    archive.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        state = json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
        result = {'status':'context_cleared','archive_id':ident,'context_id':ident,
                  'as_of':state['as_of'],'run_id':state['run_id']}
        if scoped:
            if request.get('family_id')!=state['uid'] or request.get('run_id')!=state['run_id']:
                raise ValueError('家庭或轮次已变化，请刷新')
            if state.get('conversation_id') == ident:
                return {**result, 'replayed':True}
            if request.get('expected_context') != state.get('conversation_id',''):
                raise ValueError('会话已经切换，请刷新')
            state['conversation_outbox_cursor'] = family_boundary(c, state, request)
        idle(c, receipt_complete=scoped)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if scoped and c.execute("SELECT 1 FROM events WHERE kind='passive' AND status='waiting_service_hours' LIMIT 1").fetchone():
            raise ValueError('有尚未处理的来信，请处理完后再清理')
        # The writer lock keeps the backup and session boundary on the same revision.
        with closing(sqlite3.connect(s.DB)) as source, closing(sqlite3.connect(archive)) as dest:
            source.backup(dest)
        archive.chmod(0o600)
        c.execute('UPDATE messages SET visible=0 WHERE visible=1')
        c.execute("UPDATE memories SET status='archived' WHERE status='active'")
        if 'message_hook_inbound' in tables:
            c.execute("UPDATE message_hook_inbound SET handled=1 WHERE handled=0 AND source_id IN (SELECT source_id FROM messages WHERE visible=0)")
        if 'memory_candidates' in tables:
            c.execute("UPDATE memory_candidates SET status='archived' WHERE status IN ('candidate','reported','confirmed')")
        if 'memory_checkpoints' in tables:
            c.execute("UPDATE memory_checkpoints SET seq=COALESCE((SELECT MAX(seq) FROM memory_journal j WHERE j.scope=memory_checkpoints.scope),seq)")
        if not scoped:
            state['auto_highlights']=False
            state.pop('manual_service_task_id',None)
        if 'test_outbox' in tables:
            state['context_part_rowid']=c.execute('SELECT COALESCE(MAX(rowid),0) FROM test_outbox').fetchone()[0]
        state.update(conversation_id=ident, context_reset_at=s.now(),
                     context_delivery_rowid=c.execute('SELECT COALESCE(MAX(rowid),0) FROM deliveries').fetchone()[0])
        if 'service_skill_reads' in tables:
            c.execute('DELETE FROM service_skill_reads WHERE run_id=?',(state['run_id'],))
        c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        c.execute("DELETE FROM metadata WHERE key='active_service_task'")
        c.execute("DELETE FROM task_state WHERE id='chat-gate'")
    return {**result,'auto_highlights':state.get('auto_highlights',False)}


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
        value=clear_context(json.loads(sys.stdin.read() or '{}') if '--request' in sys.argv else {}) if sys.argv[1]=='clear-context' else execute(sys.argv[2])
    except ValueError as e:value={'status':'rejected','error':str(e)}
    print(s.encoded(value))
