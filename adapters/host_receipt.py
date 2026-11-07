"""Host-owned registration for a single, validated service delivery.

No sending, inference, retries, or direct task-state writes happen here. The
existing finish and late-receipt reconciliation retain their business guards.
"""
import json
import sys
import agent_plan as ap
import business
import observability as audit
import sql_tools as s


DELIVERY_TOOLS = {'deliver_message', 'deliver_proactive', 'deliver_festival', 'send_image'}


def finish_task_receipt(task_id, request_id, receipt, tool_name="deliver_message"):
    """Register only a matching, persisted S01/S02/S03/S04/S05/S06/S07/S08 result, never a model claim."""
    completion = tool_name == "deliver_proactive"
    import test_mode as tm
    state = tm.state()
    rows = s.read('SELECT * FROM service_tasks WHERE id=? AND run_id=?', (task_id, state['run_id'])) if state else []
    task = rows[0] if rows else None
    active = ap.active()
    if (not task or not ((completion and ((task['skill'] in ('celebrate-lesson-completion','remind-historical-makeup','celebrate-learning-streak','support-zero-completion','prevent-lesson-skipping','welcome-back-to-reading') and task['kind'] in ('signal','action')) or (task['skill']=='rebuild-reading-rhythm' and task['kind']=='s07') or (task['skill']=='close-week-reading-gap' and task['kind']=='s08') or (task['skill'] in ('plan-weekend-makeup','weekly-learning-review') and task['kind']=='weekly'))) or (not completion and task['kind']=='s01')) or task.get('native_request_id') != request_id or
            (active and active['id'] != task_id) or task['status'] not in ('running','done','awaiting_delivery','blocked')):
        return {'status':'continue','reason':'No matching S01/S02/S03/S04/S05/S06/S07/S08 host execution'}
    previous = json.loads(task.get('result') or '{}')
    if previous.get('code') in ('human_confirmation_required','new_inbound_replan') or (
            task['status']=='blocked' and previous.get('outcome')=='blocked' and previous.get('code')!='delivery_unconfirmed'):
        return {'status':'continue','reason':'Preserve explicit task block'}
    calls = s.read("SELECT output FROM tool_audit WHERE task_id=? AND run_id=? AND name=? "
                   "AND origin IN ('model','adapter') AND status='ok' AND started_at>=? ORDER BY rowid DESC LIMIT 1",
                   (task_id, state['run_id'], tool_name, task['started_at']))
    if not calls or not isinstance(receipt,dict):return {'status':'continue','reason':'No actual delivery result'}
    actual = json.loads(calls[0]['output'] or '{}')
    if actual.get('status') != receipt.get('status'):return {'status':'continue','reason':'Receipt mismatch'}
    key = actual.get('action_key')
    if key:
        try:
            if business.original(receipt.get('action_key')) != business.original(key):
                return {'status':'continue','reason':'Delivery reference mismatch'}
        except (ValueError,TypeError):return {'status':'continue','reason':'Invalid delivery reference'}
        links = s.read('SELECT d.action_key,d.status FROM service_links l JOIN deliveries d '
                       'ON l.action_key=d.action_key WHERE l.task_id=? AND d.status<>"superseded"', (task_id,))
        if not any(row['action_key']==business.original(key) for row in links):
            return {'status':'continue','reason':'Delivery not owned by task'}
        if actual.get('status') not in ('accepted','sent','unknown','failed','partial'):
            return {'status':'continue','reason':'Delivery still requires handling'}
        # Existing finish/reconciliation derives the terminal or awaiting state
        # from every stored part. In particular, unknown never causes a resend.
        outcome = 'completed'
    elif actual.get('status') == 'skipped':
        evidence = actual.get('evidence',{}).get('status')
        outcome = 'blocked' if evidence=='unknown' else 'deferred' if evidence=='not_due' else 'no_contact'
    elif actual.get('status') == 'deferred':outcome = 'deferred'
    elif completion and actual.get('status') == 'expired':outcome = 'no_contact'
    elif completion and actual.get('status') in ('unknown','blocked'):outcome = 'blocked'
    else:return {'status':'continue','reason':'Revision or model judgment required'}
    reason = '宿主按本次实际交付结果登记：'+str(actual.get('reason') or actual.get('status'))
    return audit.invoke('host_service_task_finish',
                        {'task_id':task_id,'request_id':request_id,'tool_name':tool_name,'delivery_status':actual.get('status'),'deferral_id':actual.get('deferral_id')},
                        lambda:ap.finish(task_id,outcome,reason),origin='host')


def finish_delivery(task_id, request_id, tool_name, action_key, receipt_json=None):
    if tool_name=='deliver_message' and receipt_json:
        return finish_task_receipt(task_id,request_id,json.loads(receipt_json))
    task = ap.active()
    if tool_name=='deliver_proactive' and receipt_json:
        import test_mode as tm
        rows=s.read('SELECT skill FROM service_tasks WHERE id=? AND run_id=?',(task_id,(tm.state() or {}).get('run_id')))
        if rows and rows[0]['skill'] in ('celebrate-lesson-completion','remind-historical-makeup','celebrate-learning-streak','support-zero-completion','prevent-lesson-skipping','welcome-back-to-reading','rebuild-reading-rhythm','close-week-reading-gap','plan-weekend-makeup','weekly-learning-review'):
            return finish_task_receipt(task_id,request_id,json.loads(receipt_json),tool_name)
    if (not task or task['id'] != task_id or
            task.get('native_request_id') != request_id or
            not ap.receipt_driven(task) or tool_name not in DELIVERY_TOOLS):
        return {'status': 'continue', 'reason': 'No matching receipt-driven host task'}
    try:
        key = business.original(action_key)
    except (ValueError, TypeError):
        return {'status': 'continue', 'reason': 'Unknown delivery reference'}
    links = s.read('SELECT d.action_key,d.status FROM service_links l JOIN deliveries d '
                   'ON l.action_key=d.action_key WHERE l.task_id=?', (task_id,))
    if not any(row['action_key'] == key for row in links):
        return {'status': 'continue', 'reason': 'Delivery does not belong to this task'}
    if not links or any(row['status'] not in ('accepted', 'sent') for row in links):
        return {'status': 'continue', 'reason': 'Delivery not confirmed; preserve model handling'}
    # finish() rechecks Skills, prerequisite queries, every linked receipt and
    # active-task identity. accepted stays awaiting_delivery, never done.
    return audit.invoke('host_service_task_finish',
                        {'task_id': task_id, 'request_id': request_id,
                         'tool_name': tool_name, 'action_key': key},
                        lambda: ap.finish(task_id, 'completed',
                                          '宿主根据本任务实际交付回执登记结果。'),
                        origin='host')


if __name__ == '__main__':
    try:
        result = finish_delivery(*sys.argv[1:])
    except Exception as exc:
        # Do not make a delivered message look like a failed send. The model
        # keeps the real tool result and may perform the original finish flow.
        result = {'status': 'continue', 'error': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
