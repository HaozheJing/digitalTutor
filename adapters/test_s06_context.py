import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_completion_ledger as base
import sql_tools as s,test_mode as tm,agent_plan as ap,observability as audit
import completion_planner as cp,task_context,learning_context,course_service,host_receipt,mcp_server,wechat_test as w

class S06Tests(unittest.TestCase):
    setUp=base.LedgerTests.setUp
    tearDown=base.LedgerTests.tearDown
    create_source=base.LedgerTests.create_source
    set_context=base.LedgerTests.set_context
    begin=base.LedgerTests.begin
    choices=base.LedgerTests.choices
    scenario=base.LedgerTests.scenario
    # Reuse the real ledger/planner to bind a completion; do not manufacture eligible candidates.
    def action(self):
        parent,ctx=self.scenario("2025-10-30 20:00");result=cp.commit('s06-test-plan',ctx['context_token'],self.choices(ctx,'S06'))
        ap.finish(parent['id'],'completed','已规划')
        tid=next(x['task_id'] for x in result['decisions'] if x['code']=='S06')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='running',started_at=?,native_request_id='s06-request' WHERE id=?",(s.now(),tid))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(tid),))
        provider=patch.object(course_service,'course_knowledge',return_value={'status':'ok','body':'课程正文不是孩子的答案'})
        self.provider=provider.start();self.addCleanup(provider.stop)
        self.tid=tid
        return tid

    def send(self):
        task=ap.active()
        return mcp_server.audited_call('deliver_proactive',{'candidate_ids':[task['candidate_id']],'body':'侨侨，这一课学完啦，为这次阅读点个赞！'})

    def finish_receipt(self,result):
        return host_receipt.finish_delivery(self.tid,'s06-request','deliver_proactive',result.get('action_key',''),json.dumps(result))

    def test_s06_bundle_reads_overdue_course_and_refreshes(self):
        self.action();prepared=task_context.prepare(self.tid)
        value=json.loads(prepared['prompt'].split('<task_material>\n')[1].split('\n</task_material>')[0])
        facts=value['facts']['learning_context'];learning=facts['learning']
        self.assertEqual(set(value['facts']),{'learning_context'})
        self.assertNotIn('service_task_finish',prepared['prompt'].split('<loaded_skill')[0])
        self.assertNotIn('due_at',value['task']);self.assertNotIn('time_window',facts['candidate'])
        self.assertEqual(len(learning['gap_lessons']),2)
        self.assertNotEqual(learning['completed_lesson']['lesson_id'],learning['suggested_lesson']['lesson_id'])
        self.assertEqual(facts['course']['bound_lesson_id'],learning['suggested_lesson']['lesson_id'])
        self.assertEqual(self.provider.call_args.kwargs['lesson_no'],learning['suggested_lesson']['lesson_no'])
        self.assertLess(learning['preceding_lesson']['lesson_no'],learning['suggested_lesson']['lesson_no'])
        self.assertGreater(learning['completed_lesson']['lesson_no'],max(x['lesson_no'] for x in learning['gap_lessons']))
        self.assertTrue(learning['preceding_lesson']['completed_at'])
        self.assertNotIn('overdue_lessons',facts['candidate']['evidence'])
        self.assertEqual([x['name'] for x in prepared['prepared_skills']],['prevent-lesson-skipping','reply-style'])
        self.provider.return_value={'status':'ok','body':'刚刚更新的待补课正文'}
        refreshed=learning_context.read();self.assertEqual(refreshed['course']['body'],'刚刚更新的待补课正文')
        self.assertNotEqual(facts['snapshot_id'],refreshed['snapshot_id'])
        self.assertTrue(s.read("SELECT 1 FROM tool_audit WHERE name='course_knowledge' AND origin='aggregate'"))

    def test_s06_one_delivery_then_host_finish_and_no_precheck(self):
        self.action();task_context.prepare(self.tid)
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s06-one'}) as send:
            result=self.send();self.assertEqual(result['status'],'accepted')
            self.assertEqual(self.finish_receipt(result)['status'],'awaiting_delivery');self.assertEqual(send.call_count,1)
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE name IN ('check_proactive','service_task_finish')"))

    def test_s06_pause_after_preload_is_rechecked_without_sending(self):
        self.action();task_context.prepare(self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',(tm.state()['as_of'][:10],'pause','今天不联系',s.now()))
        with patch.object(w,'http_send') as send:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish_receipt(result)['status'],'no_contact');send.assert_not_called()

    def test_s06_unknown_transport_is_blocked_and_not_resent(self):
        self.action();task_context.prepare(self.tid)
        with patch.object(w,'http_send',side_effect=TimeoutError) as send:
            result=self.send();self.assertEqual(result['status'],'unknown')
            self.assertEqual(self.finish_receipt(result)['status'],'blocked');self.assertEqual(send.call_count,1)

    def test_s06_wrong_request_or_forged_receipt_cannot_finish(self):
        self.action();task_context.prepare(self.tid)
        self.assertEqual(self.finish_receipt({'status':'sent','action_key':'invented'})['status'],'continue')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s06-one'}):result=self.send()
        self.assertEqual(host_receipt.finish_delivery(self.tid,'other','deliver_proactive',result.get('action_key'),json.dumps(result))['status'],'continue')
        self.assertEqual(self.finish_receipt({**result,'status':'sent'})['status'],'continue')

    def test_s06_missing_candidate_preserves_gap(self):
        self.action()
        with patch.object(task_context,'candidates',return_value={'actions':[]}):
            value=learning_context.read();self.assertIsNone(value['candidate'])
            self.assertEqual(value['learning']['gap_lessons'],[])
            self.assertEqual(value['course']['status'],'not_requested');self.provider.assert_not_called()

    def test_s06_fully_made_up_after_preload_never_sends(self):
        self.action();task_context.prepare(self.tid)
        latest=tm.snapshot()
        for row in latest['lessons_as_of']:
            if row.get('overdue'):
                row.update(overdue=False,completed_at=tm.state()['as_of'],status='completed')
        with patch.object(tm,'snapshot',return_value=latest),patch.object(w,'http_send') as send:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish_receipt(result)['status'],'no_contact');send.assert_not_called()

    def test_s06_delivery_does_not_complete_learning_goals(self):
        self.action();task_context.prepare(self.tid)
        import service_ledger as ledger
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s06-goals'}):
            result=self.send();self.finish_receipt(result)
        case=ledger.view()['cases'][0]
        self.assertTrue(case['goals'])
        self.assertTrue(all(g['completed_event'] is None for g in case['goals']))
        self.assertEqual(s.read("SELECT * FROM service_tasks WHERE kind='case_review'"),[])

    def test_s06_expired_window_no_send(self):
        self.action();task_context.prepare(self.tid)
        state=tm.state();state['as_of']='2025-10-30T20:16:00+08:00'
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        with patch.object(w,'http_send') as send:
            result=self.send();self.assertEqual(result['status'],'expired')
            self.assertEqual(self.finish_receipt(result)['status'],'no_contact');send.assert_not_called()

    def test_s06_wrong_day_and_wrong_reply_tool(self):
        self.action();task_context.prepare(self.tid)
        with self.assertRaises(ValueError):learning_context.read('2025-10-29')
        for name in ['reply_to_message','reply_with_highlights']:
            with self.assertRaisesRegex(ap.ServiceValidationError,'deliver_proactive'):
                mcp_server.business_call(name,{'event_id':'wrong','body':'接着读吧'})
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_s06_missing_sequence_row_does_not_invent_gap(self):
        self.action();task_context.prepare(self.tid)
        latest=tm.snapshot();missing=next(x['lesson_id'] for x in latest['lessons_as_of'] if x['overdue'])
        latest['lessons_as_of']=[x for x in latest['lessons_as_of'] if x['lesson_id']!=missing]
        with patch.object(tm,'snapshot',return_value=latest),patch.object(w,'http_send') as send:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish_receipt(result)['status'],'no_contact');send.assert_not_called()

if __name__=='__main__':unittest.main()
