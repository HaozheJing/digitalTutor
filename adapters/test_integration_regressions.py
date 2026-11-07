from pathlib import Path
import sys,unittest,json,sqlite3,threading,concurrent.futures
from contextlib import closing
from unittest.mock import patch
root=Path(__file__).parent

import test_service_workflow as base
import sql_tools as s,agent_plan as ap,service_workflow as wf,test_mode as tm

class Audit(base.WorkflowTests):
 def test_operator_cannot_reply_to_workflow_handoff(self):
  import operator_alerts as alerts,wechat_test
  tid=self.new_task();self.start_action(tid)
  alert=alerts.request_alert('Human needs to approve this task',task_id=tid)['alert']
  self.assertTrue(alert['can_send'])
  with patch.object(wechat_test,'http_send',return_value={'errcode':0,'requestId':'audit-only'}) as send:
   result=alerts.send_reply(tm.state()['run_id'],alert['id'],'人工核实后的回复。',alert['revision'],'audit-operator-01')
  current=alerts.list_alerts()['items'][0]
  print('WORKFLOW_HUMAN_SEND_DEADEND',json.dumps({'initial_can_send':alert['can_send'],'result_status':result['status'],'http_called':send.called,'can_send_after':current['can_send'],'can_dismiss_after':current['can_dismiss'],'outbox_count':len(s.read('SELECT * FROM test_outbox'))}))
  self.assertTrue(send.called);self.assertEqual(result['status'],'accepted')
  self.assertFalse(current['can_send']);self.assertFalse(current['can_dismiss'])

 def test_workflow_fulfill_bypasses_human_pause(self):
  import operator_alerts as alerts,live_runtime,mcp_server,wechat_test
  tid=self.new_task();self.dialogue()
  alert=alerts.request_alert('Human must review this matter',event_id='batch')['alert']
  with closing(sqlite3.connect(s.DB)) as c,c:
   c.execute("INSERT INTO messages VALUES('new-question','user','请把阅读安排发我',?,'fixture','hash-new',1)",(s.now(),))
   live_runtime.enqueue(c,'new-event','passive',{'source_message_id':'new-question'})
   c.execute("UPDATE events SET status='dispatched' WHERE id='new-event'")
  ctx=wf.context();task=next(x for x in ctx['tasks'] if x['id']==tid)
  with self.assertRaises(ValueError):alerts.guard_model_delivery('reply_to_message',{'event_id':'new-event'})
  with patch.object(ap,'require_skills'),patch.object(wechat_test,'flush') as flush:
   with self.assertRaises(ap.ServiceValidationError):result=mcp_server.business_call('workflow_fulfill',{'request_id':'audit-fulfill-01','context_token':ctx['context_token'],'task_id':tid,'revision':task['revision'],'body':'这是安排。','evidence':'服务完整覆盖'})
  self.assertTrue(alerts.list_alerts()['model_outbound_paused'])
  self.assertFalse(flush.called)
  self.assertEqual(s.read('SELECT * FROM deliveries'),[])

 def test_explicit_business_block_lost(self):
  tid=self.new_task();self.start_action(tid)
  with closing(sqlite3.connect(s.DB)) as c,c:
   wf.bind_delivery(c,'outstanding',tid)
   c.execute("INSERT INTO deliveries VALUES('outstanding','body','accepted',?,'test_wechat')",(s.now(),))
  result=ap.finish(tid,'blocked','Only one part accepted; manual business review still required')
  self.assertEqual(result['status'],'blocked')
  before=s.read('SELECT status,result FROM service_tasks WHERE id=?',(tid,))[0]
  with closing(sqlite3.connect(s.DB)) as c,c:wf.reconcile(c)
  intermediate=s.read('SELECT status,result FROM service_tasks WHERE id=?',(tid,))[0]
  with closing(sqlite3.connect(s.DB)) as c,c:
   c.execute("UPDATE deliveries SET status='sent' WHERE action_key='outstanding'");wf.reconcile(c)
  after=s.read('SELECT status,result FROM service_tasks WHERE id=?',(tid,))[0]
  print('BUSINESS_BLOCK',json.dumps({'before':before,'after_reconcile':intermediate,'after_receipt':after}))
  self.assertEqual(intermediate['status'],'blocked')
  self.assertEqual(after['status'],'blocked')

 def test_concurrent_claim_two_running(self):
  tid=self.new_task();tm.advance('2025-10-13 07:00')
  state=tm.state();state['auto_highlights']=True
  with closing(sqlite3.connect(s.DB)) as c,c:
   c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
   fields=[r[1] for r in c.execute('PRAGMA table_info(service_tasks)')]
   row=dict(zip(fields,c.execute('SELECT * FROM service_tasks WHERE id=?',(tid,)).fetchone()))
   row['id']='other-task';c.execute('INSERT INTO service_tasks VALUES('+','.join('?' for _ in row)+')',list(row.values()))
  barrier=threading.Barrier(2);real_active=ap.active
  def active():
   value=real_active();barrier.wait(timeout=5);return value
  with patch.object(ap,'sync'),patch.object(ap,'active',side_effect=active),concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
   results=list(pool.map(lambda _:ap.claim(),range(2)))
  running=s.read("SELECT id,status FROM service_tasks WHERE status='running'")
  print('CONCURRENT_CLAIM',json.dumps({'claims':[r['id'] for r in results if r],'running':running,'active_pointer':s.read("SELECT value FROM metadata WHERE key='active_service_task'")}))
  self.assertEqual(len(running),1)
  self.assertEqual(sum(r is not None for r in results),1)

