import copy,json,sqlite3,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import s07_service as rhythm
import test_fixture_integration as fixtures
import test_mode as tm
import agent_plan as ap
import proactive as proactive
import sql_tools as s
import observability as audit


class WeeklyContractTests(unittest.TestCase):
    def summary(self,p,c,covered=True):
        return {'previous_week':{'completed':p,'coverage_complete':covered},'current_week':{'completed':c,'coverage_complete':True}}

    def test_threshold_and_unknown(self):
        for p,c,status in [(3,0,'eligible'),(0,0,'eligible'),(4,0,'not_eligible'),(5,0,'not_eligible'),(3,1,'not_eligible')]:
            self.assertEqual(rhythm.eligibility(self.summary(p,c))[0],status)
        self.assertEqual(rhythm.eligibility(self.summary(None,0,False))[0],'unknown')
        self.assertEqual(rhythm.eligibility(self.summary(None,1,False))[0],'not_eligible')


class S07IntegrationTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def test_wednesday_wakes_even_when_no_contact(self):
        for day in ['2025-10-15','2025-10-22','2025-10-29','2025-11-05']:
            tm.activate(day+' 19:00',False);ap.enable();ap.sync()
            tasks=[r for r in ap.tasks() if r['kind']=='s07']
            self.assertEqual(len(tasks),1)
            with self.assertRaises(ap.ServiceValidationError):ap.prompt(tasks[0]['id'])
            import console_control
            console_control.execute(tasks[0]['id'])
            self.assertEqual(ap.claim()['id'],tasks[0]['id'])
            summary=tm.snapshot()['weekly_learning']
            self.assertIn(rhythm.eligibility(summary)[0],('eligible','not_eligible'))
            self.assertIn('learning_query',ap.prompt(tasks[0]['id'])['prompt'])
            ap.finish(tasks[0]['id'],'blocked','仅验证宿主装配，不执行外发')
        with self.assertRaisesRegex(ValueError, 'outside the first-month'):
            tm.activate('2025-11-12 19:00',False)
        tm.activate('2025-11-06 19:00',False);ap.enable()
        self.assertFalse(any(r['kind']=='s07' for r in ap.tasks()))

    def test_missing_course_coverage_is_unknown(self):
        tm.activate('2025-10-22 19:00',False)
        snap=tm.snapshot();calendar=json.loads(fixtures.fq.FILE.read_text(encoding='utf8'))['course_calendar']
        snap['lessons_as_of']=[r for r in snap['lessons_as_of'] if r['scheduled_date']!='2025-10-14']
        summary=rhythm.weekly_learning(snap,calendar)
        self.assertFalse(summary['previous_week']['coverage_complete'])
        self.assertIsNone(summary['previous_week']['completed'])

    def test_send_rechecks_state_and_dedupes(self):
        tm.activate('2025-10-22 19:00',False);ap.enable()
        snap=tm.snapshot();summary=snap['weekly_learning']
        for key in ['previous_week','current_week']:
            summary[key]['completed']=1 if key=='previous_week' else 0
        snap['lessons_as_of']=[{**r,'status':'absent','completed_at':None} for r in snap['lessons_as_of']]
        with patch.object(tm,'snapshot',return_value=snap):
            candidate=next(r for r in proactive.refresh('2025-10-22') if r['rule']==rhythm.RULE)
            summary['current_week']['completed']=1
            self.assertEqual(proactive.deliver([candidate['id']],'这周可以一起安排阅读。')['status'],'skipped')
            summary['current_week']['completed']=0
            result=proactive.deliver([candidate['id']],'这周可以一起安排阅读。')
            self.assertEqual(result['status'],'accepted')
            self.assertTrue(proactive.deliver([candidate['id']],'另一条消息')['duplicate'])

    def test_skip_finishes_without_precheck_and_sends_wait_for_receipt(self):
        tm.activate('2025-10-22 19:00',True);ap.enable()
        task=next(r for r in ap.tasks() if r['kind']=='s07')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='running' WHERE id=?",(task['id'],))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(json.dumps(task['id']),))
        for name in ['rebuild-reading-rhythm','reply-style']:ap.load_skill(name)
        audit.invoke('learning_query',{'day':'2025-10-22'},lambda:tm.snapshot())
        self.assertEqual(ap.finish(task['id'],'no_contact','上周完成5节且本周已有完课')['status'],'no_contact')

    def test_actual_receipt_controls_deferred_completion(self):
        for delivery_status in ('accepted','sent'):
            tm.activate('2025-11-05 19:00',True);ap.enable()
            task=next(r for r in ap.tasks() if r['kind']=='s07')
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute("UPDATE service_tasks SET status='running' WHERE id=?",(task['id'],))
                c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(json.dumps(task['id']),))
                c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('receipt-test','已拟好的消息',delivery_status,'2025-11-05T19:00:00+08:00','test'))
                c.execute('INSERT INTO service_links VALUES(?,?)',(task['id'],'receipt-test'))
            for name in ['rebuild-reading-rhythm','reply-style']:ap.load_skill(name)
            audit.invoke('learning_query',{'day':'2025-11-05'},lambda:tm.snapshot())
            result=ap.finish(task['id'],'deferred','模型只见过平台受理回执')
            self.assertEqual(result['status'],'done' if delivery_status=='sent' else 'awaiting_delivery')
            # Reconcile old deferred records without letting the dispatcher send again.
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='deferred' WHERE id=?",(task['id'],))
            ap.reconcile_s01_receipts()
            self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'done' if delivery_status=='sent' else 'awaiting_delivery')
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent' WHERE action_key='receipt-test'")
            ap.reconcile_s01_receipts()
            self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'done')
            self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)


if __name__=='__main__':unittest.main()
