import json
import sqlite3
from contextlib import closing
from unittest.mock import patch
import unittest
import test_s01_delivery as legacy
import test_mode as tm
import sql_tools as s
import agent_plan as ap
import observability as audit
import task_context
import learning_context
import course_service
import wechat_test as w


class LearningContextTests(unittest.TestCase):
    tearDown = legacy.S01DeliveryTests.tearDown
    create_source = legacy.S01DeliveryTests.create_source
    set_context = legacy.S01DeliveryTests.set_context

    def setUp(self):
        legacy.S01DeliveryTests.setUp(self)
        provider=patch.object(course_service,'course_knowledge',return_value={
            'status':'ok','class_type':'L2','lesson_no':1,'body':'今日课程完整正文','live_read':True})
        self.course_provider=provider.start()
        self.addCleanup(provider.stop)

    def begin(self):
        task = legacy.S01DeliveryTests.begin(self)
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute('DELETE FROM tool_audit WHERE task_id=?', (task['id'],))
        return task

    def test_preload_is_one_entry_and_retains_actual_source_receipts(self):
        task = self.begin()
        prepared = task_context.prepare(task['id'])
        payload = json.loads(prepared['prompt'].split('<task_material>\n')[1].split('\n</task_material>')[0])
        self.assertEqual(set(payload['facts']), {'learning_context'})
        self.assertEqual([x['name'] for x in prepared['prepared_skills']], ['remind-reading', 'reply-style'])
        facts = payload['facts']['learning_context']
        self.assertNotIn('reading_status', facts)
        self.assertEqual(facts['course']['body'], '今日课程完整正文')
        self.assertEqual(facts['as_of'], tm.state()['as_of'])
        self.assertEqual(len(facts['snapshot_id']), 64)
        self.assertTrue(all(x['scheduled_date']=='2025-10-13' for x in facts['learning']['lessons_as_of']))
        self.assertNotIn('due_at', payload['task'])
        self.assertNotIn('service_agenda', facts['context'])
        self.assertNotIn('knowledge_fulltext', facts['context'])
        rows = s.read('SELECT name,origin,output FROM tool_audit WHERE task_id=?', (task['id'],))
        self.assertEqual([x['name'] for x in rows if x['origin']=='host'], ['service_skill','service_skill','learning_context'])
        sources = {x['name']:json.loads(x['output']) for x in rows if x['origin']=='aggregate'}
        self.assertEqual(sources['family_profile'], facts['family'])
        self.assertEqual(sources['learning_query'], facts['learning'])
        self.assertEqual(sources['course_schedule'], facts['schedule'])
        self.assertEqual(sources['daily_context'], facts['context'])
        self.assertEqual(sources['course_knowledge'], facts['course'])
        self.assertNotIn('未完课才读今日正文',prepared['prompt'])

    def test_model_entry_uses_same_facts_and_does_not_claim_child_model_calls(self):
        task = self.begin()
        import mcp_server
        self.assertIn('learning_context', {x[0] for x in mcp_server.base_mcp.TOOLS})
        value = audit.invoke('learning_context', {}, lambda:mcp_server._business_call('learning_context', {}))
        self.assertEqual(value['course']['status'], 'ok')
        rows=s.read('SELECT name,origin FROM tool_audit WHERE task_id=?',(task['id'],))
        self.assertEqual([x['name'] for x in rows if x['origin']=='model'], ['learning_context'])

    def test_aggregate_alone_can_deliver_with_real_course_receipt(self):
        self.begin()
        learning_context.read()
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'aggregate-one'}) as sender:
            self.assertEqual(s.deliver('2025-10-13','侨侨，今天有一节阅读课，方便时来读吧。')['status'],'accepted')
            self.assertEqual(sender.call_count,1)

    def test_missing_actual_course_receipt_still_blocks_delivery(self):
        self.begin();learning_context.read()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("DELETE FROM tool_audit WHERE name='course_knowledge'")
        with patch.object(w,'http_send') as sender:
            self.assertEqual(s.deliver('2025-10-13','读故事吧')['status'],'needs_revision')
            sender.assert_not_called()

    def test_completion_after_preload_still_prevents_delivery(self):
        self.begin();learning_context.read()
        snapshot=tm.snapshot();snapshot['lessons_as_of'][0]['status']='completed'
        with patch.object(tm,'snapshot',return_value=snapshot),patch.object(w,'http_send') as sender:
            refreshed=learning_context.read()
            self.assertEqual(refreshed['learning']['lessons_as_of'][0]['status'],'completed')
            self.assertEqual(refreshed['course']['status'],'ok')
            self.assertEqual(self.course_provider.call_count,2)
            self.assertEqual(s.deliver('2025-10-13','读故事吧')['status'],'skipped')
            sender.assert_not_called()

    def test_unknown_and_mismatched_lesson_never_become_unfinished(self):
        self.begin();snapshot=tm.snapshot()
        for field,value in [('status','unknown'),('lesson_id','different-lesson')]:
            changed=json.loads(json.dumps(snapshot));changed['lessons_as_of'][0][field]=value
            with patch.object(tm,'snapshot',return_value=changed):
                self.assertEqual(learning_context.read()['learning']['lessons_as_of'][0][field],value)
                with patch.object(w,'http_send') as sender:
                    self.assertEqual(s.deliver('2025-10-13','读故事吧')['status'],'skipped')
                    sender.assert_not_called()

    def test_no_lesson_finishes_without_course_body_or_message(self):
        task=self.begin()
        with patch.object(course_service,'course_schedule',return_value={'date':'2025-10-13','status':'no_lesson'}):
            self.assertEqual(learning_context.read()['course']['status'],'not_requested')
            self.course_provider.assert_not_called()
        self.assertEqual(ap.finish(task['id'],'no_contact','今日无课')['status'],'no_contact')
        self.assertEqual(s.read('SELECT * FROM deliveries'), [])

    def test_missing_binding_does_not_fabricate_schedule_read(self):
        self.begin()
        with patch.object(s,'family_profile',return_value={'child_nickname':'侨侨'}),patch.object(course_service,'course_schedule') as query:
            facts=learning_context.read()
            self.assertEqual(facts['schedule']['status'],'unknown')
            self.assertEqual(facts['course']['status'],'not_requested')
            self.assertNotIn('course_schedule',facts['fact_tools'])
            query.assert_not_called()

    def test_stale_day_rejected_before_any_reads(self):
        self.begin()
        with patch.object(s,'family_profile') as profile:
            with self.assertRaisesRegex(ValueError,'当前业务日期'):learning_context.read('2025-10-12')
            profile.assert_not_called()

    def test_pause_survives_aggregation_and_still_blocks_send(self):
        self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-13','m','今天不要提醒',s.now()))
        facts=learning_context.read()
        self.assertEqual(facts['context']['contact_constraints'][0]['quote'],'今天不要提醒')
        with patch.object(w,'http_send') as sender:
            self.assertEqual(s.deliver('2025-10-13','读故事吧')['status'],'skipped')
            sender.assert_not_called()

    def test_every_package_fetches_fresh_body_and_changes_snapshot(self):
        self.begin()
        first=learning_context.read()
        self.course_provider.return_value={**first['course'],'body':'本次读取的新正文'}
        second=learning_context.read()
        self.assertEqual(self.course_provider.call_count,2)
        self.assertEqual(second['course']['body'],'本次读取的新正文')
        self.assertNotEqual(first['snapshot_id'],second['snapshot_id'])

    def test_unavailable_course_is_honest_and_simple_invitation_can_send(self):
        self.begin();self.course_provider.return_value={'status':'unavailable'}
        self.assertEqual(learning_context.read()['course']['status'],'unavailable')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'simple'}) as sender:
            self.assertEqual(s.deliver('2025-10-13','今天有一节阅读课，方便时来读吧。')['status'],'accepted')
            sender.assert_called_once()

    def test_passive_granular_tools_remain_and_do_not_load_course(self):
        import mcp_server
        self.begin()
        self.assertTrue({'family_profile','learning_query','course_schedule','course_read'}<={x[0] for x in mcp_server.base_mcp.TOOLS})
        mcp_server._business_call('learning_query',{'day':'2025-10-13'})
        self.course_provider.assert_not_called()

    def test_changed_business_time_rejects_torn_package(self):
        self.begin();state=tm.state()
        with patch.object(tm,'state',side_effect=lambda:{**state,'as_of':'2025-10-13T07:01:00+08:00'} if self.course_provider.call_count else state):
            with self.assertRaisesRegex(ValueError,'读取期间'):learning_context.read()


if __name__=='__main__':unittest.main()
