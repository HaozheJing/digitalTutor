"""Host-only replay controls. No model tool and no replacement agent loop."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path

from .domain import require, timestamp
from .runtime_config import lease_clock


def configuration(app, family):
    row = app.store.connection.execute(
        "SELECT data FROM records WHERE kind='replay_config' AND family_id=?", (family,)).fetchone()
    return json.loads(row['data']) if row else None


def configure(app, family, *, synthetic, input_sha256, mode='discrete', evaluation_protocol='continuous_open_loop'):
    require(app.strict_runtime and app.clock.shared_path and mode in {'discrete','stream'},
            'REPLAY_CONFIG', 'Replay requires strict runtime and a shared clock')
    require(app.store.family(family)['mock'] == 1, 'REAL_CHANNEL_DISABLED', 'Replay is mock only')
    with app.store.transaction():
        old = configuration(app, family)
        require(old is None, 'REPLAY_ALREADY_CONFIGURED', 'Do not overwrite an existing replay')
        return app.store.put('replay_config', 'replay:'+family, family, {
            'synthetic': synthetic, 'input_sha256': input_sha256, 'mode': mode,
            'evaluation_protocol':evaluation_protocol,
            'dispatch_paused': False, 'pause_reason': None,
            'clock_path': app.clock.shared_path, 'generation_unchanged': True})


def set_dispatch_paused(app, family, paused, reason=None):
    """Serialize with JobQueue.claim's SQLite transaction; never changes generation."""
    with app.store.transaction():
        old = app.store.get('replay_config', 'replay:'+family, family)
        return app.store.put('replay_config', old['id'], family,
            {**old, 'dispatch_paused': bool(paused), 'pause_reason': reason}, old['version'])


@contextmanager
def arrival_batch(app, family):
    set_dispatch_paused(app, family, True, 'same_timestamp_arrival_batch')
    try:
        yield
    except BaseException:
        # A partial batch must stay closed for explicit reconciliation.
        raise
    else:
        set_dispatch_paused(app, family, False)


def write_clock(path, value):
    path = Path(path)
    now = timestamp(value)
    if path.exists():
        previous = timestamp(json.loads(path.read_text(encoding='utf-8'))['now'])
        require(now >= previous, 'REPLAY_CLOCK_BACKWARDS', 'Business time cannot go backwards')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    temporary.write_text(json.dumps({'now':now.isoformat()}), encoding='utf-8')
    os.replace(temporary, path)


def wall_blockers(app, family):
    now = lease_clock(app).now()
    jobs = app.store.list('job', family)
    leased = [j['id'] for j in jobs if j['status']=='leased' and j.get('lease_until')
              and timestamp(j['lease_until']) > now]
    retry = [timestamp(j['not_before']) for j in jobs if j.get('not_before')
             and j['status'] in {'ready','waiting'} and timestamp(j['not_before']) > now]
    probe = [timestamp(w['next_probe_at']) for w in app.store.list('runtime_material_wait',family)
             if w.get('status')=='unavailable' and w.get('next_probe_at')
             and timestamp(w['next_probe_at']) > now and w.get('resume_count',0)<3]
    return {'leased':leased, 'next_wall_wakeup':min(retry+probe).isoformat() if retry+probe else None}


def next_business_time(app, family, end, external_times=()):
    """Past due values are handled at the current clock, never selected forever."""
    now = app.clock.now()
    stop = timestamp(end)
    choices = [stop] + [timestamp(v) for v in external_times if timestamp(v)>now]
    for day_offset in (0,1):
        day = now.date()+timedelta(days=day_offset)
        for hour in (0, app.service_start, app.service_end):
            candidate = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo)+timedelta(hours=hour)
            if candidate>now:
                choices.append(candidate)
    for event in app.store.list('event', family):
        if event['status'] in {'done','cancelled'}:
            continue
        for data,key in ((event.get('next_action') or {},'execute_at'),(event.get('waiting') or {},'review_at')):
            if data.get(key) and timestamp(data[key])>now:
                choices.append(timestamp(data[key]))
        grant=event.get('accepted_commitment') or {}
        if grant.get('status')=='active' and grant.get('valid_until'):
            expiration=timestamp(grant['valid_until'])+timedelta(microseconds=1)
            if expiration>now:
                choices.append(expiration)
    require(stop>=now, 'REPLAY_CLOCK_RANGE', 'End precedes the current time')
    return min(choices)


def bootstrap_history(app, family, rows):
    """Audited cold-start transcript: no fake operation, reply proof or Event."""
    require(configuration(app,family) is not None,'REPLAY_CONFIG','Host replay not configured')
    with app.store.transaction() as db:
        for row in rows:
            require(timestamp(row['occurred_at'])<=app.clock.now(), 'REPLAY_FUTURE_INPUT','Future history')
            require(row['speaker_role'] in {'teacher','family_member_unknown','parent','child'},
                    'REPLAY_HISTORY_ROLE','Unknown source role')
            existing=db.execute('SELECT 1 FROM message_index WHERE family_id=? AND external_id=?',
                                (family,row['source_id'])).fetchone()
            require(not existing,'REPLAY_HISTORY_DUPLICATE','History source already exists')
            from .storage import new_id
            mid=new_id('history')
            direction='outbound' if row['speaker_role']=='teacher' else 'inbound'
            app.store.put('message',mid,family,{'external_id':row['source_id'],'body':row['body'],
                'occurred_at':row['occurred_at'],'received_at':row['occurred_at'],
                'direction':direction,'source_kind':'historical_teacher_reference' if direction=='outbound' else 'historical_customer_reference',
                'source_metadata':row['source_metadata'], 'historical_reference':True,
                'actual_agent_delivery':False,'operation_id':None,'interpreted':True,'intents':[],
                'responsibility_scope':'cold_start_history_not_imported_as_current_obligation'})
            db.execute('INSERT INTO message_index(message_id,family_id,external_id,direction) VALUES(?,?,?,?)',
                       (mid,family,row['source_id'],direction))
