"""Readonly source polling and durable work ledger. No original service imports."""
import datetime
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
import sql_tools as s


def cfg():
    return json.loads((s.R / 'config/live-source.json').read_text())


def enqueue(c, ident, kind, payload, received_at=None):
    inserted=c.execute('INSERT OR IGNORE INTO events(id,kind,payload,status,created_at,attempts) VALUES(?,?,?,?,?,0)', (ident, kind, s.encoded(payload), 'pending', s.now())).rowcount
    if inserted and kind=='passive':__import__('message_hooks').inbound(c,payload.get('source_message_id'),received_at)


def ingest(settings=None):
    settings = settings or cfg()
    pointer = json.loads(Path(settings['pointer']).read_text())
    base = Path(settings['contexts_root']).resolve()
    source_dir = Path(pointer['path']).resolve()
    if not source_dir.is_relative_to(base):
        raise ValueError('Source pointer outside readonly allowlist')
    path = source_dir / 'replyer.sqlite'
    if not path.exists():
        return {'status': 'waiting_for_source_database'}
    context_id = pointer['id']
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN IMMEDIATE')
        checkpoint = c.execute('SELECT last_rowid FROM source_checkpoints WHERE context_id=?', (context_id,)).fetchone()
        last_rowid = checkpoint[0] if checkpoint else 0
        pending = [r[0] for r in c.execute("SELECT source_rowid FROM source_seen WHERE context_id=? AND terminal=0", (context_id,))]
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
            source.row_factory = sqlite3.Row
            rows = [dict(r) for r in source.execute('SELECT rowid AS source_rowid,id,recipient,body,reply,at,status,fixture FROM turns WHERE rowid>? ORDER BY rowid LIMIT 500', (last_rowid,))]
            for start in range(0, len(pending), 400):
                ids = pending[start:start+400]
                rows.extend(dict(r) for r in source.execute('SELECT rowid AS source_rowid,id,recipient,body,reply,at,status,fixture FROM turns WHERE rowid IN (' + ','.join('?' for _ in ids) + ')', ids))
        added = 0
        batch = []
        for row in {r['source_rowid']: r for r in rows}.values():
            last_rowid = max(last_rowid, row['source_rowid'])
            if row['recipient'] != settings['recipient']:
                continue  # Single explicitly mapped family, never blend arbitrary contacts.
            ident = context_id + ':' + row['id']
            for role, body, suffix in [('user', row['body'], ''), ('assistant', row['reply'], ':reply')]:
                if not body:
                    continue
                message_id = ident + suffix
                sha = hashlib.sha256(body.encode()).hexdigest()
                existed = c.execute('SELECT 1 FROM messages WHERE source_id=?', (message_id,)).fetchone()
                if not existed:
                    provenance = settings.get('message_source', 'real_platform_test_chat') if role == 'user' else 'historical_model_reply'
                    c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)', (message_id, role, body, row['at'], provenance, sha))
                    added += 1
                    if role == 'user':
                        is_old = datetime.datetime.fromisoformat(row['at'].replace('Z', '+00:00')) < datetime.datetime.fromisoformat(settings['activated_at'])
                        if is_old:
                            batch.append(message_id)
                        else:
                            enqueue(c, 'inbound:' + ident, 'passive', {'source_message_id': message_id, 'source_context': context_id, 'source': provenance})
            c.execute('INSERT OR REPLACE INTO source_seen VALUES(?,?,?,?)', (context_id, row['source_rowid'], row['status'] == 'sent', row['status']))
        if batch:
            enqueue(c, 'backfill:' + hashlib.sha256(s.encoded(batch).encode()).hexdigest()[:24], 'memory_plan', {'source_message_ids': batch, 'no_historical_reply': True})
        c.execute('INSERT OR REPLACE INTO source_checkpoints VALUES(?,?,?,?)', (context_id, last_rowid, s.now(), s.encoded({'added': added, 'scanned': len(rows)})))
    return {'status': 'ok', 'context_id': context_id, 'last_rowid': last_rowid, 'added_messages': added, 'scanned_rows': len(rows)}


def claim(allow_internal=True):
    memory=__import__('preference_memory')
    allow_memory=memory.enabled() and memory.settings().get('slow_enabled') is True
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN IMMEDIATE')
        hours=__import__('service_hours')
        opened=hours.enforce(c)['open']
        passive_allowed=opened or hours.night_passive_enabled()
        # The DB has one family and one mutable active-task binding. Serialize all consumers.
        if c.execute("SELECT 1 FROM events WHERE status='dispatched' LIMIT 1").fetchone():return None
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='service_tasks'").fetchone() and c.execute("SELECT 1 FROM service_tasks WHERE status='running' LIMIT 1").fetchone():return None
        if passive_allowed and not __import__('message_hooks').batch(c):return None
        row = c.execute("SELECT * FROM events WHERE status='pending' AND attempts<3 AND (? OR kind='passive') AND (? OR kind<>'memory_consolidation') AND (? OR kind='memory_consolidation' OR (? AND kind='passive')) AND (retry_after IS NULL OR retry_after<?) ORDER BY CASE kind WHEN 'passive' THEN 0 ELSE 1 END,created_at LIMIT 1", (allow_internal,allow_memory,opened,passive_allowed,s.now())).fetchone()
        if not row:
            return None
        if row['kind'] != 'passive' and c.execute("SELECT 1 FROM events WHERE status='dispatched' LIMIT 1").fetchone():
            return None
        # Internal refresh waits behind any unfinished chat and the quiet window.
        gate = c.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone()
        value = json.loads(gate[0]) if gate else {}
        import time
        if row['kind'] != 'passive' and (value.get('active') or value.get('until', 0) > time.time() or c.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched') LIMIT 1").fetchone()):
            return None
        c.execute("UPDATE events SET status='dispatched',attempts=attempts+1,dispatched_at=? WHERE id=?", (s.now(), row['id']))
        return dict(row)


def recover():
    with closing(sqlite3.connect(s.DB)) as c, c:
        __import__('service_hours').defer_interrupted_inbound(c)
        c.execute("UPDATE events SET status=CASE WHEN attempts>=3 THEN 'needs_attention' ELSE 'pending' END WHERE status='dispatched'")
        row = c.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone()
        if row:
            gate = json.loads(row[0])
            if gate.get('active'):
                import time
                gate.update(active=False, until=max(gate.get('until', 0), time.time()+120))
                c.execute("UPDATE task_state SET status='recovered',payload=? WHERE id='chat-gate'", (s.encoded(gate),))
    return {'recovered': True}


def retry(ident):
    with closing(sqlite3.connect(s.DB)) as c, c:
        __import__('service_hours').defer_interrupted_inbound(c,ident)
        later = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)).isoformat()
        c.execute("UPDATE events SET status=CASE WHEN attempts>=3 THEN 'needs_attention' ELSE 'pending' END,retry_after=? WHERE id=? AND status='dispatched'", (later, ident))
    return {'retry_recorded': ident}


def status():
    import test_mode
    return {'source': s.read('SELECT * FROM source_checkpoints'), 'events': s.read('SELECT id,kind,status,attempts,completed_at FROM events ORDER BY created_at'), 'memory_count': len(s.memory_recall()), 'profile': s.family_profile(), 'delivery_mode': 'bound_test_wechat' if test_mode.state() else 'mock_only', 'needs_attention': s.read("SELECT id FROM events WHERE status='needs_attention'")}


def pending_s01():
    return s.read("SELECT * FROM task_state WHERE id LIKE 'S01:%' AND status='pending'")


def scheduled_s01(ident):
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute("UPDATE task_state SET status='scheduled' WHERE id=? AND status='pending'", (ident,))
    return {'scheduled': ident}


if __name__ == '__main__':
    commands = {'ingest': ingest, 'claim': lambda:claim(len(sys.argv)<3 or sys.argv[2]!='passive'), 'recover': recover, 'status': status, 'retry': lambda: retry(sys.argv[2]), 'pending-s01': pending_s01, 'scheduled-s01': lambda: scheduled_s01(sys.argv[2])}
    print(s.encoded(commands[sys.argv[1]]()))
