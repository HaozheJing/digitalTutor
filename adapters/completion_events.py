"""Durable completion inbox. Adapters supply facts; this module never runs a model.

The mock adapter is deliberately run-scoped. A real adapter must resolve identity
and fetch authoritative learning data before calling this same normalized contract.
"""
import copy
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
import sql_tools as s
import test_mode as tm

FIELDS = {'schema_version', 'event_id', 'event_type', 'uid', 'enrollment_id',
          'lesson_id', 'completion_id', 'completed_at'}


def initialize():
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS completion_events(
          run_id TEXT, source TEXT, event_id TEXT, uid TEXT, enrollment_id TEXT,
          lesson_id TEXT, completion_id TEXT, completed_at TEXT, received_at TEXT,
          received_business_at TEXT, payload TEXT,
          PRIMARY KEY(run_id,source,event_id),
          UNIQUE(run_id,uid,enrollment_id,lesson_id),
          UNIQUE(run_id,source,completion_id));
        ''')


def saved(state, connection=None):
    def read(c):
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='completion_events'").fetchone():
            return []
        cur = c.execute('SELECT * FROM completion_events WHERE run_id=? ORDER BY completed_at,event_id',
                        (state['run_id'],))
        return [dict(zip([col[0] for col in cur.description], row)) for row in cur.fetchall()]
    if connection is not None:
        return read(connection)
    with closing(sqlite3.connect(s.DB)) as c:
        return read(c)


def enrollment(data, lesson):
    matches = [e for e in data['enrollments'] if e['id'] == lesson.get('enrollment_id')]
    if not matches and len(data['enrollments']) == 1:
        matches = data['enrollments']
    if len(matches) != 1:
        raise ValueError('课节缺少明确的报名记录，不能推断所属课程')
    return matches[0]


def canonical(payload):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError('完课事件字段不完整或包含未支持字段')
    if type(payload['schema_version']) is not int or payload['schema_version'] != 1 or payload['event_type'] != 'lesson.completed':
        raise ValueError('仅支持第一版 lesson.completed 事件')
    for key in FIELDS - {'schema_version', 'completed_at', 'event_type'}:
        if not isinstance(payload[key], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', payload[key]):
            raise ValueError('事件标识格式无效：' + key)
    try:
        stamp = dt.datetime.fromisoformat(payload['completed_at'])
        if stamp.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('完课时间必须是包含时区的有效时间') from None
    return {**payload, 'completed_at': stamp.astimezone(tm.TZ).isoformat(timespec='seconds')}


def fixture_records(state, data):
    result = []
    for lesson in data['learning_daily']:
        stamp = lesson.get('completed_at')
        if not stamp or stamp > state['as_of']:
            continue
        e = enrollment(data, lesson)
        ident = 'fixture-' + hashlib.sha256((e['id'] + ':' + lesson['id']).encode()).hexdigest()[:24]
        result.append({'schema_version': 1, 'event_type': 'lesson.completed', 'event_id': ident,
                       'completion_id': ident, 'uid': data['uid'], 'enrollment_id': e['id'],
                       'lesson_id': lesson['id'], 'completed_at': stamp, 'source': 'fixture'})
    return result


def records(state, data, connection=None):
    stored = saved(state, connection)
    # A simulation replaces the fixture's future completion of that same lesson.
    keys = {(r['enrollment_id'], r['lesson_id']) for r in stored}
    return sorted([r for r in stored if r['completed_at'] <= state['as_of']] +
                  [r for r in fixture_records(state, data) if (r['enrollment_id'], r['lesson_id']) not in keys],
                  key=lambda r: (r['completed_at'], r['event_id']))


def overlay(data, state):
    """Copy-on-read projection; never modifies the operator's fixture file."""
    rows = saved(state)
    if not rows:
        return data
    data = copy.deepcopy(data)
    for event in rows:
        if event['source'] == 'fixture':
            continue
        lesson = next((l for l in data['learning_daily'] if l['id'] == event['lesson_id']), None)
        if lesson is None:
            continue
        lesson['completed_at'] = event['completed_at']
        if not lesson.get('attended_at') or lesson['attended_at'] > event['completed_at']:
            lesson['attended_at'] = event['completed_at']
        data['learning_events'] = [e for e in data['learning_events']
                                   if not (e.get('lesson_id') == lesson['id'] and e['kind'] == 'completion')]
        data['learning_events'].append({'id': event['event_id'], 'kind': 'completion',
            'uid': event['uid'], 'lesson_id': lesson['id'], 'occurred_at': event['completed_at'],
            'source': 'workbench_completion'})
    return data


def accept(payload, run_id, source='mock-course-system'):
    """Authenticated mock ingress; acknowledge only after commit, without sending."""
    payload = canonical(payload)
    state = tm.state()
    from completion_sources import resolve
    data = resolve(source, payload, run_id, state)
    stamp = payload['completed_at']
    initialize()
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        current = json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
        if current != state:
            raise ValueError('测试状态已变化，请刷新后再操作')
        from console_control import idle
        if not __import__('family_lab_bridge').configuration():idle(c)
        old = saved(state, c)
        exact = next((r for r in old if r['source'] == source and r['event_id'] == payload['event_id']), None)
        if exact:
            if json.loads(exact['payload']) != payload:
                raise ValueError('同一事件编号的内容发生冲突，请核对课程记录')
            return {'status': 'duplicate', 'event_id': exact['event_id'], 'completed_at': exact['completed_at']}
        used_id = next((r for r in old if r['source'] == source and r['completion_id'] == payload['completion_id']), None)
        if used_id and (used_id['lesson_id'] != payload['lesson_id'] or used_id['enrollment_id'] != payload['enrollment_id']):
            raise ValueError('完课记录编号已用于其他课节')
        previous = next((r for r in records(state, data, c)
                         if r['lesson_id'] == payload['lesson_id'] and r['enrollment_id'] == payload['enrollment_id']), None)
        if previous:
            return {'status': 'duplicate', 'event_id': previous['event_id'], 'completed_at': previous['completed_at'],
                    'reason': '该课已完成，保留首次完成事实，不重复安排事项'}
        c.execute('INSERT INTO completion_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (run_id, source, payload['event_id'], payload['uid'], payload['enrollment_id'],
                   payload['lesson_id'], payload['completion_id'], stamp, s.now(), state['as_of'], s.encoded(payload)))
    return {'status': 'recorded', 'event_id': payload['event_id'], 'completed_at': stamp,
            'notice': '完课事实已保存；事项由宿主调度，消息尚未发送'}


def simulate(data):
    state = tm.state()
    raw = tm.fixture_data(state, include_events=False)
    lesson = next((l for l in raw['learning_daily'] if l['id'] == data.get('lesson_id')), None)
    if not lesson:
        raise ValueError('请选择当前课表中的课节')
    return accept({'schema_version': 1, 'event_type': 'lesson.completed', 'event_id': data.get('event_id'),
                   'completion_id': data.get('event_id'), 'uid': state['uid'],
                   'enrollment_id': enrollment(raw, lesson)['id'], 'lesson_id': lesson['id'],
                   'completed_at': data.get('completed_at')}, data.get('run_id'), 'workbench')


def ingest_fixture():
    state = tm.state()
    if not state:
        return
    initialize()
    data = tm.fixture_data(state, include_events=False)
    with closing(sqlite3.connect(s.DB)) as c, c:
        for record in fixture_records(state, data):
            payload = {k: record[k] for k in FIELDS}
            c.execute('INSERT OR IGNORE INTO completion_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (state['run_id'], 'fixture', record['event_id'], record['uid'], record['enrollment_id'],
                       record['lesson_id'], record['completion_id'], record['completed_at'], s.now(), state['as_of'], s.encoded(payload)))


def view():
    state = tm.state()
    data = tm.fixture_data(state, include_events=False)
    rows = records(state, data)
    return {'records': list(reversed(rows)), 'mode': 'mock', 'uid': state['uid'],
            'enrollments': data['enrollments'], 'run_id': state['run_id']}


if __name__ == '__main__':
    try:
        data = json.loads(sys.argv[2])
        result = simulate(data) if sys.argv[1] == 'simulate' else accept(data.get('event'), data.get('run_id'),data.get('trusted_source') if sys.argv[1]=='external' else 'mock-course-system')
        if result['status'] in ('recorded', 'duplicate'):
            # Retry-safe: a crash here leaves a committed inbox record; the host
            # poller will derive the missing task on its next synchronization.
            import agent_plan
            agent_plan.sync()
    except (ValueError, TypeError, KeyError) as error:
        result = {'status': 'rejected', 'error': str(error)}
    print(s.encoded(result))
