"""Deliver current-day interface assets verbatim, without model or image generation."""
import hashlib
import json
import sqlite3
import time
import datetime as dt
from contextlib import closing
from urllib.parse import urlsplit
import sql_tools as s
import test_mode as tm


def queue():
    import proactive
    state = tm.state()
    if not state:
        return {'status': 'disabled'}
    if __import__('service_workflow').enabled(state):
        return {'status':'blocked','reason':'高光需先由模型规划对应事项，再调用绑定高光交付'}
    # This is the existing time-bounded highlight interface, never the forecast.
    available = tm.highlight_lookup()['highlights']
    day = state['as_of'][:10]
    available = [h for h in available if h['candidate_at'][:10] == day]
    proactive.refresh(day)
    results = []
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        current = json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
        if (current['run_id'], current['as_of']) != (state['run_id'], state['as_of']):
            return {'status': 'deferred', 'reason': '业务时间已变化，等待重新查询高光'}
        if c.execute('SELECT 1 FROM contact_constraints WHERE day=?', (day,)).fetchone():
            return {'status': 'blocked', 'reason': '用户要求今天暂停主动联系'}
        gate = c.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone()
        gate = json.loads(gate[0]) if gate else {}
        if gate.get('active') or gate.get('until', 0) > time.time() or c.execute(
                "SELECT 1 FROM events WHERE status IN ('pending','dispatched')").fetchone():
            return {'status': 'deferred', 'reason': '当前对话结束后直接发送已有高光',
                    'retry_at': (dt.datetime.fromisoformat(state['as_of']) + dt.timedelta(seconds=max(1,gate.get('until',0)-time.time()))).isoformat()}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'operator_alerts' in tables and c.execute(
                "SELECT 1 FROM operator_alerts WHERE run_id=? AND status IN ('pending','queued','accepted','unknown','partial','failed')",
                (state['run_id'],)).fetchone():
            return {'status': 'blocked', 'reason': '当前有待人工处理的提醒，暂停自动外发'}
        for h in available:
            dedupe = state['run_id'] + ':' + h['dedupe_key']
            prior = c.execute('SELECT d.action_key,d.status FROM proactive_receipts r LEFT JOIN deliveries d '
                              'ON d.action_key=r.delivery_key WHERE r.dedupe_key=?', (dedupe,)).fetchone()
            if prior:
                results.append({'highlight_id': h['id'], 'action_key': prior[0], 'status': prior[1] or 'unknown', 'duplicate': True})
                continue
            body, image = h.get('share_txt'), h.get('img_url')
            url = urlsplit(image) if isinstance(image, str) else None
            if not isinstance(body, str) or not body.strip() or not url or url.scheme != 'https' or url.hostname != 'coolcdn.igetcool.com' or url.username or url.fragment:
                results.append({'highlight_id': h['id'], 'status': 'blocked', 'reason': '接口缺少原始文案或可信图片，不生成替代内容'})
                continue
            key = 'highlight:' + hashlib.sha256(dedupe.encode()).hexdigest()[:32]
            candidate = 'proactive:fixture:' + state['run_id'] + ':' + h['id']
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)', (key, body, 'queued', state['as_of'], 'test_wechat_pending'))
            # Interface-authored caption is sent intact, independent of model prose limits.
            import wechat_test
            __import__('message_hooks').register_external(c,key,'highlight_original')
            wechat_test.queue_images(c, key, [image])
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)', (dedupe, candidate, key, state['as_of']))
            c.execute("UPDATE proactive_candidates SET status='delivered' WHERE id=?", (candidate,))
            results.append({'highlight_id': h['id'], 'action_key': key, 'status': 'queued', 'duplicate': False})
    return {'status': 'queued' if any(not r.get('duplicate') and r['status'] == 'queued' for r in results) else 'blocked' if any(r['status'] == 'blocked' for r in results) else 'idle',
            'items': results, 'reason': '当天已触发高光使用接口原文和原图交付；已有回执不重发'}


def tick():
    state = tm.state()
    if __import__('service_workflow').enabled(state):
        return {'status':'model_owned','reason':'新模式仅通过已知事实规划对应事项，不轮询生成高光任务'}
    if not state or not state.get('auto_highlights'):
        return {'status': 'manual', 'reason': '自动执行已暂停'}
    visible = [h for h in tm.highlight_lookup()['highlights'] if h['candidate_at'][:10] == state['as_of'][:10]]
    if not any(not s.read('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?',
                         (state['run_id'] + ':' + h['dedupe_key'],)) for h in visible):
        return {'status': 'idle', 'reason': '没有新的当日已触发高光'}
    import observability
    return observability.invoke('host_highlights', {}, queue, origin='scheduler')


def execute(task_id):
    """Run an already claimed, model-planned original-card task without an LLM."""
    import agent_plan as ap,service_workflow as wf,observability as audit
    task=ap.active()
    if not task or task['id']!=task_id or task['kind']!='highlight':
        row=s.read('SELECT status FROM service_tasks WHERE id=? AND run_id=?',(task_id,tm.state()['run_id']))
        return {'status':row[0]['status'] if row else 'not_active','duplicate':True}
    try:
        wf.prepare_execution(task_id)
        result=audit.invoke('host_highlight_delivery',{'task_id':task_id},wf.highlight,origin='host')
    except Exception as exc:
        result={'status':'blocked','reason':str(exc)}
    with closing(sqlite3.connect(s.DB)) as c,c:
        wf.reconcile(c)
        deferred=wf.defer_pending_contact(c,task_id)
        if deferred:return deferred
        links=c.execute('SELECT d.action_key,d.status FROM service_links l JOIN deliveries d ON d.action_key=l.action_key WHERE l.task_id=? AND d.status<>"superseded"',(task_id,)).fetchall()
        statuses=[x[1] for x in links]
        status=('done' if all(x=='sent' for x in statuses) else 'awaiting_delivery' if all(x in ('queued','accepted','sent') for x in statuses) else 'blocked') if links else 'no_contact' if result.get('status')=='no_contact' else 'blocked'
        summary={'host_owned':True,'reason':result.get('reason','沿用外部接口原文原图，结果以逐片回执为准'),'deliveries':[{'action_key':k,'status':v} for k,v in links]}
        c.execute('UPDATE service_tasks SET status=?,result=?,finished_at=? WHERE id=?',(status,s.encoded(summary),None if status=='awaiting_delivery' else s.now(),task_id))
        c.execute("DELETE FROM metadata WHERE key IN ('active_service_task','native_service_content_task') AND value=?",(s.encoded(task_id),))
    return {'status':status,**summary}


if __name__ == '__main__':
    import sys
    import observability
    observability.initialize()
    print(s.encoded(execute(sys.argv[2]) if len(sys.argv)>2 and sys.argv[1]=='execute' else tick() if len(sys.argv) > 1 and sys.argv[1] == 'tick' else observability.invoke('host_highlights', {}, queue, origin='operator')))
