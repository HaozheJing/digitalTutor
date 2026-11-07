import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_completion_ledger as base
import sql_tools as s,test_mode as tm,agent_plan as ap,observability as audit
import completion_planner as cp,task_context,learning_context,course_service,host_receipt,mcp_server,wechat_test as w

class S02Tests(unittest.TestCase):
    setUp=base.LedgerTests.setUp
    tearDown=base.LedgerTests.tearDown
    create_source=base.LedgerTests.create_source
    set_context=base.LedgerTests.set_context
    begin=base.LedgerTests.begin
    choices=base.LedgerTests.choices
    # Reuse the real ledger/planner to bind a completion; do not manufacture eligible candidates.
    def action(self):
        parent,ctx=self.begin();result=cp.commit('s02-test-plan',ctx['context_token'],self.choices(ctx,'S02'))
        ap.finish(parent['id'],'completed','已规划')
        tid=next(x['task_id'] for x in result['decisions'] if x['code']=='S02')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='running',started_at=?,native_request_id='s02-request' WHERE id=?",(s.now(),tid))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(tid),))
        provider=patch.object(course_service,'course_knowledge',return_value={'status':'ok','body':'课程正文不是孩子的答案'})
        self.provider=provider.start();self.addCleanup(provider.stop)
        self.tid=tid
        return tid

    def send(self):
        task=ap.active()
        return mcp_server.audited_call('deliver_proactive',{'candidate_ids':[task['candidate_id']],'body':'侨侨，这一课学完啦，为这次阅读点个赞！'})

    def finish_receipt(self,result):
        return host_receipt.finish_delivery(self.tid,'s02-request','deliver_proactive',result.get('action_key',''),json.dumps(result))

    def test_s02_bundle_is_bound_slim_and_refreshes_body(self):
        tid=self.action();prepared=task_context.prepare(tid)
        value=json.loads(prepared['prompt'].split('<task_material>\n')[1].split('\n</task_material>')[0])
        self.assertEqual(set(value['facts']),{'learning_context'})
        instructions=prepared['prompt'].split('<loaded_skill')[0]
        self.assertIn('调用deliver_proactive提交正文',instructions)
        self.assertNotIn('service_task_finish',instructions)
        self.assertNotIn('不再调用',prepared['prompt'])
        facts=value['facts']['learning_context'];lesson=facts['learning']['lessons_as_of'][0]
        self.assertEqual(lesson['lesson_id'],facts['candidate']['evidence']['lesson']['lesson_id'])
        self.assertEqual(self.provider.call_args.kwargs['lesson_no'],lesson['lesson_no'])
        self.assertNotIn('due_at',value['task']);self.assertNotIn('time_window',facts['candidate'])
        self.assertNotIn('service_agenda',facts['context']);self.assertEqual(len(facts['learning']['lessons_as_of']),1)
        self.assertTrue(facts['learning']['quiz_attempts'])
        self.assertTrue(all(x['lesson_id']==lesson['lesson_id'] for x in facts['learning']['quiz_attempts']))
        self.assertEqual([x['name'] for x in prepared['prepared_skills']],['celebrate-lesson-completion','reply-style'])
        self.provider.return_value={'status':'ok','body':'刚刚更新的正文'}
        second=learning_context.read();self.assertEqual(second['course']['body'],'刚刚更新的正文')
        self.assertNotEqual(facts['snapshot_id'],second['snapshot_id'])
        self.assertTrue(s.read("SELECT 1 FROM tool_audit WHERE name='action_candidates' AND origin='aggregate'"))

    def test_s02_one_delivery_then_host_finish_and_no_precheck(self):
        self.action();task_context.prepare(self.tid)
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s02-one'}) as send:
            result=self.send();self.assertEqual(result['status'],'accepted')
            self.assertEqual(self.finish_receipt(result)['status'],'awaiting_delivery');self.assertEqual(send.call_count,1)
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE name IN ('check_proactive','service_task_finish')"))

    def test_s02_pause_after_preload_is_rechecked_without_sending(self):
        self.action();task_context.prepare(self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',(tm.state()['as_of'][:10],'pause','今天不联系',s.now()))
        with patch.object(w,'http_send') as send:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish_receipt(result)['status'],'no_contact');send.assert_not_called()

    def test_s02_unknown_transport_is_blocked_and_not_resent(self):
        self.action();task_context.prepare(self.tid)
        with patch.object(w,'http_send',side_effect=TimeoutError) as send:
            result=self.send();self.assertEqual(result['status'],'unknown')
            self.assertEqual(self.finish_receipt(result)['status'],'blocked');self.assertEqual(send.call_count,1)

    def test_s02_wrong_request_or_forged_receipt_cannot_finish(self):
        self.action();task_context.prepare(self.tid)
        self.assertEqual(self.finish_receipt({'status':'sent','action_key':'invented'})['status'],'continue')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s02-one'}):result=self.send()
        self.assertEqual(host_receipt.finish_delivery(self.tid,'other','deliver_proactive',result.get('action_key'),json.dumps(result))['status'],'continue')
        self.assertEqual(self.finish_receipt({**result,'status':'sent'})['status'],'continue')

    def test_s02_missing_candidate_preserves_gap_without_reading_unrelated_course(self):
        self.action()
        with patch.object(task_context,'candidates',return_value={'actions':[]}):
            value=learning_context.read();self.assertIsNone(value['candidate']);self.assertEqual(value['learning']['lessons_as_of'],[])
            self.assertEqual(value['course']['status'],'not_requested');self.provider.assert_not_called()

    def test_s02_makeup_keeps_bound_lesson_and_does_not_ask_today_schedule(self):
        self.action();original=tm.snapshot()
        for row in original['lessons_as_of']:
            if row['lesson_id']=='L2-002':row['scheduled_date']='2025-10-13'
        with patch.object(tm,'snapshot',return_value=original),patch.object(course_service,'course_schedule') as schedule:
            value=learning_context.read();self.assertEqual(value['learning']['lessons_as_of'][0]['scheduled_date'],'2025-10-13')
            self.assertEqual(self.provider.call_args.kwargs['lesson_no'],2);schedule.assert_not_called()

if __name__=='__main__':unittest.main()
