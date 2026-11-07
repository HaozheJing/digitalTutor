"""Isolated replay data, evidence-backed memory, plans and full context snapshots."""
import datetime
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

R = Path(__file__).resolve().parents[1]
DB = R / 'runtime/live.sqlite'


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def validate_day(day):
    if datetime.date.fromisoformat(day).isoformat() != day:
        raise ValueError('Use canonical YYYY-MM-DD business date')


def read(sql, args=()):
    with closing(sqlite3.connect('file:' + str(DB) + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, args)]


def memory_recall():
    return read("SELECT * FROM memories WHERE status='active' ORDER BY memory_key")


def memory_remember(key, value, source_message_id, quote):
    # Deliberately narrow v2: names stated by the speaker, not inferred child profiles.
    if key != 'speaker.self_reported_name' or not value or len(value) > 30:
        raise ValueError('Unsupported memory key or value')
    with closing(sqlite3.connect(DB)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN IMMEDIATE')
        source = connection.execute('SELECT * FROM messages WHERE source_id=? AND speaker=?', (source_message_id, 'user')).fetchone()
        self_name=re.search(r'(?:我是|我叫)'+re.escape(value)+r'(?=$|[\s，。！？、,.!?；;：:~～])',quote)
        if source is None or quote not in source['body'] or not self_name:
            raise ValueError('Memory must cite an exact self-introduction from SQL')
        previous = connection.execute("SELECT * FROM memories WHERE memory_key=? AND status='active'", (key,)).fetchone()
        if previous and previous['value'] == value:
            return {'status': 'already_remembered', 'memory': dict(previous)}
        if previous and previous['observed_at'] > source['created_at']:
            raise ValueError('An older message cannot replace newer memory')
        memory_id = 'mem-' + hashlib.sha256(encoded([key, value, source_message_id]).encode()).hexdigest()[:20]
        connection.execute("UPDATE memories SET status='superseded' WHERE memory_key=? AND status='active'", (key,))
        connection.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?)', (memory_id, key, value, source_message_id, quote, source['sha256'], source['created_at'], now(), 'active', 'self_reported:' + source['source']))
        row = dict(connection.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone())
    return {'status': 'remembered', 'memory': row, 'boundary': '说话者自报称呼；不知道是家长还是孩子，不能推断儿童档案'}


def context(day):
    validate_day(day)
    meta = {row['key']: json.loads(row['value']) for row in read('SELECT * FROM metadata')}
    payload = {
        'business_day': day, 'timezone': 'Asia/Shanghai', 'scenario': meta['scenario'],
        'identity': family_profile(),
        'long_term_memory': memory_recall(),
        'today_lessons': read('SELECT * FROM lessons WHERE business_date=?', (day,)),
        'recent_conversation': list(reversed(read('SELECT source_id,speaker,body,created_at,source,sha256 FROM messages WHERE visible=1 ORDER BY created_at DESC,source_id DESC LIMIT 60'))),
        'conversation_window': {'limit': 60, 'older_messages': 'retained in SQL; use conversation_read for exact older evidence'},
        'pending_events': read("SELECT id,kind,payload,status FROM events WHERE status!='done' ORDER BY created_at"),
        'knowledge_fulltext': read('SELECT * FROM knowledge'),
        'nonperiodic_candidates': read('SELECT * FROM plan_candidates WHERE business_date=?', (day,)),
        'saved_plan': read('SELECT * FROM daily_plans WHERE business_date=?', (day,)),
        'previous_deliveries': read('SELECT * FROM deliveries ORDER BY created_at DESC LIMIT 30'),
        'boundaries': meta['boundaries'],
        'instruction': '事实仅来自SQL。保留来源区分。真实平台消息不等于真实家长案例；知识正文不证明课程绑定或完课。读取全文，unknown不等于未完成。'
    }
    body = encoded(payload)
    digest = hashlib.sha256(body.encode()).hexdigest()
    snapshot_id = 'ctx-' + digest[:20]
    with closing(sqlite3.connect(DB)) as connection, connection:
        connection.execute('INSERT OR IGNORE INTO context_snapshots VALUES(?,?,?,?,?)', (snapshot_id, day, digest, now(), body))
    return {'snapshot_id': snapshot_id, 'sha256': digest, 'section_chars': {key: len(encoded(value)) for key, value in payload.items()}, **payload}


def check(day):
    validate_day(day)
    import test_mode
    if test_mode.state():
        if test_mode.state().get('agent_planning'):
            import s01_delivery
            return s01_delivery.check(day)
        if day != test_mode.clock().date().isoformat():
            return {'status':'unknown','reason':'not_current_business_day'}
        v=test_mode.snapshot(day)
        today=[x for x in v['lessons_as_of'] if x['scheduled_date']==day]
        return {'status':'eligible' if any(x['overdue'] for x in today) else 'not_eligible',
                'business_day':day,'evidence':today}
    rows = read('SELECT id,completed,completion_source,binding_source,as_of FROM lessons WHERE business_date=?', (day,))
    status = 'unknown' if not rows or any(x['completed'] is None for x in rows) else 'eligible' if any(x['completed'] == 0 for x in rows) else 'not_eligible'
    return {'status': status, 'evidence': rows, 'business_day': day}


def deliver(day, body):
    validate_day(day)
    import test_mode
    if test_mode.state():
        if test_mode.state().get('agent_planning'):
            import s01_delivery
            return s01_delivery.deliver(day,body)
        return {'status':'skipped','reason':'主动课程提醒未启用'}
    if not __import__('service_hours').is_open():return {'status':'skipped','reason':'每日22:00停止主动服务，不顺延至次日。'}
    # The demonstration never has a real delivery connector.
    with closing(sqlite3.connect(DB)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='contact_constraints'").fetchone() and connection.execute('SELECT 1 FROM contact_constraints WHERE day=?',(day,)).fetchone():
            return {'status':'skipped','reason':'user_contact_pause'}
        raw = connection.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone()
        gate = json.loads(raw[0]) if raw else {}
        waiting_chat = connection.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched') LIMIT 1").fetchone()
        if gate.get('active') or gate.get('until', 0) > time.time() or waiting_chat:
            connection.execute('INSERT OR REPLACE INTO task_state VALUES(?,?,?)', ('S01:' + day, 'pending', encoded({'day': day})))
            return {'status': 'deferred', 'reason': 'passive_chat_priority'}
        connection.execute('DELETE FROM task_state WHERE id=?', ('S01:' + day,))
        rows = connection.execute('SELECT completed FROM lessons WHERE business_date=?', (day,)).fetchall()
        if not rows or any(row[0] is None for row in rows):
            return {'status': 'skipped', 'reason': 'unknown'}
        if all(row[0] == 1 for row in rows):
            return {'status': 'skipped', 'reason': 'completed'}
        key = 'live-family:S01:' + day
        existing = connection.execute('SELECT body FROM deliveries WHERE action_key=?', (key,)).fetchone()
        if existing:
            return {'status': 'mock_sent', 'duplicate': True, 'body': existing[0]}
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='proactive_settings'").fetchone():
            from zoneinfo import ZoneInfo
            policy=connection.execute("SELECT body FROM proactive_settings WHERE key='contact'").fetchone()
            if policy:
                policy=json.loads(policy[0]);tz=ZoneInfo('Asia/Shanghai')
                stamps=[datetime.datetime.fromisoformat(x[0]).astimezone(tz) for x in connection.execute("SELECT created_at FROM deliveries WHERE action_key NOT LIKE 'reply:%'")]
                # S01 is classroom-essential and exempt from the optional daily
                # quota; explicit pauses, duplicate checks and quiet time remain.
                if stamps and (datetime.datetime.now(tz)-max(stamps)).total_seconds()<policy['min_gap_minutes']*60:
                    connection.execute('INSERT OR REPLACE INTO task_state VALUES(?,?,?)',('S01:'+day,'pending',encoded({'day':day,'not_before':max(stamps).timestamp()+policy['min_gap_minutes']*60})))
                    return {'status':'deferred','reason':'contact_interval'}
        connection.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)', (key, body, 'mock_sent', now(), 'mock_channel'))
    return {'status': 'mock_sent', 'action_key': key, 'body': body}


def save_plan(day, items, reason):
    validate_day(day)
    candidates = {row['id']: row for row in read('SELECT * FROM plan_candidates WHERE business_date=?', (day,))}
    if len({item['candidate_id'] for item in items}) != len(items):
        raise ValueError('Repeated candidate')
    normalized = []
    for item in items:
        row = candidates.get(item['candidate_id'])
        if row is None or not item['schedule_id'].startswith('schedule-'):
            raise ValueError('Plan must use a SQL candidate and native schedule receipt')
        normalized.append({**row, 'schedule_id': item['schedule_id']})
    value = {'business_date': day, 'items': normalized, 'reason': reason, 'memory_ids': [m['id'] for m in memory_recall()], 'profile': family_profile(), 'planned_at': now(), 'periodic_rule': '06:30每日规划与S01、周五20:00 S09、周六09:00 S10由当前任务契约确定，不重复创建'}
    with closing(sqlite3.connect(DB)) as connection, connection:
        connection.execute('INSERT OR REPLACE INTO daily_plans VALUES(?,?,?,?)', (day, now(), encoded(value), len(normalized)))
    return value


def family_profile():
    if __import__('tool_surface').enabled('identity'):
        bound=__import__('family_session').current()
        if bound:return bound['profile']
    return _source_family_profile()


def _source_family_profile():
    """Integration seam: trusted profile feed populates SQL; chat never writes it."""
    import test_mode
    if test_mode.state():
        return test_mode.snapshot()['profile']
    rows = read('SELECT * FROM profiles ORDER BY CASE source WHEN \'business_api\' THEN 0 ELSE 1 END, as_of DESC')
    result = {'family_id': 'live-family', 'child_nickname': None, 'guardian_role': 'unknown', 'fields': {}}
    for field in ['child_nickname', 'guardian_role']:
        for row in rows:
            if row[field] and (field != 'guardian_role' or row[field] != 'unknown'):
                result[field] = row[field]
                result['fields'][field] = {'source': row['source'], 'as_of': row['as_of'], 'verified': row['source'] == 'business_api'}
                break
    return result


def conversation_read(source_id):
    return read('SELECT * FROM messages WHERE source_id=? AND visible=1', (source_id,))


def memory_review(event_id, note):
    with closing(sqlite3.connect(DB)) as c, c:
        if not c.execute('SELECT 1 FROM events WHERE id=?', (event_id,)).fetchone():
            raise ValueError('Unknown event')
        c.execute('INSERT OR REPLACE INTO memory_reviews VALUES(?,?,?)', (event_id, now(), note))
    return {'event_id': event_id, 'reviewed': True, 'active_memories': memory_recall()}


def mock_reply(event_id, body, image_urls=None):
    with closing(sqlite3.connect(DB)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT kind FROM events WHERE id=?', (event_id,)).fetchone()
        if not row or row[0] != 'passive':
            raise ValueError('Only new inbound events may receive a simulated reply')
        if not __import__('service_hours').passive_open(__import__('service_hours').now(c)):
            return __import__('service_hours').defer_inbound(c,event_id)
        key = 'reply:' + event_id
        import test_mode
        mode = bool(test_mode.state())
        if mode:
            __import__('emoji_assets').check_existing(c,key)
        from message_text import parts
        try:parts(body)
        except ValueError as error:return {'status':'needs_revision','reason':str(error)}
        inserted=c.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?,?)', (key, body, 'queued' if mode else 'mock_sent', now(), 'test_wechat_pending' if mode else 'mock_channel')).rowcount
        if mode and inserted:
            import wechat_test
            wechat_test.queue_text(c,key,body)
        if image_urls:
            if not mode:
                raise ValueError('Existing highlight images require the bound test mode')
            import wechat_test
            if inserted:
                wechat_test.queue_images(c,key,image_urls)
            else:
                prior=c.execute('SELECT images FROM test_delivery_bundles WHERE action_key=?',(key,)).fetchone()
                if not prior or json.loads(prior[0])!=list(dict.fromkeys(image_urls)):
                    raise ValueError('An existing reply cannot be changed into a different image delivery')
        result = {'status': 'queued' if mode else 'mock_sent', 'action_key': key,
                  'body': c.execute('SELECT body FROM deliveries WHERE action_key=?', (key,)).fetchone()[0]}
    if mode:
        import wechat_test
        wechat_test.flush()
        result['status'] = read('SELECT status FROM deliveries WHERE action_key=?',(key,))[0]['status']
    return result


def finish_event(event_id, day):
    validate_day(day)
    with closing(sqlite3.connect(DB)) as c, c:
        row = c.execute('SELECT kind FROM events WHERE id=?', (event_id,)).fetchone()
        if row and row[0]=='passive' and not __import__('service_hours').passive_open(__import__('service_hours').now(c)):
            return __import__('service_hours').defer_inbound(c,event_id)
        review = c.execute('SELECT reviewed_at FROM memory_reviews WHERE event_id=?', (event_id,)).fetchone()
        plan = c.execute('SELECT created_at FROM daily_plans WHERE business_date=?', (day,)).fetchone()
        if not row or not review or not plan or plan[0] < review[0]:
            raise ValueError('Review memory, then save the current day plan before completing event')
        if row[0] == 'passive' and not c.execute('SELECT 1 FROM deliveries WHERE action_key=?', ('reply:' + event_id,)).fetchone():
            raise ValueError('Missing simulated reply')
        c.execute("UPDATE events SET status='done',completed_at=? WHERE id=?", (now(), event_id))
    return {'status': 'done', 'event_id': event_id, 'business_day': day}


def review_context(candidate_id):
    rows = read('SELECT * FROM plan_candidates WHERE id=?', (candidate_id,))
    if not rows:
        raise ValueError('No SQL candidate')
    candidate = rows[0]
    snapshot = context(candidate['business_date'])
    result = {'candidate_id': candidate_id, 'snapshot_id': snapshot['snapshot_id'], 'course_check': check(candidate['business_date']), 'memory_count': len(snapshot['long_term_memory']), 'channel': 'internal_only', 'source': candidate['source']}
    with closing(sqlite3.connect(DB)) as connection, connection:
        connection.execute('INSERT OR IGNORE INTO internal_executions VALUES(?,?,?)', (candidate_id, now(), encoded(result)))
    return result


def gate(mode):
    with closing(sqlite3.connect(DB)) as connection, connection:
        if mode != 'read':
            value = {'active': mode == 'start', 'until': time.time() + 120, 'owner_pid': os.getppid()}
            connection.execute('INSERT OR REPLACE INTO task_state VALUES(?,?,?)', ('chat-gate', mode, encoded(value)))
        row = connection.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone()
    return json.loads(row[0]) if row else {}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['context', 'gate'])
    parser.add_argument('--day')
    parser.add_argument('--mode', default='read')
    args = parser.parse_args()
    print(encoded(context(args.day) if args.command == 'context' else gate(args.mode)))
