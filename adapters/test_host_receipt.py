import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_service_workflow as base
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf
import host_receipt as host,observability as audit,task_context,course_service,wechat_test as w

class HostReceiptTests(unittest.TestCase):
    def setUp(self):
        base.WorkflowTests.setUp(self)
        legacy=patch('s01_reading_guides.required',return_value=False)
        legacy.start();self.addCleanup(legacy.stop)  # Old frozen text-only S01 contract.
    tearDown=base.WorkflowTests.tearDown
    create_source=base.WorkflowTests.create_source
    set_context=base.WorkflowTests.set_context
    begin_workflow=base.WorkflowTests.begin_workflow
    plan=base.WorkflowTests.plan
    stop_plan=base.WorkflowTests.stop_plan
    new_task=base.WorkflowTests.new_task
    start_action=base.WorkflowTests.start_action

    def begin(self):
        self.tid=self.new_task();self.start_action(self.tid);self.request='host-request-1'
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('UPDATE service_tasks SET native_request_id=? WHERE id=?',(self.request,self.tid))
        with patch.object(course_service,'course_knowledge',return_value={'status':'unavailable'}):task_context.prepare(self.tid)

    def deliver(self):
        return audit.invoke('deliver_message',{'day':'2025-10-13','body':'侨侨，来读今天的课吧。'},lambda:s.deliver('2025-10-13','侨侨，来读今天的课吧。'))

    def finish(self,receipt,request=None):
        return host.finish_delivery(self.tid,request or self.request,'deliver_message',receipt.get('action_key',''),json.dumps(receipt))

    def test_accepted_already_reconciled_registers_and_late_sent_completes_without_resend(self):
        self.begin()
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'one'}) as send:
            receipt=self.deliver()
            with closing(sqlite3.connect(s.DB)) as c,c:wf.reconcile(c)
            self.assertIsNone(ap.active())
            self.assertEqual(self.finish(receipt)['status'],'awaiting_delivery')
            self.assertEqual(self.finish(receipt)['status'],'awaiting_delivery')
            self.assertEqual(send.call_count,1)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE deliveries SET status='sent'");wf.reconcile(c)
        self.assertEqual(self.finish(receipt)['status'],'done')
        self.assertTrue(s.read("SELECT 1 FROM tool_audit WHERE name='host_service_task_finish' AND origin='host'"))

    def test_completed_and_unknown_learning_map_without_a_model_finish(self):
        self.begin();snapshot=tm.snapshot()
        snapshot['lessons_as_of'][0]['status']='completed'
        with patch.object(tm,'snapshot',return_value=snapshot),patch.object(w,'http_send') as send:
            self.assertEqual(self.finish(self.deliver())['status'],'no_contact');send.assert_not_called()

    def test_unknown_learning_blocks_instead_of_claiming_no_contact(self):
        self.begin();snapshot=tm.snapshot();snapshot['lessons_as_of'][0]['status']='unknown'
        with patch.object(tm,'snapshot',return_value=snapshot),patch.object(w,'http_send') as send:
            self.assertEqual(self.finish(self.deliver())['status'],'blocked');send.assert_not_called()

    def test_pause_and_chat_defer_keep_existing_outcomes(self):
        self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("INSERT INTO task_state VALUES('chat-gate','active',?)",(json.dumps({'active':True}),))
        with patch.object(w,'http_send') as send:
            self.assertEqual(self.finish(self.deliver())['status'],'deferred');send.assert_not_called()

    def test_pause_does_not_send(self):
        self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-13','m','今天不要提醒',s.now()))
        with patch.object(w,'http_send') as send:
            self.assertEqual(self.finish(self.deliver())['status'],'no_contact');send.assert_not_called()

    def test_unknown_transport_stops_and_does_not_retry(self):
        self.begin()
        with patch.object(w,'http_send',side_effect=TimeoutError) as send:
            receipt=self.deliver();self.assertEqual(receipt['status'],'unknown')
            self.assertEqual(self.finish(receipt)['status'],'blocked');self.assertEqual(send.call_count,1)

    def test_foreign_request_forged_receipt_and_other_active_task_cannot_finish(self):
        self.begin()
        self.assertEqual(self.finish({'status':'accepted','action_key':'fake'})['status'],'continue')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'one'}):receipt=self.deliver()
        self.assertEqual(self.finish(receipt,'different-request')['status'],'continue')
        self.assertEqual(self.finish({**receipt,'status':'sent'})['status'],'continue')
        self.assertEqual(self.finish({**receipt,'action_key':'foreign-key'})['status'],'continue')
        with patch.object(ap,'active',return_value={'id':'other-task'}):self.assertEqual(self.finish(receipt)['status'],'continue')

    def test_revision_and_explicit_human_block_stay_with_original_handler(self):
        self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("DELETE FROM tool_audit WHERE name='course_knowledge'")
        receipt=self.deliver();self.assertEqual(receipt['status'],'needs_revision')
        self.assertEqual(self.finish(receipt)['status'],'continue');self.assertIsNotNone(ap.active())
        audit.invoke('course_knowledge',{'class_type':'L2','lesson_no':1},lambda:{'status':'unavailable'})
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'one'}):receipt=self.deliver()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='blocked',result=? WHERE id=?",(json.dumps({'code':'human_confirmation_required'}),self.tid))
        self.assertEqual(self.finish(receipt)['status'],'continue')

if __name__=='__main__':unittest.main()
