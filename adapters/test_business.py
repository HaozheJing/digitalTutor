import unittest,json,datetime
from zoneinfo import ZoneInfo
import test_live as fixtures
import sql_tools as s
import business as b
import live_runtime
from contextlib import closing
import sqlite3
import io
from unittest.mock import patch

class BusinessTests(unittest.TestCase):
    insert=fixtures.LiveTests.insert
    create_source=fixtures.LiveTests.create_source
    set_context=fixtures.LiveTests.set_context
    tearDown=fixtures.LiveTests.tearDown
    def setUp(self):fixtures.LiveTests.setUp(self);b.initialize()
    def test_reply_finishes_without_plan_or_memory_review(self):
        self.insert();live_runtime.ingest(self.settings)
        event=live_runtime.claim()['id']
        with self.assertRaises(ValueError):b.finish(b.token(event),'2026-10-20')
        s.mock_reply(event,'您好。')
        self.assertEqual(b.finish(b.token(event),'2026-10-20')['status'],'done')
        self.assertEqual(s.read('SELECT * FROM daily_plans'),[])
    def test_task_distinguishes_reply_event_from_source_message(self):
        self.insert();live_runtime.ingest(self.settings)
        event=live_runtime.claim()['id']
        with patch.object(b,'context',return_value={}),patch('service_workflow.enabled',return_value=False):
            prompt=b.task(event)['prompt']
        self.assertIn('event_id 使用此引用）：'+b.token(event),prompt)
        self.assertIn('source_id 是消息资料引用',prompt)
    def test_projection_preserves_user_quote_and_unknown(self):
        value=b.project({'source':'sql_mock','id':'sql-mock-event','status':'mock_sent','guardian_role':'unknown','body':'用户问：这是模拟吗？','verified':False})
        self.assertNotIn('source',value);self.assertNotIn('verified',value)
        self.assertEqual(value['status'],'accepted');self.assertEqual(value['body'],'用户问：这是模拟吗？')
        self.assertEqual(b.original(value['id']),'sql-mock-event')
    def test_contact_constraint_requires_evidence(self):
        day=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        self.insert();live_runtime.ingest(self.settings)
        with self.assertRaises(ValueError):b.pause_contact(day,'a:m1','今天别催')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE messages SET body='今天别催了' WHERE source_id='a:m1'")
        result=b.pause_contact(day,'a:m1','今天别催了')
        self.assertEqual(result['proactive_contact'],'paused')

    def test_business_references_survive_nested_projection(self):
        value={'candidate_id':'candidate:review:2026-10-20'}
        projected=b.project(value)
        self.assertEqual(b.project(projected),projected)
        self.assertEqual(b.original(projected['candidate_id']),value['candidate_id'])

    def test_plan_preserves_actual_schedule_time_and_rejects_outside_window(self):
        day='2026-10-20'
        policy={'business_dates':[day],'purpose':'资料复查','earliest':day+'T01:00:00+00:00','latest':day+'T02:00:00+00:00'}
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO action_catalog VALUES(?,?,?,?,?)',('review','复查','review-context',1,json.dumps(policy)))
        candidate=b.candidates(day)['actions'][0]['candidate_id']
        row={'id':'schedule-test','title':candidate,'prompt':'review','scheduledAt':day+'T01:30:00+00:00'}
        def response(*a,**k):return io.BytesIO(json.dumps([row]).encode())
        with patch('urllib.request.urlopen',response):
            result=b.save_plan(day,[{'candidate_id':candidate,'schedule_id':row['id']}],'在允许窗口内')
            self.assertEqual(result['items'][0]['run_at'],row['scheduledAt'])
            row['scheduledAt']=day+'T03:00:00+00:00'
            with self.assertRaises(ValueError):b.save_plan(day,[{'candidate_id':candidate,'schedule_id':row['id']}],'越界')

if __name__=='__main__':unittest.main()
