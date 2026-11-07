"""Human confirmation ledger; replies use the existing bound WeChat outbox.

The model may transfer its current work to an operator, never pick a recipient
or submit an operator reply. An accepted send is immutable, including when the
provider has not confirmed its result. Read projections do not migrate or write
the database, so archived runs remain read-only.
"""
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import sql_tools as s


CATEGORIES = ('needs_confirmation', 'missing_information', 'unsupported_request',
              'delivery_problem', 'other')
OUTBOUND_TOOLS = frozenset(('reply_to_message', 'reply_with_highlights',
                            'deliver_message', 'deliver_proactive', 'deliver_festival', 'send_image',
                            'mock_reply', 'send_mock', 'workflow_fulfill', 'workflow_highlight'))


def _writable():
    if any(part.lower() == 'test-archives' for part in Path(s.DB).resolve().parts):
        raise ValueError('历史轮次仅供查看，不能创建提醒或发送消息')


def initialize():
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS operator_alerts(
          id TEXT PRIMARY KEY,run_id TEXT NOT NULL,uid TEXT NOT NULL,
          context_key TEXT NOT NULL,event_id TEXT,task_id TEXT,category TEXT NOT NULL,
          reason TEXT NOT NULL,proposed_reply TEXT NOT NULL,source_message TEXT,
          task TEXT,status TEXT NOT NULL,revision INTEGER NOT NULL,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
          action_key TEXT,submitted_body TEXT,request_id TEXT,resolution_reason TEXT,
          UNIQUE(run_id,context_key));
        CREATE INDEX IF NOT EXISTS operator_alerts_run ON operator_alerts(run_id,created_at);
        ''')


def _table(c, name):
    return bool(c.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone())


def _state(c, expected_run_id=None):
    row = c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
    state = json.loads(row[0]) if row else None
    if not state or not state.get('run_id') or not state.get('uid'):
        raise ValueError('当前没有已绑定家庭的工作轮次')
    if expected_run_id is not None and state['run_id'] != expected_run_id:
        raise ValueError('工作轮次已变化，请刷新；历史提醒仅供查看')
    return state


def _text(value, label, maximum, optional=False):
    if not isinstance(value, str) or (not value.strip() and not optional):
        raise ValueError(label + '不能为空')
    value = value.strip()
    if len(value) > maximum or any(ord(char) < 32 and char not in '\r\n\t' for char in value):
        raise ValueError(label + '过长或含不可发送的控制字符')
    return value


def _delivery(c, action_key):
    if not action_key or not _table(c, 'deliveries'):
        return None
    row = c.execute('SELECT * FROM deliveries WHERE action_key=?', (action_key,)).fetchone()
    return dict(row) if row else None


def _project(c, row):
    value = dict(row)
    for field in ('source_message', 'task'):
        value[field] = json.loads(value[field]) if value[field] else None
    delivery = _delivery(c, value['action_key'])
    delivery_status = delivery['status'] if delivery else None
    value['delivery_status'] = delivery_status
    value['status'] = delivery_status or value['status']
    value['resolved'] = value['status'] in ('sent', 'dismissed')
    pending = value['status'] == 'pending' and not value['action_key']
    internal = bool(value['task'] and value['task']['kind'] in ('plan', 'completion_plan', 'case_review'))
    value['can_send'] = pending and not internal
    value['can_dismiss'] = pending
    value['blocked_reason'] = (None if value['can_send'] else
        '内部规划或资料检查事项，不对家庭发送；处理后可撤回提醒并保留原任务记录' if internal and pending else
        '已确认送达' if value['status'] == 'sent' else
        '已撤回人工回复' if value['status'] == 'dismissed' else
        '已提交发送，正文已冻结；请核对最终回执，不可重复发送')
    value['recipient'] = '当前绑定微信家庭'
    value['handling'] = __import__('execution_contract').handoff_projection(value)
    return value


def list_alerts():
    """Project only this database's current run; no writes, even on archives."""
    with closing(sqlite3.connect('file:' + str(s.DB) + '?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        state = _state(c)
        if not _table(c, 'operator_alerts'):
            return {'run_id': state['run_id'], 'items': [], 'pending_count': 0,
                    'model_outbound_paused': False}
        items = [_project(c, row) for row in c.execute(
            'SELECT * FROM operator_alerts WHERE run_id=? AND uid=? ORDER BY created_at DESC,id',
            (state['run_id'], state['uid']))]
    pending = sum(not item['resolved'] for item in items)
    return {'run_id': state['run_id'], 'items': items, 'pending_count': pending,
            'model_outbound_paused': bool(pending)}


def request_alert(reason, proposed_reply='', event_id=None, task_id=None,
                  category='needs_confirmation'):
    """Transfer the currently executing event or service task to a human."""
    _writable()
    reason = _text(reason, '转人工原因', 2000)
    proposed_reply = _text(proposed_reply, '拟回复', 2000, optional=True)
    if category not in CATEGORIES or bool(event_id) == bool(task_id):
        raise ValueError('请选择有效原因分类，并仅关联当前消息事件或服务任务')
    initialize()
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN IMMEDIATE')
        state = _state(c)
        context_key = ('event:' + event_id) if event_id else ('task:' + task_id)
        prior = c.execute('SELECT * FROM operator_alerts WHERE run_id=? AND context_key=?',
                          (state['run_id'], context_key)).fetchone()
        if prior:
            if (prior['reason'], prior['proposed_reply'], prior['category']) != (reason, proposed_reply, category):
                raise ValueError('该事项已有人工确认提醒，请沿用；不能覆盖人工处理记录')
            return {'status': 'human_confirmation_required', 'duplicate': True,
                    'alert': _project(c, prior), 'instruction': '已转人工，请勿再次向家庭发送本事项消息'}
        source_message, task = None, None
        if event_id:
            event = c.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
            if not event or event['kind'] != 'passive' or event['status'] != 'dispatched':
                raise ValueError('只能转交当前正在处理的微信消息事件')
            source_id = json.loads(event['payload']).get('source_message_id')
            source = c.execute('SELECT * FROM messages WHERE source_id=? AND speaker=? AND visible=1',
                               (source_id, 'user')).fetchone()
            if not source:
                raise ValueError('当前消息原文不存在或已不属于当前上下文')
            source_message = {key: source[key] for key in ('source_id', 'body', 'created_at', 'source')}
            if _delivery(c, 'reply:' + event_id):
                raise ValueError('此消息已有交付申请，请先核对原回执；不能转人工重复回复')
        else:
            active = c.execute("SELECT value FROM metadata WHERE key='active_service_task'").fetchone()
            task = c.execute('SELECT * FROM service_tasks WHERE id=? AND run_id=? AND status=?',
                             (task_id, state['run_id'], 'running')).fetchone() if _table(c, 'service_tasks') else None
            if not active or json.loads(active[0]) != task_id or not task:
                raise ValueError('只能转交当前正在执行的服务任务')
            if _table(c, 'service_links') and c.execute('SELECT 1 FROM service_links WHERE task_id=?', (task_id,)).fetchone():
                raise ValueError('此任务已有交付申请，请先核对原回执；不能另行发送')
            task = {key: task[key] for key in ('id', 'day', 'kind', 'title', 'skill', 'candidate_id', 'reason')}
        ident = 'alert-' + hashlib.sha256(s.encoded([state['run_id'], context_key]).encode()).hexdigest()[:32]
        stamp = s.now()
        c.execute('''INSERT INTO operator_alerts(id,run_id,uid,context_key,event_id,task_id,category,
          reason,proposed_reply,source_message,task,status,revision,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (ident, state['run_id'], state['uid'], context_key, event_id, task_id, category,
           reason, proposed_reply, s.encoded(source_message) if source_message else None,
           s.encoded(task) if task else None, 'pending', 1, stamp, stamp))
        if event_id:
            c.execute("UPDATE events SET status='needs_attention' WHERE id=?", (event_id,))
        else:
            result = {'code': 'human_confirmation_required', 'reason': reason, 'alert_id': ident}
            c.execute("UPDATE service_tasks SET status='blocked',finished_at=?,result=? WHERE id=?",
                      (stamp, s.encoded(result), task_id))
            c.execute("DELETE FROM metadata WHERE key='active_service_task' AND value=?", (s.encoded(task_id),))
        row = c.execute('SELECT * FROM operator_alerts WHERE id=?', (ident,)).fetchone()
        return {'status': 'human_confirmation_required', 'alert': _project(c, row),
                'instruction': '已转人工；本轮未解决提醒期间暂停模型对外发送，人工确认后由工作台处理'}


def guard_model_delivery(name, args):
    """This guard also covers native tail calls after active() has been cleared."""
    if name not in OUTBOUND_TOOLS:
        return
    with closing(sqlite3.connect('file:' + str(s.DB) + '?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        if not _table(c, 'operator_alerts'):
            return
        state = _state(c)
        rows = c.execute('SELECT * FROM operator_alerts WHERE run_id=? AND uid=?',
                         (state['run_id'], state['uid'])).fetchall()
        event_id = args.get('event_id')
        native = c.execute("SELECT value FROM metadata WHERE key='native_service_content_task'").fetchone()
        native_id = json.loads(native[0]) if native else None
        for row in rows:
            value = _project(c, row)
            if (not value['resolved'] or (event_id and row['event_id'] == event_id)
                    or (native_id and row['task_id'] == native_id)):
                raise ValueError('该家庭有待人工确认的提醒，或本事项已交由人工；模型不得继续对外发送')


def _alert(c, expected_run_id, alert_id):
    state = _state(c, expected_run_id)
    row = c.execute('SELECT * FROM operator_alerts WHERE id=? AND run_id=? AND uid=?',
                    (alert_id, state['run_id'], state['uid'])).fetchone()
    if not row:
        raise ValueError('提醒不存在或属于历史轮次；请刷新当前工作台')
    return state, row


def send_reply(expected_run_id, alert_id, body, revision, request_id):
    """Operator-only entry point. Queue once, then use verified transport receipts."""
    _writable()
    body = _text(body, '回复正文', 304)
    from message_text import parts
    parts(body)
    if type(revision) is not int or revision < 1:
        raise ValueError('提醒版本无效，请刷新')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request_id):
        raise ValueError('发送请求编号无效')
    import wechat_test as w
    if not w.config():
        raise ValueError('当前未配置绑定微信发送通道')
    # Reuse the established allowlist; callers never supply a recipient or route.
    w.provider_config()
    initialize()
    w.initialize()
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN IMMEDIATE')
        state, row = _alert(c, expected_run_id, alert_id)
        if row['request_id']:
            if row['request_id'] != request_id or row['submitted_body'] != body:
                raise ValueError('此提醒已提交发送且正文已冻结，不可再次发送或更改正文')
            return {'status': _project(c, row)['status'], 'duplicate': True,
                    'alert': _project(c, row)}
        if row['revision'] != revision or row['status'] != 'pending':
            raise ValueError('提醒已被处理或版本已变化，请刷新')
        if row['task'] and json.loads(row['task'])['kind'] in ('plan', 'completion_plan', 'case_review'):
            raise ValueError('内部规划或资料检查事项不能转为对客消息')
        if _table(c, 'events') and c.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched')").fetchone():
            raise ValueError('有新的家庭消息待处理，请先核对原话后再发送')
        # Human consent is scoped to this item, not arbitrary unsolicited outreach.
        # Passive replies may answer a family while its proactive reminders pause.
        if not row['event_id']:
            if _table(c, 'contact_constraints') and c.execute('SELECT 1 FROM contact_constraints WHERE day=?',
                    (state['as_of'][:10],)).fetchone():
                raise ValueError('家庭已暂停当日主动联系；此任务不能人工补发')
            task = c.execute('SELECT * FROM service_tasks WHERE id=? AND run_id=?',
                             (row['task_id'], state['run_id'])).fetchone()
            result = json.loads(task['result'] or '{}') if task else {}
            if (not task or task['status'] != 'blocked' or result.get('alert_id') != alert_id
                    or result.get('code') != 'human_confirmation_required'):
                raise ValueError('原服务任务已变化，请刷新核对')
            if task['day'] != state['as_of'][:10] or (task['expires_at'] and task['expires_at'] < state['as_of']):
                raise ValueError('原服务任务已过期，不能补发旧消息')
        if row['event_id']:
            event = c.execute('SELECT * FROM events WHERE id=?', (row['event_id'],)).fetchone()
            source = json.loads(row['source_message'])
            if (not event or event['kind'] != 'passive' or event['status'] != 'needs_attention'
                    or json.loads(event['payload']).get('source_message_id') != source['source_id']):
                raise ValueError('原消息事件已变化，不能发送旧回复')
            current = c.execute('SELECT body FROM messages WHERE source_id=? AND visible=1 AND speaker=?',
                                (source['source_id'], 'user')).fetchone()
            if not current or current[0] != source['body']:
                raise ValueError('原消息上下文已变化，请重新核对后处理')
        key = 'reply:' + row['event_id'] if row['event_id'] else 'operator:' + alert_id
        if _delivery(c, key) or (row['task_id'] and _table(c, 'service_links')
                and c.execute('SELECT 1 FROM service_links WHERE task_id=?', (row['task_id'],)).fetchone()):
            raise ValueError('原事项已有其他交付申请，请核对回执，不能重复发送')
        stamp = s.now()
        c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)', (key, body, 'queued', stamp, 'test_wechat_pending'))
        __import__('message_hooks').register_external(c,key,'operator')
        w.queue_text(c, key, body)
        if row['task_id']:
            c.execute('INSERT OR IGNORE INTO service_links VALUES(?,?)', (row['task_id'], key))
        c.execute('''UPDATE operator_alerts SET status='queued',revision=revision+1,updated_at=?,
                     action_key=?,submitted_body=?,request_id=? WHERE id=?''',
                  (stamp, key, body, request_id, alert_id))
    # flush is the sole physical sending path. Its durable outbox never retries
    # an unknown result, and callbacks reconcile the same delivery in place.
    w.flush()
    # A rejection before durable outbox reservation is safe to re-review, unlike unknown.
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('SELECT action_key FROM operator_alerts WHERE id=? AND request_id=?',(alert_id,request_id)).fetchone()
        if current and current[0]:
            action=current[0]
            delivery=c.execute('SELECT status FROM deliveries WHERE action_key=?',(action,)).fetchone()
            if delivery and delivery[0] in ('blocked','superseded') and not __import__('message_hooks').attempted(c,action):
                for table in ('service_links','test_delivery_texts','test_delivery_bundles','message_hook_deliveries'):
                    if _table(c,table):c.execute('DELETE FROM '+table+' WHERE action_key=?',(action,))
                c.execute('DELETE FROM deliveries WHERE action_key=?',(action,))
                c.execute("UPDATE operator_alerts SET status='pending',revision=revision+1,action_key=NULL,submitted_body=NULL,request_id=NULL,updated_at=? WHERE id=?",(s.now(),alert_id))
    reconcile()
    with closing(sqlite3.connect(s.DB)) as c:
        c.row_factory = sqlite3.Row
        _state(c, expected_run_id)
        row = c.execute('SELECT * FROM operator_alerts WHERE id=?', (alert_id,)).fetchone()
        value = _project(c, row)
        return {'status': value['status'], 'alert': value}


def reconcile():
    """Finish only source items with a final sent receipt; never enqueue sends."""
    _writable()
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.row_factory = sqlite3.Row
        if not _table(c, 'operator_alerts'):
            return
        state = _state(c)
        for row in c.execute('SELECT * FROM operator_alerts WHERE run_id=? AND uid=? AND action_key IS NOT NULL',
                             (state['run_id'], state['uid'])).fetchall():
            delivery = _delivery(c, row['action_key'])
            if not delivery or delivery['status'] != 'sent':
                continue
            if row['event_id']:
                c.execute("UPDATE events SET status='done',completed_at=? WHERE id=? AND status='needs_attention'",
                          (s.now(), row['event_id']))
                __import__('message_hooks').handled(c,row['event_id'])
            if row['task_id']:
                # A sent manual reply resolves this handoff, not an internal
                # planning job whose non-messaging obligations are unfinished.
                task = json.loads(row['task'])
                if task['kind'] not in ('plan', 'completion_plan', 'case_review'):
                    c.execute("UPDATE service_tasks SET status='done',finished_at=?,result=? WHERE id=? AND run_id=? AND status='blocked'",
                              (s.now(), s.encoded({'outcome': 'completed', 'code': 'human_reply_sent',
                               'alert_id': row['id'], 'deliveries': [{'action_key': row['action_key'], 'status': 'sent'}]}),
                               row['task_id'], state['run_id']))


def dismiss(expected_run_id, alert_id, revision, reason):
    _writable()
    reason = _text(reason, '处理说明', 2000)
    if type(revision) is not int or revision < 1:
        raise ValueError('提醒版本无效，请刷新')
    initialize()
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN IMMEDIATE')
        state, row = _alert(c, expected_run_id, alert_id)
        if row['revision'] != revision or row['status'] != 'pending' or row['action_key']:
            raise ValueError('仅可撤回尚未提交发送的提醒；已提交事项须核对回执')
        c.execute("UPDATE operator_alerts SET status='dismissed',revision=revision+1,updated_at=?,resolution_reason=? WHERE id=?",
                  (s.now(), reason, alert_id))
        # Do not return ownership to a model tail call or retry an old message.
        if row['event_id']:
            c.execute("UPDATE events SET status='done',completed_at=? WHERE id=? AND status='needs_attention'",
                      (s.now(), row['event_id']))
            __import__('message_hooks').handled(c,row['event_id'])
        row = c.execute('SELECT * FROM operator_alerts WHERE id=?', (alert_id,)).fetchone()
        return {'status': 'dismissed', 'alert': _project(c, row)}


if __name__ == '__main__':
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else 'list'
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        if command == 'list':
            result = list_alerts()
        elif command == 'reply':
            result = send_reply(**args)
        elif command == 'dismiss':
            result = dismiss(**args)
        elif command == 'reconcile':
            reconcile()
            result = list_alerts()
        else:
            raise ValueError('工作台操作不存在')
    except ValueError as error:
        result = {'status': 'rejected', 'error': str(error)}
    print(s.encoded(result))


def authorize_part(c, key):
    """Validate a persisted human approval; never infer authority from caller origin."""
    if not _table(c,'operator_alerts'):return False
    state=_state(c)
    rows=c.execute("SELECT id,task_id,event_id,action_key,submitted_body,request_id FROM operator_alerts WHERE run_id=? AND uid=? AND request_id IS NOT NULL",(state['run_id'],state['uid'])).fetchall()
    for ident,task_id,event_id,action,body,request in rows:
        prefix=state['run_id']+':'+action
        if key!=prefix and not key.startswith(prefix+':text:'):continue
        delivery=c.execute('SELECT body FROM deliveries WHERE action_key=?',(action,)).fetchone()
        if not delivery or delivery[0]!=body:raise ValueError('人工审核正文已变化')
        if c.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched')").fetchone():raise ValueError('新消息需要重新核验人工答复')
        if task_id:
            task=c.execute('SELECT status,result,expires_at,day,skill FROM service_tasks WHERE id=? AND run_id=?',(task_id,state['run_id'])).fetchone()
            if not task or task[0]!='blocked' or json.loads(task[1] or '{}').get('alert_id')!=ident:raise ValueError('人工事项授权已失效')
            if task[3]!=state['as_of'][:10] or task[2]<state['as_of']:raise ValueError('人工事项已过期')
            if _table(c,'contact_constraints') and c.execute('SELECT 1 FROM contact_constraints WHERE day=?',(state['as_of'][:10],)).fetchone():raise ValueError('主动联系已暂停')
            wf=__import__('service_workflow')
            if wf.enabled() and wf.constraint_blocks(c,state,task[4],task_id):raise ValueError('事项已暂停')
        return True
    return False
