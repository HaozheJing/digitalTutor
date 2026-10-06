"""Independent stage-two regression probes; synthetic families and temp databases only."""
import unittest
import time

import test_stage1 as baseline
from digital_tutor.application import Clock
from digital_tutor.jobs import JobQueue, set_enabled
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import dispatch
from digital_tutor.scheduler import Worker
from digital_tutor.tools import ToolRegistry
from digital_tutor.wechat import prepare_message


class Stage2ReviewTests(baseline.CoreTests):
    def test_cancelled_before_dispatch_can_be_prepared_again_next_window(self):
        message = self.incoming()
        first = prepare_message(self.app, self.service, self.reply(message))['data']
        self.app.clock = Clock('2026-10-03T21:00:00+08:00')
        cancelled = dispatch(self.app, 'f', first['id'])
        self.assertEqual(cancelled['stage'], 'cancelled')
        self.assertEqual(channel_for(self.app).count(), 0)
        self.app.clock = Clock('2026-10-04T09:00:00+08:00')
        retried = self.call(self.service, 'wechat.send_message', self.reply(message))['data']
        self.assertEqual(retried['stage'], 'sent')
        self.assertEqual(channel_for(self.app).count(), 1)
        self.assertEqual(self.app.store.get('operation', first['id'], 'f')['stage'], 'cancelled')

    def test_progress_cannot_finish_without_consuming_its_report(self):
        event_id = self.create()['a']
        proof = self.evidence(lesson_ref='lesson-a')
        report_id = self.report([event_id], [proof])
        result = Worker(self.app, lambda job, tools: {}).run_once()
        report = self.app.store.get('report', self.app.report_key('f', report_id, 1), 'f')
        job = self.app.store.get('job', 'progress:' + report['id'], 'f')
        self.assertIsNone(report['resolution'])
        self.assertNotEqual(job['status'], 'succeeded', result)
        self.assertTrue(self.app.event('f', event_id)['update_pending'])

    def test_stop_then_resume_does_not_restore_old_worker_capability(self):
        message = self.incoming(False)
        queue = JobQueue(self.app)
        claim = queue.claim('old-worker')
        token = queue.session(claim)
        set_enabled(self.app, False)
        set_enabled(self.app, True)
        result = ToolRegistry(self.app, token).call(
            'message.interpret', {'request_id': self.key(), 'message_id': message['id'],
                                  'expected_version': message['version'],
                                  'intents': [{'intent_id': 'old', 'text': '旧运行继续'}]})
        self.assertFalse(result['ok'], '恢复开关不能恢复停用前仍在运行的旧模型权限')
        self.assertFalse(self.app.store.get('message', message['id'], 'f')['interpreted'])

    def test_late_transport_return_does_not_downgrade_delivered_receipt(self):
        message = self.incoming()
        prepared = prepare_message(self.app, self.service, self.reply(message))['data']
        channel = channel_for(self.app)
        original_send = channel.send

        def send_with_early_receipt(operation, outcome):
            result = original_send(operation, outcome)
            self.call(self.channel, 'wechat.mock_receipt', {
                'request_id': self.key(), 'operation_id': operation['id'], 'stage': 'delivered'})
            return result

        channel.send = send_with_early_receipt
        final = dispatch(self.app, 'f', prepared['id'])
        self.assertEqual(final['stage'], 'delivered')
        self.assertEqual(channel.count(), 1)
        outbound = [m for m in self.app.store.list('message', 'f') if m['direction'] == 'outbound']
        self.assertEqual(len(outbound), 1)

    def test_handler_exception_after_stop_does_not_escape_worker(self):
        self.incoming(False)

        def stopped_handler(job, tools):
            set_enabled(self.app, False)
            raise RuntimeError('synthetic handler failure after stop')

        result = Worker(self.app, stopped_handler).run_once()
        self.assertTrue(result['ran'])
        self.assertIn(result['outcome'], {'failed', 'rejected'})
        self.assertIsNone(JobQueue(self.app).claim('disabled-worker'))

    def test_host_heartbeat_preserves_long_handler_lease(self):
        self.app.clock = Clock()
        self.app.service_start, self.app.service_end = 0, 24
        message = self.incoming(False)
        captured = []

        def slow_handler(job, tools):
            captured.append(tools)
            time.sleep(1.4)
            result = tools.call('message.interpret', {
                'request_id': self.key(), 'message_id': message['id'],
                'expected_version': message['version'],
                'intents': [{'intent_id': 'ack', 'text': '无需回复', 'requires_reply': False}]})
            self.assertTrue(result['ok'], result)
            return {}

        result = Worker(self.app, slow_handler, lease_seconds=1).run_once()
        self.assertEqual(result['outcome'], 'succeeded')
        self.assertEqual(captured[0].call('state.read', {})['error']['code'], 'RUN_REVOKED')


# Reuse only fixture helpers, without executing baseline tests a second time.
for _name in baseline.CoreTests.__dict__:
    if _name.startswith('test_'):
        setattr(Stage2ReviewTests, _name, None)
