import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock,patch
from p0_acceptance import Console
from reply_lab import Fault


class ConsoleBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c=Console.__new__(Console);self.c.root=Path(self.tmp.name);self.c.home=self.c.root;self.c.lock=threading.Lock()
        self.c.current={'owner':'one','case':'unknown','running':True,'experiment_run_id':'exp-test'}
        self.c.authorize=lambda actor:None;self.c.ops=Mock(return_value={'status':'checked'})
        self.actor={'id':'one'}
    def test_outside_integration_root_cannot_enable_console(self):
        (self.c.root/'config').mkdir();(self.c.root/'config/p0-acceptance.json').write_text('{"scope":"integration_only"}')
        with self.assertRaises(Fault):Console(types.SimpleNamespace(root=self.c.root))
    def test_stale_run_is_rejected_before_operator_action(self):
        with self.assertRaisesRegex(Fault,'场景已变化'):
            self.c.dispatch(self.actor,'late_receipt',{'request_id':'stale-001','experiment_run_id':'other','operation_id':'x'})
        self.c.ops.assert_not_called()
    def test_duplicate_operation_replays_result_without_resend(self):
        data={'request_id':'same-request-001','experiment_run_id':'exp-test'}
        a=self.c.dispatch(self.actor,'flush',data);b=self.c.dispatch(self.actor,'flush',data)
        self.assertEqual(a,b);self.c.ops.assert_called_once()
        with self.assertRaisesRegex(Fault,'不可改写'):self.c.dispatch(self.actor,'flush',{**data,'extra':'different'})
        self.c.ops.assert_called_once()
    def test_cross_case_and_unknown_action_are_rejected(self):
        with self.assertRaises(Fault):self.c.dispatch(self.actor,'night',{'request_id':'wrong-case-001','experiment_run_id':'exp-test'})
        with self.assertRaises(Fault):self.c.dispatch(self.actor,'shell',{'request_id':'invalid-action'})
        self.c.ops.assert_not_called()
    def test_bound_run_cannot_escape_registered_experiment_home(self):
        self.c.current['run_root']='/home/ubuntu/digitalTutor-dsh-lab'
        with self.assertRaises(Fault):self.c.bound()
    def test_stop_recovers_process_after_launch_timeout(self):
        self.c.current['running']=False;self.c.lease=None
        self.c.bound=Mock(return_value=self.c.root);self.c.save=Mock()
        record=self.c.root/'record.json';record.write_text('{"status":"running"}')
        with patch('native_experiments.stop',side_effect=lambda run:record.write_text('{"status":"stopped"}')) as stop:
            self.assertEqual(self.c.stop()['status'],'stopped_evidence_retained')
        stop.assert_called_once_with(self.c.root)
    def test_stop_waits_until_native_worker_releases_lock(self):
        run=self.c.root/'experiments/exp-test';run.mkdir(parents=True)
        (run.parent/'worker.lock').touch()
        with patch('fcntl.flock',side_effect=[BlockingIOError(),None]) as flock,patch('time.sleep') as sleep:
            self.c.wait_worker_release(run)
        self.assertEqual(flock.call_count,2);sleep.assert_called_once_with(.25)


class OperatorStageTests(unittest.TestCase):
    import test_service_workflow as fixture
    setUp=fixture.WorkflowTests.setUp
    tearDown=fixture.WorkflowTests.tearDown
    create_source=fixture.WorkflowTests.create_source
    set_context=fixture.WorkflowTests.set_context
    begin_workflow=fixture.WorkflowTests.begin_workflow
    plan=fixture.WorkflowTests.plan
    stop_plan=fixture.WorkflowTests.stop_plan
    new_task=fixture.WorkflowTests.new_task
    def test_staged_handoff_uses_actual_task_and_blocks_old_authority(self):
        import test_mode as tm, p0_actions,operator_alerts,sql_tools as s
        tid=self.new_task();tm.advance('2025-10-13 07:00')
        with patch('experiment_runtime.verify',return_value={'scenario_version':'p0-human-v1'}):
            self.assertEqual(p0_actions.execute('stage',{})['task_id'],tid)
            result=p0_actions.execute('takeover',{})
            self.assertEqual(result['status'],'human_confirmation_required')
            probe=p0_actions.execute('probe',{})
        self.assertTrue(probe['outbox_unchanged'])
        self.assertNotEqual(probe['checks']['old_execution'],'allowed')
        self.assertNotEqual(probe['checks']['deliver_proactive'],'allowed')
        self.assertNotEqual(probe['checks']['workflow_fulfill'],'allowed')
        self.assertTrue(operator_alerts.list_alerts()['model_outbound_paused'])
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(tid,))[0]['status'],'blocked')
    def test_late_receipt_requires_real_attempted_mock_operation(self):
        import p0_actions
        self.new_task()
        with self.assertRaisesRegex(ValueError,'尚无模拟发送记录'):p0_actions.late_receipt('invented-unsent-part')


if __name__=='__main__':unittest.main()
