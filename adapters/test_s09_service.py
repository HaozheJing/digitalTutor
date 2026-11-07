"""Saturday entry executes S09 directly and waits for actual delivery receipts."""
import json,sqlite3,unittest
from contextlib import closing
import test_fixture_integration as fixtures
import test_mode as tm,agent_plan as ap,sql_tools as s

class S09Tests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def begin(self,day='2025-10-24'):
        tm.activate(day+(' 09:00' if day=='2025-10-25' else ' 20:00'),True);ap.enable()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='cancelled' WHERE kind!='weekly'")
        return ap.claim()

    def link(self,task,status):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('s09-receipt','消息正文',status,s.now(),'test'))
            c.execute('INSERT INTO service_links VALUES(?,?)',(task['id'],'s09-receipt'))

    def test_friday_prompt_executes_without_creating_another_schedule(self):
        task=self.begin();prompt=ap.prompt(task['id'])['prompt']
        self.assertIn('plan-weekend-makeup',prompt)
        self.assertIn('不创建新的service_schedule',prompt)
        self.assertIn('不调用save_plan',prompt)
        self.assertIn('deliver_proactive',prompt)
        self.assertNotIn('通过service_schedule落实非周期安排',prompt)
        self.assertEqual(task['due_at'],'2025-10-24T20:00:00+08:00')

    def test_saturday_s10_entry(self):
        task=self.begin('2025-10-25');prompt=ap.prompt(task['id'])['prompt']
        self.assertIn('周六',prompt)
        self.assertNotIn('内部周六09:00补读检查',prompt)
        self.assertTrue(ap.receipt_driven(task))

    def test_accepted_is_waiting_and_sent_receipt_completes_without_another_send(self):
        from unittest.mock import patch
        task=self.begin();self.link(task,'accepted')
        with patch.object(ap,'require_skills'),patch.object(ap,'require_queries'):
            self.assertEqual(ap.finish(task['id'],'deferred','平台受理')['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE deliveries SET status='sent'")
        ap.reconcile_s01_receipts()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'done')
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_missing_finish_uses_only_trusted_delivery(self):
        for receipt,expected in [('sent','done'),('accepted','awaiting_delivery'),(None,'blocked')]:
            with self.subTest(receipt=receipt):
                task=self.begin()
                if receipt:self.link(task,receipt)
                self.assertEqual(ap.settle(task['id'])['status'],expected)
                self.assertIsNone(ap.active())
                if receipt=='accepted':
                    with self.assertRaises(ValueError):tm.activate('2025-10-24 09:00',True)
                    with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent' WHERE action_key='s09-receipt'")
                    ap.reconcile_s01_receipts()
                    self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'done')

    def test_other_business_block_is_not_overridden_by_sent_receipt(self):
        task=self.begin();self.link(task,'sent')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='blocked',result=? WHERE id=?",(json.dumps({'reason':'人工核对另有业务问题'}),task['id']))
        ap.reconcile_s01_receipts()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'blocked')

if __name__=='__main__':unittest.main()
