"""Durable ingress debounce and generation fencing for the existing sender.

The native host owns generation creation. MCP can use, never refresh, a token.
SQLite BEGIN IMMEDIATE serializes ingestion and last-mile authorization. The
authorization commit is the handoff boundary; an already issued HTTP cannot be
recalled. No business clock, periodic business tasks, or transport is introduced.
"""
import contextlib
import contextvars
import hashlib
import json
import sqlite3
import sys
import time
import uuid
import datetime
import sql_tools as s

QUIET_SECONDS = 60
AUTHORITY = contextvars.ContextVar('message_generation', default=None)
TOOLS = {'reply_to_message', 'reply_with_highlights', 'deliver_message',
         'deliver_proactive', 'deliver_festival', 'send_image', 'workflow_fulfill', 'workflow_highlight'}


class StaleGeneration(ValueError):
    pass


def enabled():
    path = s.R / 'config/message-hooks.json'
    return path.exists() and json.loads(path.read_text()).get('enabled') is True


def tables(c):
    # Do not executescript: it commits the caller's ingress/outbox transaction.
    for sql in (
        'CREATE TABLE IF NOT EXISTS message_hook_inbound(scope TEXT, source_id TEXT, received_at REAL, handled INTEGER DEFAULT 0, PRIMARY KEY(scope,source_id))',
        'CREATE TABLE IF NOT EXISTS message_hook_generations(token TEXT PRIMARY KEY, scope TEXT, owner_type TEXT, owner_id TEXT, cursor INTEGER, created_at REAL, status TEXT)',
        'CREATE TABLE IF NOT EXISTS message_hook_deliveries(action_key TEXT PRIMARY KEY, token TEXT, stopped INTEGER DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS message_hook_audit(id INTEGER PRIMARY KEY, at REAL, kind TEXT, body TEXT)',
    ): c.execute(sql)


def scope(c):
    row = c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
    return json.loads(row[0]).get('run_id', 'live') if row else 'live'


def cursor(c):
    return c.execute("SELECT COALESCE(MAX(rowid),0) FROM messages WHERE speaker='user' AND visible=1").fetchone()[0]


def deadline(c):
    row = c.execute('SELECT MAX(received_at) FROM message_hook_inbound WHERE scope=?', (scope(c),)).fetchone()
    if __import__('family_lab_bridge').configuration() and row and row[0] is not None:
        first=c.execute('SELECT MIN(received_at) FROM message_hook_inbound WHERE scope=? AND handled=0',(scope(c),)).fetchone()[0]
        return min(row[0]+5,first+15) if first is not None else row[0]+5
    return (row[0] + QUIET_SECONDS) if row and row[0] is not None else 0


def audit(c, kind, body):
    c.execute('INSERT INTO message_hook_audit(at,kind,body) VALUES(?,?,?)', (time.time(), kind, s.encoded(body)))


def inbound(c, source_id, received_at=None):
    if not enabled() or not source_id: return
    tables(c)
    arrival = time.time()
    if received_at is not None:
        try: arrival = min(arrival, datetime.datetime.fromisoformat(received_at.replace('Z','+00:00')).timestamp())
        except (ValueError,TypeError,AttributeError): pass
    inserted = c.execute('INSERT OR IGNORE INTO message_hook_inbound(scope,source_id,received_at) VALUES(?,?,?)',
                         (scope(c), source_id, arrival)).rowcount
    if inserted:
        c.execute("UPDATE message_hook_generations SET status='stale' WHERE scope=? AND cursor<>? AND status='active'", (scope(c), cursor(c)))
        audit(c, 'inbound', {'source_id': source_id, 'cursor': cursor(c), 'quiet_until': deadline(c)})


def batch(c):
    """Called inside the existing event claimant, never creates a second queue."""
    if not enabled(): return True
    tables(c)
    for key, status in c.execute("SELECT d.action_key,d.status FROM deliveries d JOIN message_hook_deliveries h ON h.action_key=d.action_key WHERE d.action_key LIKE 'reply:%' AND d.status IN ('accepted','sent','unknown','partial','failed')").fetchall():
        delivery_status(c, key, status)
    # Adopt pending inputs on first enable; never replay completed history.
    for raw, in c.execute("SELECT payload FROM events WHERE kind='passive' AND status IN ('pending','dispatched')").fetchall():
        p = json.loads(raw)
        for source_id in p.get('source_message_ids', []) or [p.get('source_message_id')]: inbound(c, source_id)
    if time.time() < deadline(c): return False
    rows = c.execute('SELECT source_id FROM message_hook_inbound WHERE scope=? AND handled=0 ORDER BY received_at,source_id', (scope(c),)).fetchall()
    if not rows: return True
    ids = [r[0] for r in rows]
    ident = 'message-batch:' + hashlib.sha256(s.encoded([scope(c), ids]).encode()).hexdigest()[:32]
    existing = c.execute('SELECT status FROM events WHERE id=?', (ident,)).fetchone()
    if not existing:
        c.execute("UPDATE events SET status='superseded' WHERE kind='passive' AND status IN ('pending','dispatched')")
        c.execute('INSERT INTO events(id,kind,payload,status,created_at,attempts) VALUES(?,?,?,?,?,0)',
                  (ident, 'passive', s.encoded({'source_message_id': ids[-1], 'source_message_ids': ids}), 'pending', s.now()))
        audit(c, 'batch_ready', {'event_id': ident, 'source_ids': ids, 'cursor': cursor(c)})
    return True


def handled(c, event_id):
    if not enabled(): return
    tables(c)
    row = c.execute('SELECT payload FROM events WHERE id=?', (event_id,)).fetchone()
    if row:
        p = json.loads(row[0])
        for ident in p.get('source_message_ids', []) or [p.get('source_message_id')]:
            c.execute('UPDATE message_hook_inbound SET handled=1 WHERE scope=? AND source_id=?', (scope(c), ident))


def delivery_status(c, key, status):
    if not enabled() or not key.startswith('reply:') or status not in ('accepted','sent','unknown','partial','failed'): return
    event_id = key[len('reply:'):]
    handled(c, event_id)
    # A submitted response owns this batch even if its result is unknown.
    # Fresh input must not replay the original question under a new batch key.
    c.execute("UPDATE events SET status=?,completed_at=? WHERE id=? AND kind='passive' AND status<>'done'",
              ('done' if status=='sent' else 'needs_attention' if status=='failed' else 'awaiting_delivery', s.now() if status=='sent' else None, event_id))


def refresh():
    """Drain the existing verified receiver before authorizing any new part."""
    if not enabled(): return
    import wechat_test as w
    if w.config():
        for _ in range(200):
            result = w.receive(skip_controls=True)
            if result is None: return
            if result.get('callback_id'): raise StaleGeneration('待处理控制消息，暂停发送')
        raise StaleGeneration('收件积压尚未排空，暂停发送')


def begin(owner_type, owner_id):
    if not enabled(): return {'enabled': False, 'prompt': ''}
    refresh()
    with contextlib.closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('BEGIN IMMEDIATE'); tables(c)
        if time.time() < deadline(c): return {'ready': False, 'quiet_until': deadline(c)}
        token = uuid.uuid4().hex
        c.execute('INSERT INTO message_hook_generations VALUES(?,?,?,?,?,?,?)',
                  (token, scope(c), owner_type, owner_id, cursor(c), time.time(), 'active'))
        prior = []
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='test_outbox_parts'").fetchone():
            prior = [dict(zip(('action_key','body','status'), r)) for r in c.execute('''SELECT p.action_key,o.body,o.status FROM test_outbox_parts p
                JOIN test_outbox o ON o.id=p.operation_id JOIN message_hook_deliveries h ON h.action_key=p.action_key
                ORDER BY o.rowid DESC LIMIT 20''')]
        audit(c, 'generation', {'token': token, 'owner_type': owner_type, 'owner_id': owner_id, 'cursor': cursor(c)})
    __import__('family_lab_bridge').begin(token,owner_type,owner_id)
    return {'ready': True, 'token': token, 'prompt': '\n工程发送授权 generation_token=' + token +
            '。调用任何回复或交付工具必须携带此令牌；replan_required表示旧内容已拦截，本轮立即结束，等待系统合并新消息重新唤醒。' +
            '\n近期已经提交的交付分段（实际状态，accepted/unknown不可视作未发；不要盲目重复；被打断时仅重新评估尚未交付部分）：' + s.encoded(prior)}


def validate(c, token):
    tables(c)
    row = c.execute('SELECT scope,cursor,status FROM message_hook_generations WHERE token=?', (token,)).fetchone()
    if not row or row[0] != scope(c) or row[1] != cursor(c) or row[2] != 'active' or time.time() < deadline(c):
        raise StaleGeneration('有新消息或缺少本次生成授权，旧内容不能发送')


@contextlib.contextmanager
def authority(token):
    refresh()
    with contextlib.closing(sqlite3.connect(s.DB)) as c:
        validate(c, token)
    reset = AUTHORITY.set(token)
    try: yield
    finally: AUTHORITY.reset(reset)


def register(c, key):
    if not enabled(): return
    tables(c)
    if c.execute('SELECT 1 FROM message_hook_deliveries WHERE action_key=?', (key,)).fetchone(): return
    token = AUTHORITY.get()
    validate(c, token)
    c.execute('INSERT INTO message_hook_deliveries VALUES(?,?,0)', (key, token))


def register_external(c, key, origin):
    """Only explicit human replies / original interface assets use this path."""
    if not enabled(): return
    if origin not in ('operator', 'highlight_original', 'weekly_material_original'): raise ValueError('Unknown origin')
    tables(c)
    if time.time() < deadline(c): raise StaleGeneration('新消息尚未静默60秒')
    token = uuid.uuid4().hex
    c.execute('INSERT INTO message_hook_generations VALUES(?,?,?,?,?,?,?)',
              (token, scope(c), origin, key, cursor(c), time.time(), 'active'))
    c.execute('INSERT INTO message_hook_deliveries VALUES(?,?,0)', (key, token))


def draft_key(c, key):
    if not enabled(): return key
    old = c.execute('SELECT status FROM deliveries WHERE action_key=?', (key,)).fetchone()
    prefix = key + ':generation:'
    issued = c.execute('SELECT action_key FROM deliveries WHERE substr(action_key,1,?)=? AND status<>? ORDER BY rowid DESC LIMIT 1',
                       (len(prefix), prefix, 'superseded')).fetchone()
    if issued: return issued[0]
    return key + ':generation:' + AUTHORITY.get() if old and old[0] == 'superseded' and AUTHORITY.get() else key


def attempted(c, key):
    """Include reservations made BEFORE HTTP/part linkage, even after a crash."""
    prefix = scope(c) + ':' + key
    names = [prefix] + [prefix+':text:'+str(i) for i in range(1,20)] + [prefix+':image:'+str(i) for i in range(20)] + [prefix+':emoji:'+str(i) for i in range(8)]
    ids = ['dsh-'+hashlib.sha256(x.encode()).hexdigest()[:40] for x in names]
    return c.execute('SELECT 1 FROM test_outbox WHERE id IN ('+','.join('?' for _ in ids)+')', ids).fetchone() is not None


def authorize(c, key):
    if not enabled() or key.startswith('control:'): return
    tables(c)
    prefix = scope(c) + ':'
    matches = [(action, token, stopped) for action, token, stopped in c.execute('SELECT * FROM message_hook_deliveries')
               if key == prefix+action or key.startswith(prefix+action+':text:') or key.startswith(prefix+action+':image:') or key.startswith(prefix+action+':emoji:')]
    if len(matches) != 1 or matches[0][2]: raise StaleGeneration('没有有效的分段发送授权')
    validate(c, matches[0][1])
    audit(c, 'send_authorized', {'action_key': matches[0][0], 'cursor': cursor(c), 'part': key})
    return matches[0][0]


def interrupt(token, action_key=None):
    """Never retry accepted/unknown parts. Unsent stale draft stays auditable."""
    if not enabled(): return {'status': 'disabled'}
    with contextlib.closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('BEGIN IMMEDIATE'); tables(c)
        if action_key:
            row = c.execute('SELECT token FROM message_hook_deliveries WHERE action_key=?', (action_key,)).fetchone()
            token = row[0] if row else token
            c.execute('UPDATE message_hook_deliveries SET stopped=1 WHERE action_key=?', (action_key,))
            issued = attempted(c, action_key)
            c.execute("UPDATE deliveries SET status=?,source='test_wechat' WHERE action_key=?", ('blocked' if issued else 'superseded', action_key))
            if not issued and c.execute("SELECT 1 FROM sqlite_master WHERE name='proactive_receipts'").fetchone():
                receipts = c.execute('SELECT * FROM proactive_receipts WHERE delivery_key=?', (action_key,)).fetchall()
                audit(c, 'retired_unsent_draft', {'action_key':action_key, 'reservations':[list(x) for x in receipts]})
                c.execute('DELETE FROM proactive_receipts WHERE delivery_key=?', (action_key,))
        owner = c.execute('SELECT owner_type,owner_id FROM message_hook_generations WHERE token=?', (token,)).fetchone()
        task_ids = {owner[1]} if owner and owner[0] == 'task' else set()
        if action_key and c.execute("SELECT 1 FROM sqlite_master WHERE name='service_links'").fetchone():
            task_ids.update(r[0] for r in c.execute('SELECT task_id FROM service_links WHERE action_key=?', (action_key,)))
        for task_id in task_ids:
            links = c.execute("SELECT 1 FROM service_links l JOIN deliveries d ON d.action_key=l.action_key WHERE l.task_id=? AND d.status<>'superseded'", (task_id,)).fetchone()
            status = 'blocked' if links else 'deferred'
            c.execute('UPDATE service_tasks SET status=?,not_before_wall=?,attempts=MAX(0,attempts-1),result=? WHERE id=? AND status IN (\'running\',\'awaiting_delivery\')',
                      (status, deadline(c), s.encoded({'code':'new_inbound_replan','reason':'新消息使旧生成失效；原交付记录保留，已提交部分不重发。'}), task_id))
            c.execute("DELETE FROM metadata WHERE key IN ('active_service_task','native_service_content_task') AND value=?", (s.encoded(task_id),))
        audit(c, 'replan_required', {'token': token, 'action_key': action_key, 'owner': list(owner) if owner else None})
    return {'status': 'replan_required', 'blocked_by': 'message_hooks', 'reason': '旧内容已拦截；系统等待最后一条新消息满60秒后合并重新生成。'}


def defer(task_id):
    with contextlib.closing(sqlite3.connect(s.DB)) as c, c:
        tables(c)
        c.execute("UPDATE service_tasks SET status='deferred',not_before_wall=?,attempts=MAX(0,attempts-1) WHERE id=? AND status='running'", (deadline(c), task_id))
        c.execute("DELETE FROM metadata WHERE key='active_service_task' AND value=?", (s.encoded(task_id),))
    return {'status':'deferred'}


if __name__ == '__main__':
    if sys.argv[1] == 'begin': result = begin(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'defer': result = defer(sys.argv[2])
    else: raise ValueError('Unknown hook command')
    print(s.encoded(result))
