"""Daily outbound window. Expire proactive work; retain inbound for fresh morning handling."""
import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from zoneinfo import ZoneInfo
import sql_tools as s

TZ=ZoneInfo('Asia/Shanghai')
OPEN=dt.time(6,30)
CLOSE=dt.time(22)


class ClosedServiceHours(ValueError):
    pass


def stamp(value):
    value=dt.datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)


def now(c=None):
    state=None
    if c is not None and table(c,'metadata'):
        row=c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
        state=json.loads(row[0]) if row else None
    elif c is None:
        state=__import__('test_mode').state()
    # A real-clock turn can outlive its frozen preparation facts. Check wall time.
    return stamp(state['as_of']) if state and state.get('as_of') and state.get('frozen',True) else dt.datetime.now(TZ)


def is_open(at=None):
    at=stamp(at) if at is not None else now()
    return OPEN<=at.time().replace(tzinfo=None)<CLOSE


def night_passive_enabled():
    from family_lab_bridge import configuration
    cfg=configuration()
    if not cfg:return False
    from family_lab import night_passive_binding
    from pathlib import Path
    with closing(sqlite3.connect(Path(cfg['control_db']).resolve().as_uri()+'?mode=ro',uri=True)) as control:
        return night_passive_binding(control,cfg['run_id'],cfg['family_id'])


def passive_open(at=None):
    return is_open(at) or night_passive_enabled()


def next_open(at=None):
    at=stamp(at) if at is not None else now()
    day=at.date()+(dt.timedelta(days=1) if at.time().replace(tzinfo=None)>=CLOSE else dt.timedelta())
    return dt.datetime.combine(day,OPEN,TZ).isoformat(timespec='seconds')


def task_expiry(due,expires):
    close=dt.datetime.combine(stamp(due).date(),dt.time(21,59,59),TZ)
    return min(stamp(expires),close).isoformat(timespec='seconds')


def policy():
    return {'timezone':'Asia/Shanghai','opens_at':'06:30','closes_at':'22:00','close_is_exclusive':True,
            'unfinished_proactive':'expire_without_next_day_replay','night_inbound':'retain_and_process_after_0630',
            'note':'仅控制面向家庭的自动外发；内部夜间记忆和已提交交付的回执仍可处理。'}


def table(c,name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())


def limit_candidates(items):
    fixed_events={'celebrate-lesson-completion','remind-historical-makeup','celebrate-learning-streak','prevent-lesson-skipping','welcome-back-to-reading'}
    for item in items:
        window=item.get('time_window')
        if not window:continue
        first=stamp(window['earliest']);last=stamp(window['latest'])
        close=dt.datetime.combine(first.date(),dt.time(21,59,59),TZ)
        if item.get('rule',item.get('skill')) in fixed_events and not is_open(first):
            if item.get('status')=='eligible':item.update(status='expired',reason='触发时点在每日06:30—22:00服务时间外，结束本次主动事项，不顺延。')
        else:
            first=max(first,dt.datetime.combine(first.date(),OPEN,TZ))
        last=min(last,close)
        window.update(earliest=first.isoformat(timespec='seconds'),latest=last.isoformat(timespec='seconds'))
        if first>last and item.get('status')=='eligible':item.update(status='expired',reason='本次事项已无可服务窗口，不顺延至次日。')
    return items


def defer_inbound(c,event_id):
    """No reply was queued: keep the original inbox item for a fresh morning turn."""
    c.execute("UPDATE events SET status='waiting_service_hours',attempts=MAX(0,attempts-1),retry_after=NULL WHERE id=? AND kind='passive' AND status IN ('pending','dispatched')",(event_id,))
    return {'status':'deferred','reason':'夜间只接收消息，次日06:30重新读取并回复；本轮结束，不发送。','retry_at':next_open(now(c))}



def defer_interrupted_inbound(c,event_id=None):
    """A turn interrupted after closing must not exhaust the morning retry budget."""
    if passive_open(now(c)):return []
    enforce(c)
    held=[]
    query="SELECT id FROM events WHERE kind='passive' AND status='dispatched'"
    for ident, in c.execute(query+(" AND id=?" if event_id else ''),(event_id,) if event_id else ()).fetchall():
        key='reply:'+ident
        row=c.execute('SELECT status FROM deliveries WHERE action_key=?',(key,)).fetchone()
        if (row and row[0] not in ('queued','superseded')) or issued(c,key):continue
        defer_inbound(c,ident);held.append(ident)
    return held


def human_delivery(c,key):
    return table(c,'operator_alerts') and bool(c.execute('SELECT 1 FROM operator_alerts WHERE action_key=?',(key,)).fetchone())


def issued(c,key):
    if not table(c,'test_outbox'):return False
    # Includes a reservation made before HTTP, even if part linkage was interrupted.
    return __import__('message_hooks').attempted(c,key) or (table(c,'test_outbox_parts') and bool(c.execute('SELECT 1 FROM test_outbox_parts WHERE action_key=?',(key,)).fetchone()))


def register_draft(c,key):
    c.execute('CREATE TABLE IF NOT EXISTS service_hours_drafts(run_id TEXT NOT NULL,action_key TEXT NOT NULL,business_at TEXT NOT NULL,PRIMARY KEY(run_id,action_key))')
    c.execute('INSERT OR IGNORE INTO service_hours_drafts VALUES(?,?,?)',(__import__('message_hooks').scope(c),key,now(c).isoformat()))


def stop_delivery(c,key,at=None):
    at=at or now(c)
    if human_delivery(c,key):return {'status':'human_owned'}
    row=c.execute('SELECT status FROM deliveries WHERE action_key=?',(key,)).fetchone()
    if not row:return {'status':'absent'}
    submitted=row[0] in ('accepted','sent','unknown','partial','failed') or issued(c,key)
    c.execute("UPDATE deliveries SET status=?,source='test_wechat' WHERE action_key=?",('blocked' if submitted else 'superseded',key))
    if table(c,'message_hook_deliveries'):c.execute('UPDATE message_hook_deliveries SET stopped=1 WHERE action_key=?',(key,))
    if key.startswith('reply:') and submitted:__import__('message_hooks').delivery_status(c,key,'unknown')
    if key.startswith('reply:') and not submitted:
        original=key[len('reply:'):]
        event=c.execute("SELECT payload FROM events WHERE id=? AND kind='passive'",(original,)).fetchone()
        if event:
            target=next_open(at);ident='service-hours-inbound:'+hashlib.sha256((original+'|'+target).encode()).hexdigest()[:32]
            payload=json.loads(event[0]);payload['service_hours']={'original_event_id':original,'resume_at':target,'reason':'unsent_draft_discarded'}
            c.execute("UPDATE events SET status='superseded' WHERE id=?",(original,))
            c.execute("INSERT OR IGNORE INTO events(id,kind,payload,status,created_at,attempts) VALUES(?,?,?,?,?,0)",
                      (ident,'passive',s.encoded(payload),'pending' if passive_open(at) else 'waiting_service_hours',s.now()))
    if table(c,'service_links') and table(c,'service_tasks'):
        for tid, in c.execute('SELECT task_id FROM service_links WHERE action_key=?',(key,)).fetchall():
            result={'code':'service_hours_closed','reason':'服务截止，未提交部分不补发；保留已提交部分回执。','delivery_key':key,'submitted_parts':submitted}
            c.execute("UPDATE service_tasks SET status=?,finished_at=?,result=? WHERE id=? AND status NOT IN ('done','cancelled','expired')",('blocked' if submitted else 'expired',s.now(),s.encoded(result),tid))
            c.execute("DELETE FROM metadata WHERE key IN ('active_service_task','native_service_content_task') AND value=?",(s.encoded(tid),))
    return {'status':'blocked' if submitted else 'expired','action_key':key,'reason':'每日22:00停止自动外发；未交付主动事项不顺延。'}


def enforce(c,at=None):
    at=stamp(at) if at is not None else now(c);opened=is_open(at);ended=[]
    cfg,lab=__import__('family_lab_bridge').connection()
    if cfg:
        binding=lab.read('SELECT mode FROM bindings WHERE run_id=? AND family_id=?',(cfg['run_id'],cfg['family_id']))[0]
        if binding['mode']=='wechat':opened=opened and is_open(dt.datetime.now(TZ))
    passive_allowed=opened or night_passive_enabled()
    if table(c,'events'):
        if passive_allowed:c.execute("UPDATE events SET status='pending',retry_after=NULL WHERE kind='passive' AND status='waiting_service_hours'")
        else:c.execute("UPDATE events SET status='waiting_service_hours' WHERE kind='passive' AND status='pending'")
    if table(c,'deliveries'):
        for key,created in c.execute("SELECT action_key,created_at FROM deliveries WHERE source='test_wechat_pending'").fetchall():
            draft=c.execute('SELECT business_at FROM service_hours_drafts WHERE run_id=? AND action_key=?',(__import__('message_hooks').scope(c),key)).fetchone() if table(c,'service_hours_drafts') else None
            stale=(draft[0][:10]<at.date().isoformat()) if draft else (not key.startswith('reply:') and created[:10]<at.date().isoformat())
            if stale or not (passive_allowed if key.startswith('reply:') else opened):stop_delivery(c,key,at)
    if table(c,'service_tasks'):
        for tid,kind,due,expires in c.execute("SELECT id,kind,due_at,expires_at FROM service_tasks WHERE status IN ('planned','deferred','running')").fetchall():
            due_at=stamp(due)
            expired=not is_open(due_at) or due_at.date()<at.date() or (not opened and at.time().replace(tzinfo=None)>=CLOSE and due_at.date()==at.date()) or stamp(expires)<at
            if not expired:continue
            # Submitted deliveries keep their reconciliation path, even overnight.
            linked=table(c,'service_links') and c.execute('SELECT 1 FROM service_links l JOIN deliveries d ON d.action_key=l.action_key WHERE l.task_id=? AND d.status<>"superseded"',(tid,)).fetchone()
            if linked:continue
            c.execute("UPDATE service_tasks SET status='expired',finished_at=?,result=? WHERE id=?",(s.now(),s.encoded({'code':'service_hours_closed','reason':'服务窗口结束，未交付主动事项到期结束，不顺延至次日。'}),tid))
            if table(c,'service_task_versions'):c.execute('UPDATE service_task_versions SET execution_token=NULL WHERE task_id=?',(tid,))
            c.execute("DELETE FROM metadata WHERE key IN ('active_service_task','native_service_content_task') AND value=?",(s.encoded(tid),));ended.append(tid)
    if table(c,'proactive_pending'):
        for ident,retry,expires in c.execute("SELECT id,retry_at,expires_at FROM proactive_pending WHERE status IN ('pending','scheduled')").fetchall():
            if not is_open(stamp(retry)) or stamp(retry).date()<at.date() or (not opened and at.time().replace(tzinfo=None)>=CLOSE and stamp(retry).date()==at.date()) or stamp(expires)<at:
                c.execute("UPDATE proactive_pending SET status='expired' WHERE id=?",(ident,))
    return {'open':opened,'expired_task_ids':ended,'policy':policy()}


def tick():
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');return enforce(c)


if __name__=='__main__':print(s.encoded(tick()))
