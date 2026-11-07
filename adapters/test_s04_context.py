import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_completion_ledger as base
import sql_tools as s,test_mode as tm,agent_plan as ap,observability as audit
import completion_planner as cp,task_context,learning_context,course_service,host_receipt,mcp_server,wechat_test as w

class S04Tests(unittest.TestCase):
    setUp=base.LedgerTests.setUp
    tearDown=base.LedgerTests.tearDown
    create_source=base.LedgerTests.create_source
    set_context=base.LedgerTests.set_context
    begin=base.LedgerTests.begin
    choices=base.LedgerTests.choices
    # Reuse the real ledger/planner to bind a completion; do not manufacture eligible candidates.
    def action(self):
        parent,ctx=self.begin("2025-10-22 20:00");result=cp.commit('s04-test-plan',ctx['context_token'],self.choices(ctx,'S04'))
        ap.finish(parent['id'],'completed','已规划')
        tid=next(x['task_id'] for x in result['decisions'] if x['code']=='S04')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='running',started_at=?,native_request_id='s04-request' WHERE id=?",(s.now(),tid))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(tid),))
        provider=patch.object(course_service,'course_knowledge',return_value={'status':'ok','body':'课程正文不是孩子的答案'})
        self.provider=provider.start();self.addCleanup(provider.stop)
        self.tid=tid
        return tid

    def send(self):
        task=ap.active()
        return mcp_server.audited_call('deliver_proactive',{'candidate_ids':[task['candidate_id']],'body':'侨侨，连续三天都完成了当天的课，老师来为这份积累点个赞！'})

    def finish_receipt(self,result):
        return host_receipt.finish_delivery(self.tid,'s04-request','deliver_proactive',result.get('action_key',''),json.dumps(result))

    def test_s04_bundle_is_bound_to_streak_and_refreshes_without_course(self):
        self.action();prepared=task_context.prepare(self.tid)
        value=json.loads(prepared['prompt'].split('<task_material>\n')[1].split('\n</task_material>')[0])
        facts=value['facts']['learning_context']
        self.assertEqual(set(value['facts']),{'learning_context'})
        self.assertNotIn('service_task_finish',prepared['prompt'].split('<loaded_skill')[0])
        self.assertNotIn('due_at',value['task']);self.assertNotIn('time_window',facts['candidate'])
        self.assertEqual(facts['streak']['days'],3)
        self.assertEqual(facts['streak']['from'],'2025-10-20')
        self.assertEqual([d['date'] for d in facts['learning']['daily']],['2025-10-20','2025-10-21','2025-10-22'])
        self.assertEqual(len(facts['learning']['lessons_as_of']),3)
        self.assertTrue(all(x['completed_on_day']==x['recorded_lessons']==1 for x in facts['learning']['daily']))
        self.assertNotIn('streak_lessons',facts['candidate']['evidence'])
        self.assertEqual([x['name'] for x in prepared['prepared_skills']],['celebrate-learning-streak','reply-style'])
        self.provider.assert_not_called();self.assertNotIn('course',facts)
        latest=tm.snapshot()
        last=facts['streak']['completed_lesson_id']
        for x in latest['lessons_as_of']:
            if x['lesson_id']==last:x['title']='刚更新的课程名称'
        with patch.object(tm,'snapshot',return_value=latest):refreshed=learning_context.read()
        self.assertEqual(refreshed['learning']['lessons_as_of'][-1]['title'],'刚更新的课程名称')
        self.assertNotEqual(facts['snapshot_id'],refreshed['snapshot_id'])
        self.assertTrue(s.read("SELECT 1 FROM tool_audit WHERE name='learning_query' AND origin='aggregate'"))

    def test_s04_one_delivery_then_host_finish_and_no_precheck(self):
        self.action();task_context.prepare(self.tid)
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s04-one'}) as send:
            result=self.send();self.assertEqual(result['status'],'accepted')
            self.assertEqual(self.finish_receipt(result)['status'],'awaiting_delivery');self.assertEqual(send.call_count,1)
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE name IN ('check_proactive','service_task_finish')"))

    def test_s04_pause_after_preload_is_rechecked_without_sending(self):
        self.action();task_context.prepare(self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',(tm.state()['as_of'][:10],'pause','今天不联系',s.now()))
        with patch.object(w,'http_send') as send:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish_receipt(result)['status'],'no_contact');send.assert_not_called()

    def test_s04_unknown_transport_is_blocked_and_not_resent(self):
        self.action();task_context.prepare(self.tid)
        with patch.object(w,'http_send',side_effect=TimeoutError) as send:
            result=self.send();self.assertEqual(result['status'],'unknown')
            self.assertEqual(self.finish_receipt(result)['status'],'blocked');self.assertEqual(send.call_count,1)

    def test_s04_wrong_request_or_forged_receipt_cannot_finish(self):
        self.action();task_context.prepare(self.tid)
        self.assertEqual(self.finish_receipt({'status':'sent','action_key':'invented'})['status'],'continue')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s04-one'}):result=self.send()
        self.assertEqual(host_receipt.finish_delivery(self.tid,'other','deliver_proactive',result.get('action_key'),json.dumps(result))['status'],'continue')
        self.assertEqual(self.finish_receipt({**result,'status':'sent'})['status'],'continue')

    def test_s04_missing_candidate_preserves_gap(self):
        self.action()
        with patch.object(task_context,'candidates',return_value={'actions':[]}):
            facts=learning_context.read();self.assertIsNone(facts['candidate'])
            self.assertIsNone(facts['streak']['days']);self.assertEqual(facts['learning']['lessons_as_of'],[])
        self.provider.assert_not_called()

    def test_s04_revised_completion_breaks_streak_and_blocks_send(self):
        self.action();task_context.prepare(self.tid)
        latest=tm.snapshot()
        for row in latest['lessons_as_of']:
            if row['scheduled_date']=='2025-10-21':row['completed_at']='2025-10-22T18:00:00+08:00'
        with patch.object(tm,'snapshot',return_value=latest),patch.object(w,'http_send') as send:
            facts=learning_context.read();self.assertNotEqual(facts['streak']['days'],3)
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish_receipt(result)['status'],'no_contact');send.assert_not_called()

    def test_s04_wrong_day_cannot_read_other_period(self):
        self.action()
        with self.assertRaises(ValueError):learning_context.read('2025-10-21')
        self.provider.assert_not_called()

    def test_s04_wrong_reply_tool_guides_to_delivery_without_other_skills(self):
        self.action();task_context.prepare(self.tid)
        for name in ['reply_to_message','reply_with_highlights']:
            with self.assertRaises(ap.ServiceValidationError) as caught:
                mcp_server.business_call(name,{'event_id':'wrong','body':'连续三天的小小积累，老师看见啦'})
            self.assertIn('deliver_proactive',str(caught.exception))
            self.assertNotIn('tutor-main',str(caught.exception))
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s04-recovered'}):
            result=self.send();self.assertEqual(result['status'],'accepted')
            self.assertEqual(self.finish_receipt(result)['status'],'awaiting_delivery')

if __name__=='__main__':unittest.main()
