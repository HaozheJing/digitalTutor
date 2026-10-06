"""Strict Worker contract tests; all state and channels are isolated fixtures."""
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from digital_tutor.application import Application, Clock
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue, validate_lease
from digital_tutor.scheduler import Scheduler, Worker


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'strict.sqlite'
        self.runtime_clock = Clock('2026-10-04T09:00:00+08:00')
        self.app = Application(self.path, Clock('2026-10-04T09:00:00+08:00'),
                               mode='passive', strict_runtime=True, runtime_clock=self.runtime_clock)
        self.app.create_family('f', 'synthetic-u', 'mock:f')
        self.app.create_family('g', 'synthetic-g', 'mock:g')
        self.queue = JobQueue(self.app)
        self.counter = 0

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def inbound(self, family='f'):
        self.counter += 1
        token = self.app.open_session(family, 'channel')
        return self.app.ingest_message(token, {'external_id':'in-'+str(self.counter),
            'body':'synthetic question', 'occurred_at':self.app.clock.iso()})['data']

    def seed_report(self):
        # Scheduler gate fixture only; report semantics are exercised by progress test.
        with self.app.store.transaction():
            self.app.store.put('report', 'report-fixture', 'f', {'resolution':None})

    def test_passive_old_active_jobs_cannot_invoke_model(self):
        with self.app.store.transaction():
            for kind in ('daily', 'progress', 'step', 'review'):
                self.queue.enqueue('f', 'old-'+kind, {'kind':kind})
        calls = []
        worker = Worker(self.app, lambda j,t:calls.append(j))
        for _ in range(3):
            self.assertEqual(Scheduler(self.app).tick()['created'], 0)
            self.assertFalse(worker.run_once()['ran'])
        self.assertEqual(calls, [])

    def test_reply_scope_contains_only_pending_family_input(self):
        m = self.inbound(); self.inbound('g')
        old = self.inbound()
        t = self.app.open_session('f', 'service')
        self.app.interpret(t, {'request_id':'interpreted-no-reply', 'message_id':old['id'],
            'expected_version':old['version'], 'intents':[{'intent_id':'receipt',
            'text':'acknowledgement', 'requires_reply':False}]})
        claim = self.queue.claim('w')
        token = self.queue.session(claim)
        _, binding = self.app.auth(token, {'service'})
        self.assertEqual(binding['reply_scope'], {m['id']:['*']})
        self.assertEqual(binding['mode'], 'passive')
        self.assertEqual(binding['run_id'], claim['job']['run_id'])

    def test_no_pending_input_stays_zero_model_calls(self):
        m = self.inbound()
        token = self.app.open_session('f', 'service')
        self.app.interpret(token, {'request_id':'ack', 'message_id':m['id'],
            'expected_version':1, 'intents':[{'intent_id':'receipt', 'text':'ack', 'requires_reply':False}]})
        calls = []
        self.assertFalse(Worker(self.app, lambda j,t:calls.append(j)).run_once()['ran'])
        self.assertEqual(calls, [])

    def test_daily_pending_reports_never_call_or_bump_business_version(self):
        self.app.mode = 'daily'; self.seed_report()
        Scheduler(self.app).tick()
        version = self.app.store.family('f')['version']
        calls = []
        # The independent family g may legitimately get an empty daily; inspect f only.
        with self.app.store.transaction():
            for job in self.app.store.list('job', 'g'):
                job['status']='succeeded'; self.app.store.put('job', job['id'], 'g', job, job['version'])
        worker = Worker(self.app, lambda j,t:calls.append(j))
        for _ in range(4):
            self.assertFalse(worker.run_once()['ran'])
        self.assertEqual(calls, [])
        self.assertEqual(self.app.store.family('f')['version'], version)
        job = self.app.store.list('job', 'f')[0]
        self.assertEqual(job['reason'], 'REPORTS_PENDING')
        self.assertEqual(job['attempt'], 0)

    def test_inbound_revokes_planner_but_holds_serial_until_release(self):
        self.app.mode = 'full'
        with self.app.store.transaction():
            self.queue.enqueue('f', 'daily-f', {'kind':'daily','business_date':'2026-10-04'})
        claim = self.queue.claim('planner'); token = self.queue.session(claim)
        self.inbound()
        with self.assertRaises(BusinessError) as exc:
            self.app.auth(token, {'planner_daily'})
        self.assertEqual(exc.exception.code, 'RUN_REVOKED')
        self.assertIsNone(self.queue.claim('service-before-exit'))
        old = self.app.store.get('job', 'daily-f', 'f')
        self.assertEqual(old['status'], 'leased')
        self.assertTrue(old['yield_requested'])
        self.queue.finish(claim, 'waiting', 'RUN_REVOKED')
        service = self.queue.claim('service-after-exit')
        self.assertEqual(service['job']['kind'], 'service')
        self.queue.finish(service)
        resumed = self.queue.claim('planner-resume')
        self.assertEqual(resumed['job']['id'], claim['job']['id'])
        _, fresh = self.app.auth(self.queue.session(resumed), {'planner_daily'})
        self.assertEqual(fresh['family_version'], self.app.store.family('f')['version'])
        self.assertNotEqual(fresh['run_id'], claim['job']['run_id'])

    def test_preemption_other_family_is_unchanged(self):
        self.app.mode = 'full'
        with self.app.store.transaction():
            self.queue.enqueue('f', 'daily-f', {'kind':'daily', 'business_date':'2026-10-04'})
        claim = self.queue.claim('w'); token = self.queue.session(claim)
        self.inbound('g')
        self.app.auth(token, {'planner_daily'})
        self.assertEqual(self.queue.claim('other')['family_id'], 'g')

    def test_shared_clock_changes_child_business_time_not_strict_lease(self):
        clock_path = Path(self.tmp.name) / 'clock.json'
        clock_path.write_text(json.dumps({'now':'2026-10-04T20:59:59+08:00'}))
        self.app.clock = Clock(shared_path=clock_path)
        self.inbound(); claim = self.queue.claim('w')
        code = 'from digital_tutor.application import Clock; import sys; print(Clock(shared_path=sys.argv[1]).iso())'
        first = subprocess.check_output([sys.executable, '-c', code, str(clock_path)], text=True).strip()
        clock_path.write_text(json.dumps({'now':'2026-10-04T21:00:00+08:00'}))
        second = subprocess.check_output([sys.executable, '-c', code, str(clock_path)], text=True).strip()
        self.assertTrue(first.endswith('20:59:59+08:00'))
        self.assertTrue(second.endswith('21:00:00+08:00'))
        validate_lease(self.app, 'f', claim['lease'])
        self.runtime_clock.fixed += timedelta(seconds=61)
        with self.assertRaises(BusinessError) as exc:
            validate_lease(self.app, 'f', claim['lease'])
        self.assertEqual(exc.exception.code, 'LEASE_LOST')

    def test_missing_result_retries_three_times_with_backoff_no_business_bump(self):
        self.inbound(); version = self.app.store.family('f')['version']
        worker = Worker(self.app, lambda j,t:{})
        for delay in (30, 120):
            result = worker.run_once()
            self.assertEqual(result['error'], 'INTERNAL_RESULT_MISSING')
            self.assertFalse(worker.run_once()['ran'])
            self.runtime_clock.fixed += timedelta(seconds=delay)
        result = worker.run_once()
        self.assertEqual(result['outcome'], 'failed')
        self.assertFalse(worker.run_once()['ran'])
        job = self.app.store.list('job', 'f')[0]
        self.assertEqual(job['failure_count'], 3)
        self.assertEqual(job['attempt'], 3)
        self.assertEqual(self.app.store.family('f')['version'], version)
        self.assertFalse(self.app.store.list('message', 'f')[0]['interpreted'])

    def test_repeated_expired_process_leases_are_bounded(self):
        self.inbound()
        for delay in (30, 120):
            self.assertIsNotNone(self.queue.claim('crashing', 1))
            self.runtime_clock.fixed += timedelta(seconds=2)
            self.assertIsNone(self.queue.claim('reaper'))
            self.runtime_clock.fixed += timedelta(seconds=delay)
        self.assertIsNotNone(self.queue.claim('crashing', 1))
        self.runtime_clock.fixed += timedelta(seconds=2)
        self.assertIsNone(self.queue.claim('reaper'))
        self.assertEqual(self.app.store.list('job', 'f')[0]['status'], 'failed')

    def test_renewal_does_not_change_family_input_version(self):
        self.inbound(); claim = self.queue.claim('w')
        version = self.app.store.family('f')['version']
        self.queue.renew(claim)
        self.assertEqual(self.app.store.family('f')['version'], version)

    def test_progress_commit_keeps_real_worker_lease_until_handler_exit(self):
        import time
        self.app.mode = 'full'
        self.app.runtime_clock = Clock()
        planner = self.app.open_session('f', 'planner_daily')
        committed = self.app.commit_daily(planner, {'request_id':'seed-event',
            'expected_family_version':1, 'business_date':'2026-10-04',
            'new_events':[{'client_ref':'fixture', 'goal_key':'fixture', 'goal_text':'Synthetic original responsibility',
                'due_at':'2026-10-04T20:00:00+08:00',
                'completion':{'kind':'goal','lesson_refs':['SYNTHETIC-LESSON']}}], 'plan_refs':['fixture']})
        event_id = committed['data']['event_ids']['fixture']
        admin = self.app.open_session('f', 'admin')
        self.app.add_evidence(admin, 'evidence-fixture', {'kind':'lesson_completed','lesson_ref':'SYNTHETIC-LESSON'})
        service = self.app.open_session('f', 'service')
        self.app.submit_report(service, {'request_id':'seed-report', 'report_id':'report-lease',
            'revision':1, 'event_ids':[event_id], 'facts':['Original responsibility remains'], 'evidence_refs':[{'kind':'evidence','id':'evidence-fixture','version':1}]})
        observed = []
        def handler(job, tools):
            self.assertEqual(job['kind'], 'progress')
            event = self.app.event('f', event_id)
            result = tools.call('planner.commit_progress', {'request_id':'commit-leased',
                'report_id':'report-lease', 'revision':1,
                'expected_family_version':self.app.store.family('f')['version'],
                'event_updates':[{'event_id':event_id,'expected_version':event['version'],
                    'changes':{},'evidence_refs':[]}]})
            self.assertTrue(result['ok'], result)
            observed.append(self.app.store.get('job',job['id'],'f')['status'])
            # Host would still monitor the capability while Hermes produces final output.
            time.sleep(1.3)
            state = tools.call('state.read', {})
            self.assertTrue(state['ok'], state)
            observed.append(self.app.store.get('job',job['id'],'f')['status'])
            return {}
        result = Worker(self.app, handler, lease_seconds=1).run_once()
        self.assertEqual(result['outcome'], 'succeeded', result)
        self.assertEqual(observed, ['leased','leased'])
        self.assertEqual(self.app.store.list('report','f')[0]['resolution'], 'applied')
        self.assertEqual(self.app.store.list('job','f')[0]['status'], 'succeeded')

    def test_worker_preemption_releases_old_identity_for_fresh_snapshot(self):
        self.app.mode = 'full'
        with self.app.store.transaction():
            self.queue.enqueue('f', 'daily-preempt', {'kind':'daily', 'business_date':'2026-10-04'})
        def handler(job, tools):
            self.inbound()
            tools.app.auth(tools.token, {'planner_daily'})
        result = Worker(self.app, handler).run_once()
        self.assertEqual(result['error'], 'RUN_REVOKED')
        job = self.app.store.get('job','daily-preempt','f')
        self.assertEqual(job['status'], 'ready')
        self.assertEqual(job['failure_count'], 0)
        self.assertTrue(job['resume_with_fresh_snapshot'])
        self.assertEqual(self.queue.claim('new-service')['job']['kind'], 'service')

    def test_strict_default_uses_real_runtime_clock(self):
        app = Application(Path(self.tmp.name)/'clock-default.sqlite',
                          Clock('1999-01-01T10:00:00+08:00'), strict_runtime=True)
        try:
            self.assertIsNone(app.runtime_clock.fixed)
            self.assertNotEqual(app.runtime_clock.now().year, app.clock.now().year)
        finally:
            app.close()

    def test_no_external_fact_before_native_crash_remains_diagnostic_failure(self):
        message = self.inbound()
        def handler(job, tools):
            # A native process can persist interpretation then exit before its result.
            result = tools.call('message.interpret', {'request_id':'ack-crash',
                'message_id':message['id'],'expected_version':1,
                'intents':[{'intent_id':'ack','text':'Acknowledged','requires_reply':False}]})
            self.assertTrue(result['ok'], result)
            return {'retryable_error':'NATIVE_PROCESS_FAILED'}
        worker = Worker(self.app, handler)
        self.assertEqual(worker.run_once()['outcome'],'waiting')
        self.runtime_clock.fixed += timedelta(seconds=30)
        self.assertFalse(worker.run_once()['ran'])
        job = self.app.store.list('job','f')[0]
        self.assertEqual(job['status'],'failed')
        self.assertEqual(job['reason'],'INTERNAL_RESULT_MISSING')
        self.assertEqual(job['attempt'],1)

    def test_new_input_revokes_service_draft_and_preserves_both_messages(self):
        from digital_tutor.tools import ToolRegistry
        from digital_tutor.mock_channel import channel_for
        first = self.inbound()
        claim = self.queue.claim('old-service')
        token = self.queue.session(claim)
        tools = ToolRegistry(self.app, token)
        interpreted = tools.call('message.interpret', {'request_id':'draft-input',
            'message_id':first['id'],'expected_version':1,
            'intents':[{'intent_id':'question','text':'Original question','requires_reply':True}]})
        self.assertTrue(interpreted['ok'], interpreted)
        second = self.inbound()
        result = tools.call('wechat.send_message', {'request_id':'old-draft-send',
            'expected_family_version':self.app.store.family('f')['version'],
            'body':'Draft formed before the correction', 'action_kind':'reply',
            'reply_to':[{'message_id':first['id'],'intent_id':'question'}]})
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'RUN_REVOKED')
        self.assertEqual(channel_for(self.app).count(), 0)
        self.assertIsNone(self.queue.claim('overlapping-service'))
        saved = self.queue.finish(claim, 'waiting', 'RUN_REVOKED')
        self.assertEqual(saved['status'], 'ready')
        resumed = self.queue.claim('fresh-service')
        self.assertEqual(resumed['job']['id'], claim['job']['id'])
        _, binding = self.app.auth(self.queue.session(resumed), {'service'})
        self.assertEqual(binding['reply_scope'], {first['id']:['question'],second['id']:['*']})
        self.assertNotEqual(binding['run_id'], claim['job']['run_id'])
        messages = self.app.store.list('message', 'f')
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['intents'][0]['reply_operation_refs'], [])
        self.assertFalse(messages[1]['interpreted'])

    def external_fact_then_crash(self, stage='sent', count=1):
        message = self.inbound()
        self.app.mock_send_outcome = stage
        def handler(job, tools):
            result = tools.call('message.interpret', {'request_id':'interpret-before-crash',
                'message_id':message['id'],'expected_version':1,
                'intents':[{'intent_id':'question','text':'Question','requires_reply':True}] +
                    ([{'intent_id':'second','text':'Second question','requires_reply':True}] if count==2 else [])})
            self.assertTrue(result['ok'], result)
            result = tools.call('wechat.send_message', {'request_id':'send-before-crash',
                'expected_family_version':self.app.store.family('f')['version'],
                'body':'Synthetic authorized answer', 'action_kind':'reply',
                'reply_to':[{'message_id':message['id'],'intent_id':'question'}]})
            self.assertTrue(result['ok'], result)
            return {'retryable_error':'NATIVE_PROCESS_FAILED'}
        first = Worker(self.app, handler).run_once()
        self.assertEqual(first['outcome'], 'waiting', first)
        self.runtime_clock.fixed += timedelta(seconds=30)
        return message, self.app.store.list('operation','f')[0], self.app.store.list('job','f')[0]

    def recovery_outcome(self, job, tools, outcome='action_taken'):
        _, binding = self.app.auth(tools.token, {'service'})
        self.assertTrue(binding['recovery_only'])
        self.assertTrue(binding['no_contact'])
        self.assertEqual(binding['reply_scope'], {})
        op = self.app.store.get('operation', job['recovery_operation_ids'][0], 'f')
        message = self.app.store.get('message', job['message_id'], 'f')
        args = {'request_id':'recover-'+binding['run_id'], 'outcome':outcome,
                'reason':'Verify original operation only',
                'refs':[{'kind':'message','id':message['id'],'version':message['version']},
                        {'kind':'operation','id':op['id'],'version':op['version']}]}
        if outcome in {'waiting','needs_input'}:
            args.update(remaining_work=['Original operation receipt'], resume_condition='Original operation changes')
        result = tools.call('service.record_outcome', args)
        self.assertTrue(result['ok'], result)
        return {'outcome_id':binding['run_id']}

    def test_answered_before_native_crash_recovers_same_job_without_new_send(self):
        from digital_tutor.mock_channel import channel_for
        message, op, original = self.external_fact_then_crash()
        self.app.clock = Clock('2026-10-04T21:01:00+08:00')
        def handler(job, tools):
            self.assertEqual(job['id'], original['id'])
            self.assertEqual(job['previous_run_id'], original['run_id'])
            blocked = tools.call('wechat.send_message', {'request_id':'forbidden-recovery-send',
                'expected_family_version':self.app.store.family('f')['version'],
                'body':'Forbidden repeat', 'action_kind':'reply',
                'reply_to':[{'message_id':message['id'],'intent_id':'question'}]})
            self.assertFalse(blocked['ok'], blocked)
            self.assertEqual(blocked['error']['code'], 'NO_CONTACT_RUN')
            return self.recovery_outcome(job, tools)
        result = Worker(self.app, handler).run_once()
        self.assertEqual(result['outcome'], 'succeeded', result)
        self.assertEqual(channel_for(self.app).count(), 1)
        self.assertEqual(self.app.store.get('operation',op['id'],'f')['version'], op['version'])
        self.assertEqual(self.app.store.list('job','f')[0]['recovery_attempt'], 1)
        self.assertFalse(Worker(self.app, handler).run_once()['ran'])

    def test_internal_unknown_recovery_waits_for_original_operation_change(self):
        message, op, original = self.external_fact_then_crash('unknown')
        calls = []
        def handler(job, tools):
            calls.append(job['run_id'])
            return self.recovery_outcome(job, tools, 'waiting')
        worker = Worker(self.app, handler)
        self.assertEqual(worker.run_once()['outcome'], 'waiting')
        self.assertFalse(worker.run_once()['ran'])
        with self.app.store.transaction():
            self.app.store.bump('f')
        self.assertFalse(worker.run_once()['ran'])
        self.assertEqual(len(calls), 1)
        channel = self.app.open_session('f', 'channel')
        from digital_tutor.tools import ToolRegistry
        receipt = ToolRegistry(self.app, channel).call('wechat.mock_receipt',
            {'request_id':'late-original-receipt','operation_id':op['id'],'stage':'sent'})
        self.assertTrue(receipt['ok'], receipt)
        result = Worker(self.app, lambda j,t:self.recovery_outcome(j,t)).run_once()
        self.assertEqual(result['outcome'], 'succeeded', result)
        self.assertFalse(worker.run_once()['ran'])

    def test_internal_recovery_failures_are_bounded_at_three(self):
        self.external_fact_then_crash()
        worker = Worker(self.app, lambda j,t:{'retryable_error':'RECOVERY_NATIVE_FAILED'})
        for delay in (30, 120):
            self.assertEqual(worker.run_once()['outcome'], 'waiting')
            self.assertFalse(worker.run_once()['ran'])
            self.runtime_clock.fixed += timedelta(seconds=delay)
        self.assertEqual(worker.run_once()['outcome'], 'failed')
        self.assertFalse(worker.run_once()['ran'])
        job = self.app.store.list('job','f')[0]
        self.assertEqual(job['recovery_attempt'], 3)
        self.assertEqual(job['recovery_failure_count'], 3)
        self.assertEqual(len(self.app.store.list('operation','f')), 1)

    def test_legacy_failed_missing_outcome_can_recover_existing_external_fact(self):
        self.external_fact_then_crash()
        with self.app.store.transaction():
            job = self.app.store.list('job','f')[0]
            job.update(status='failed',reason='INTERNAL_RESULT_MISSING',not_before=None)
            self.app.store.put('job',job['id'],'f',job,job['version'])
        result = Worker(self.app, lambda j,t:self.recovery_outcome(j,t)).run_once()
        self.assertEqual(result['outcome'], 'succeeded', result)

    def test_partial_multi_intent_internal_recovery_releases_fresh_reply_capability(self):
        from digital_tutor.mock_channel import channel_for
        message, op, original = self.external_fact_then_crash(count=2)
        recovered = Worker(self.app, lambda j,t:self.recovery_outcome(j,t)).run_once()
        self.assertEqual(recovered['outcome'], 'ready', recovered)
        self.assertEqual(channel_for(self.app).count(), 1)
        def reply_remaining(job, tools):
            _, binding = self.app.auth(tools.token, {'service'})
            self.assertFalse(binding['recovery_only'])
            self.assertFalse(binding['no_contact'])
            self.assertEqual(binding['reply_scope'], {message['id']:['second']})
            sent = tools.call('wechat.send_message', {'request_id':'reply-second',
                'expected_family_version':self.app.store.family('f')['version'],
                'body':'Answer to the second synthetic question', 'action_kind':'reply',
                'reply_to':[{'message_id':message['id'],'intent_id':'second'}]})
            self.assertTrue(sent['ok'], sent)
            effect = sent['result']['data']
            recorded = tools.call('service.record_outcome', {'request_id':'second-outcome',
                'outcome':'action_taken','reason':'Second pending intent answered',
                'refs':[{'kind':'operation','id':effect['id'],'version':effect['version']}]})
            self.assertTrue(recorded['ok'], recorded)
            return {'outcome_id':binding['run_id']}
        result = Worker(self.app, reply_remaining).run_once()
        self.assertEqual(result['outcome'],'succeeded',result)
        self.assertEqual(channel_for(self.app).count(), 2)
        self.assertEqual(len(self.app.store.list('job','f')), 1)
        self.assertFalse(Worker(self.app, reply_remaining).run_once()['ran'])
