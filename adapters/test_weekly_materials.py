"""Fixed weekly PDF scheduling, guarded sending, and receipt behavior."""
import copy,json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as fixture
import test_mode as tm,sql_tools as s,agent_plan as ap
import weekly_materials as wm,s09_service as svc,wechat_test as w

class WeeklyMaterialsTests(unittest.TestCase):
    setUp=fixture.FixtureIntegrationTests.setUp
    tearDown=fixture.FixtureIntegrationTests.tearDown
    create_source=fixture.FixtureIntegrationTests.create_source
    set_context=fixture.FixtureIntegrationTests.set_context

    def setup_simulation(self,at='2025-10-18 09:00'):
        self.enterContext(patch.object(wm,'ENABLED',True))  # Historical delivery behavior only.
        tm.activate(at,True);ap.enable()
        self.enterContext(patch('experiment_runtime.manifest',return_value={'experiment_run_id':'exp-'+'1'*32}))
        self.enterContext(patch.object(w,'provider_config',return_value=({'simulation':True},{})))
        self.enterContext(patch.object(w,'http_send',return_value={'errcode':0,'requestId':'simulation-file'}))

    def test_fixed_pdf_sent_once_despite_all_lessons_complete(self):
        self.setup_simulation()
        self.assertEqual(svc.eligibility(tm.snapshot()['weekly_learning'],'2025-10-17')[0],'not_eligible')
        result=wm.queue();self.assertEqual(result['status'],'queued')
        self.assertEqual(wm.select()['asset']['course_week'],1)
        self.assertEqual(w.flush()[0]['status'],'accepted')
        self.assertEqual(wm.queue()['status'],'accepted')
        fixture.confirm_outbox()
        self.assertEqual(wm.queue()['status'],'sent')
        self.assertEqual(wm.context()['delivery_status'],'sent')
        self.assertEqual(w.flush(),[])
        self.assertEqual(len(s.read('SELECT * FROM test_outbox')),1)
        self.assertEqual(s.read('SELECT message_type FROM test_outbox')[0]['message_type'],1)

    def test_before_friday_20_and_saturday_do_not_send(self):
        self.setup_simulation('2025-10-18 08:59')
        self.assertEqual(wm.queue()['status'],'not_due')
        tm.activate('2025-10-19 09:00',True)
        self.assertEqual(wm.queue()['status'],'not_due')

    def test_four_exact_weeks_and_reject_fifth(self):
        self.setup_simulation()
        for day,week in [('2025-10-18',1),('2025-10-25',2),('2025-11-01',3),('2025-11-08',4)]:
            tm.activate(day+' 09:00',True)
            self.assertEqual(wm.select()['asset']['course_week'],week)
        with self.assertRaisesRegex(ValueError,'outside the first-month'):
            tm.activate('2025-11-15 09:00',True)
        self.assertEqual(tm.state()['as_of'][:10],'2025-11-08')

    def test_pause_before_queue_and_before_provider(self):
        self.setup_simulation()
        with closing(sqlite3.connect(s.DB)) as c,c:
            columns=c.execute('PRAGMA table_info(contact_constraints)').fetchall()
            # Use the published pause API when possible, with the same database contract.
            c.execute('INSERT INTO contact_constraints(day,quote) VALUES(?,?)',('2025-10-18','家长暂停'))
        self.assertEqual(wm.queue()['status'],'deferred')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('DELETE FROM contact_constraints')
        wm.queue()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints(day,quote) VALUES(?,?)',('2025-10-18','家长暂停'))
        with self.assertRaisesRegex(ValueError,'暂停'):w.flush()
        self.assertEqual(s.read('SELECT * FROM test_outbox'),[])

    def test_withdrawal_blocks_an_already_queued_file(self):
        self.setup_simulation();result=wm.queue()
        with patch.object(wm,'ENABLED',False),closing(sqlite3.connect(s.DB)) as c:
            with self.assertRaisesRegex(ValueError,'撤销'):wm.authorize_part(c,tm.state()['run_id']+':'+result['action_key'])
        self.assertEqual(s.read('SELECT * FROM test_outbox'),[])

    def test_unknown_outcome_is_not_resent(self):
        self.setup_simulation();wm.queue()
        with patch.object(w,'http_send',side_effect=TimeoutError) as send:
            self.assertEqual(w.flush()[0]['status'],'unknown')
            wm.queue();w.flush();self.assertEqual(send.call_count,1)

    def test_changed_payload_and_hash_rejected(self):
        self.setup_simulation();result=wm.queue()
        with closing(sqlite3.connect(s.DB)) as c:
            payload=wm.payload_for(c,result['action_key']);payload['url']='https://invalid.example/arbitrary.pdf'
            with self.assertRaises(ValueError):wm.authorize_part(c,tm.state()['run_id']+':'+result['action_key'],body=json.dumps(payload))
        asset=copy.deepcopy(wm.select()['asset']);asset['sha256']='0'*64
        with self.assertRaises(ValueError):wm.verify_asset(asset)
        asset['path']='../../etc/passwd'
        with self.assertRaises(ValueError):wm.verify_asset(asset)

    def test_missing_data_is_not_zero_completion(self):
        self.setup_simulation();summary=tm.snapshot()['weekly_learning']
        summary['current_week']['coverage_complete']=False
        self.assertEqual(svc.eligibility(summary,'2025-10-17')[0],'unknown')
        summary['current_week']['coverage_complete']=True
        for row in summary['current_week']['lessons']:row['status']='not_started'
        self.assertEqual(svc.eligibility(summary,'2025-10-17')[0],'eligible')
        self.assertEqual(svc.eligibility(summary,'2025-10-18')[0],'not_eligible')

    def test_unregistered_model_file_rejected(self):
        self.setup_simulation()
        with self.assertRaises(ValueError):w.send('unregistered',json.dumps({'name':'x.pdf','url':'https://invalid.example/x.pdf','size':1}),1)
        self.assertEqual(s.read('SELECT * FROM test_outbox'),[])

    def test_s10_new_contract_requires_exact_sent_pdf_and_no_resend(self):
        self.setup_simulation();__import__('service_workflow').initialize()
        with patch('service_workflow.enabled',return_value=True), patch('service_workflow.planning_contract',return_value={'required_weekly_material':True}):
            with closing(sqlite3.connect(s.DB)) as c,c:self.assertEqual(wm.s10_delivery_gate(c)['status'],'deferred')
            queued=wm.queue();w.flush()
            with closing(sqlite3.connect(s.DB)) as c,c:self.assertEqual(wm.s10_delivery_gate(c)['status'],'deferred')
            fixture.confirm_outbox()
            with closing(sqlite3.connect(s.DB)) as c,c:self.assertIsNone(wm.s10_delivery_gate(c))
            for status in ('unknown','failed','partial'):
                with closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute('UPDATE deliveries SET status=? WHERE action_key=?',(status,queued['action_key']))
                    self.assertEqual(wm.s10_delivery_gate(c)['status'],'blocked')
            self.assertEqual(len(s.read('SELECT * FROM test_outbox')),1)

    def test_s10_missing_pdf_and_live_publisher_are_explicit_blocks(self):
        self.setup_simulation()
        with patch('service_workflow.enabled',return_value=True), patch('service_workflow.planning_contract',return_value={'required_weekly_material':True}), closing(sqlite3.connect(s.DB)) as c,c:
            with patch('experiment_runtime.manifest',return_value=None):self.assertEqual(wm.s10_delivery_gate(c)['status'],'blocked')
            with patch.object(wm,'select',return_value={'status':'unknown','reason':'没有本周文件'}):self.assertEqual(wm.s10_delivery_gate(c)['status'],'blocked')
        self.assertEqual(s.read('SELECT * FROM test_outbox'),[])

    def test_s10_previous_frozen_contract_keeps_original_behavior(self):
        self.setup_simulation()
        with patch('service_workflow.enabled',return_value=True), patch('service_workflow.planning_contract',return_value={}), closing(sqlite3.connect(s.DB)) as c:
            self.assertIsNone(wm.s10_delivery_gate(c))

if __name__=='__main__':unittest.main()
