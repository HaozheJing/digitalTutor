"""Shared read-only execution semantics. No scheduler, sender or authority issuer."""
import json
import re

INTERNAL = frozenset({'plan', 'completion_plan', 'case_review'})


def validate_body(body, authorities=()):
    if not isinstance(body, str):
        return  # The domain validator reports the missing/invalid body.
    if re.search(r'(?i)(?:generation_token|execution_token)\s*[:=]', body) or any(
            isinstance(token, str) and len(token) >= 8 and token in body for token in authorities):
        raise ValueError('internal_authority_in_body_remove_it_and_retry')


def delivery_task_status(deliveries):
    """Project persisted transport states only; never decide to retry a send."""
    items = [d for d in deliveries if d.get('status') != 'superseded']
    if not items:
        return None
    if all(d['status'] == 'sent' for d in items):
        return 'done'
    if all(d['status'] in ('queued', 'accepted', 'sent') or
           (d['status'] == 'partial' and d.get('source') == 'test_wechat_pending') for d in items):
        return 'awaiting_delivery'
    return 'blocked'


def task_projection(c, task):
    deliveries = [dict(zip(('action_key', 'status', 'source'), row)) for row in c.execute(
        'SELECT d.action_key,d.status,d.source FROM service_links l JOIN deliveries d '
        'ON d.action_key=l.action_key WHERE l.task_id=? AND d.status<>? ORDER BY d.action_key',
        (task['id'], 'superseded'))]
    result = task.get('result') or {}
    if isinstance(result, str):
        result = json.loads(result)
    state = task['status']
    internal = task.get('kind') in INTERNAL
    transport = delivery_task_status(deliveries)
    human = result.get('code') == 'human_confirmation_required'
    waiting = ('human_review' if human else 'delivery_receipt' if transport == 'awaiting_delivery'
               else 'delivery_verification' if transport == 'blocked'
               else 'existing_schedule' if state in ('planned', 'deferred') else None)
    return {'recorded_state': state, 'completion_scope': 'internal_task' if internal else 'service_delivery',
            'completion_confirmed': state == 'done' and (internal or transport == 'done'),
            'business_issue_resolved': 'not_established', 'deliveries': deliveries,
            'waiting_for': waiting,
            'wakeup': {'kind': 'existing_task_due', 'at': task.get('due_at')}
                      if state in ('planned', 'deferred') and not deliveries and not human else None,
            'retry_delivery': False if deliveries or human else None,
            'guidance': '按原事项与回执继续；等待条件仅描述既有状态，不授权创建跟进或改变联系节奏。'}


def handoff_projection(value):
    state = value.get('status', 'unknown')
    return {'state': state, 'scope': 'operator_handoff',
            'handling_complete': state in ('sent', 'dismissed'),
            'delivery_confirmed': value.get('delivery_status') == 'sent',
            'business_issue_resolved': 'not_established', 'auto_resume_original': False,
            'next_action': 'wait_for_operator' if state == 'pending' else
                           'verify_existing_delivery' if state not in ('sent', 'dismissed') else
                           'await_new_event',
            'guidance': '人工回复送达或撤回仅结束本次转交，不证明问题解决；不重跑原消息或原任务。'}


def handoffs(c, state):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='operator_alerts'").fetchone():
        return []
    rows = c.execute('SELECT a.id,a.event_id,a.task_id,a.status,d.status FROM operator_alerts a '
                     'LEFT JOIN deliveries d ON d.action_key=a.action_key WHERE a.run_id=? AND a.uid=? '
                     'ORDER BY a.created_at,a.id', (state['run_id'], state['uid'])).fetchall()
    return [{'alert_id': ident, 'event_id': event, 'task_id': task,
             **handoff_projection({'status': delivery or status, 'delivery_status': delivery})}
            for ident, event, task, status, delivery in rows]


def fulfillment_receipt(c, receipt):
    delivery = c.execute('SELECT status FROM deliveries WHERE action_key=?', (receipt['action_key'],)).fetchone()
    task = c.execute('SELECT status FROM service_tasks WHERE id=?', (receipt['task_id'],)).fetchone()
    return {**receipt, 'delivery': delivery[0] if delivery else 'unknown',
            'task_status': task[0] if task else 'unknown', 'retry_delivery': False}


def context_projection(value):
    """Join provider lesson numbers to the catalog without aliasing provider IDs."""
    import sql_tools as s
    from query_contract import schedule_coordinates
    learning=value.get('facts',{}).get('learning',{})
    profile=learning.get('profile',{})
    catalog=s.R/'knowledge/curriculum/L2/catalog.json'
    lessons=json.loads(catalog.read_text())['lessons'] if catalog.is_file() else []
    result=schedule_coordinates(value,profile.get('class_type'),lessons)
    result['knowledge_reference_policy']={
        'provider_book_id_is_curriculum_book_id':False,
        'guidance':'学情提供器的books[].id和课程库book_id属于不同编号体系，不能混用。优先用课节curriculum_ref中的level/week/day读取知识；没有正式映射时先knowledge_browse查目录，不猜book_id。'}
    return result
