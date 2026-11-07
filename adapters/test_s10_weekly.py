import copy,json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as fixtures
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf,fixture_query as fq,s10_service as s10,weekly_materials as wm
class S10WeeklyTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context
    def current(self):tm.activate('2025-10-18 09:00',True);return tm.snapshot()
    def test_three_bands_and_unknown_not_zero(self):
        value=self.current();week=value['weekly_learning']['current_week'];self.assertEqual(s10.review(value)['band'],'complete')
        for n in [4,1,0]:
            v=copy.deepcopy(value);w=v['weekly_learning']['current_week'];w['completed']=n
            for i,x in enumerate(w['lessons']):x['status']='completed' if i<n else 'not_started'
            self.assertEqual(s10.review(v)['band'],'zero' if n==0 else 'partial')
        week['coverage_complete']=False;self.assertEqual(s10.review(value)['band'],'unknown')
    def test_fewer_scheduled_lessons_can_be_complete(self):
        value=self.current();week=value['weekly_learning']['current_week'];week.update(lessons=week['lessons'][:3],scheduled=3,completed=3)
        self.assertEqual(s10.review(value)['band'],'complete')
    def test_every_known_band_has_s10_candidate(self):
        value=self.current()
        with closing(sqlite3.connect(s.DB)) as c:
            for n in [5,3,0]:
                v=copy.deepcopy(value);w=v['weekly_learning']['current_week'];w['completed']=n
                for i,x in enumerate(w['lessons']):x['status']='completed' if i<n else 'absent'
                self.assertEqual(s10.decision(c,tm.state(),v)['status'],'eligible')
    def test_host_fixed_task_created_once_without_model_planning(self):
        self.current();ap.initialize();wf.initialize();state=tm.state();state.update(agent_planning=True,service_workflow_mode=wf.MODE)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        ap.sync();ap.sync()
        rows=s.read("SELECT id,due_at FROM service_tasks WHERE skill='weekly-learning-review' AND run_id=?",(state['run_id'],))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['due_at'],'2025-10-18T09:00:00+08:00')
        self.assertEqual(s.read("SELECT * FROM service_workflow_commits"),[])
    def test_family_time_handoff_requires_actual_sent_zero_invitation(self):
        import business,operator_alerts,live_runtime,hashlib
        value=self.current();ap.initialize();state=tm.state();item={'rule':s10.RULE,'dedupe_key':state['run_id']+':S10:test','evidence':{'weekly_review':{'band':'zero'}}}
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',('s10-zero','2025-10-18',s10.RULE,'week',s.encoded(item),'delivered',s.now()))
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(item['dedupe_key'],'s10-zero','zero-delivery',s.now()))
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('zero-delivery','请家长提供方便沟通时间','accepted',state['as_of'],'simulation'))
        self.assertIsNone(s10.appointment_context('2025-10-18'))
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE deliveries SET status='sent'")
            text='我周日上午十点方便，能和老师聊一下吗？'
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)',('appointment-msg','user',text,s.now(),'explicit_synthetic_appointment_case',hashlib.sha256(text.encode()).hexdigest()))
            live_runtime.enqueue(c,'appointment-event','passive',{'source_message_id':'appointment-msg'},received_at=s.now())
            c.execute("UPDATE events SET status='dispatched' WHERE id='appointment-event'")
        self.assertFalse(s10.appointment_context('2025-10-18')['confirmed'])
        self.assertIn('request_human_confirmation',business.task('appointment-event')['prompt'])
        result=operator_alerts.request_alert('家长提出周日上午十点沟通，需真人确认排班',event_id='appointment-event')
        self.assertEqual(result['status'],'human_confirmation_required');self.assertEqual(result['alert']['source_message']['body'],text)
        self.assertEqual(s.read('SELECT * FROM test_outbox'),[])

    def test_friday_no_file_and_saturday_first_week_pdf(self):
        tm.activate('2025-10-17 20:00',True);self.assertEqual(wm.queue()['status'],'not_due')
        self.current();self.assertTrue(wm.due(tm.state()));self.assertEqual(wm.select()['asset']['course_week'],1)
    def test_missing_current_material_not_replaced_by_other_week(self):
        import tempfile
        from pathlib import Path
        tm.activate('2025-11-08 09:00',True)
        registry=json.loads(wm.REGISTRY.read_text());registry['assets']=[x for x in registry['assets'] if x['course_week']!=4]
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'materials.json';target.write_text(json.dumps(registry))
            with patch.object(wm,'REGISTRY',target):self.assertEqual(wm.select()['status'],'unknown')
        with self.assertRaises(ValueError):tm.activate('2025-11-15 09:00',True)
if __name__=='__main__':unittest.main()
