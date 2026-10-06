"""Real Worker/tool effects for original-intent ownership, without provider calls."""
from copy import deepcopy
import unittest

import test_request_responsibility as fixtures
from digital_tutor.application import Clock
from digital_tutor.jobs import JobQueue
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import reconcile
from digital_tutor.report_contract import message_source_digest
from digital_tutor.request_responsibility import (
    actual_delivery_started, deliveries, held_intents, notified)
from digital_tutor.runtime_config import reply_scope
from digital_tutor.scheduler import Worker
from digital_tutor.scheduling import step_key


class ActualDeliveryHoldTests(unittest.TestCase):
    setUp = fixtures.RequestResponsibilityTests.setUp
    tearDown = fixtures.RequestResponsibilityTests.tearDown
    key = fixtures.RequestResponsibilityTests.key
    call = fixtures.RequestResponsibilityTests.call
    applied = fixtures.RequestResponsibilityTests.applied
    rejected = fixtures.RequestResponsibilityTests.rejected
    business_snapshot = fixtures.RequestResponsibilityTests.business_snapshot
    message = fixtures.RequestResponsibilityTests.message
    pending = fixtures.RequestResponsibilityTests.pending
    admission = fixtures.RequestResponsibilityTests.admission
    accepted = fixtures.RequestResponsibilityTests.accepted
    ok = fixtures.RequestResponsibilityTests.ok
    acknowledge = fixtures.RequestResponsibilityTests.acknowledge
    enqueue_step = fixtures.RequestResponsibilityTests.enqueue_step
    consume = fixtures.RequestResponsibilityTests.consume

    def prepare_without_confirmation(self, other_intent=False):
        pending, message, _ = self.pending()
        if other_intent:
            # Historical multi-intent source fixture before any acceptance; no
            # change to an accepted source to manufacture later eligibility.
            with self.app.store.transaction():
                message = self.app.store.get('message', message['id'], 'f')
                message['intents'].append({'intent_id': 'ordinary', 'text': 'A separate current question',
                    'requires_reply': True, 'service_kind': 'reply', 'service_authorization': None,
                    'reply_operation_refs': []})
                message = self.app.store.put('message', message['id'], 'f', message, message['version'])
                source = self.app.store.get('candidate_source', pending['candidate_source_id'], 'f')
                source.update(source_message_digest=message_source_digest(message),
                              source_message_version=message['version'])
                self.app.store.put('candidate_source', source['id'], 'f', source, source['version'])
                self.app.store.bump('f')
        token, args = self.admission(pending)
        data = self.applied(token, 'planner.commit_admission', args)
        event = self.app.event('f', data['event_ids']['new-request'])
        def wait(job, tools):
            self.assertEqual(job['kind'], 'service')
            current = self.app.store.get('message', message['id'], 'f')
            self.ok(tools.call('service.record_outcome', {'request_id': self.key(), 'outcome': 'waiting',
                'reason': 'Accepted delivery remains outstanding; no confirmation was sent.',
                'remaining_work': ['Original delivery'] + (['Separate ordinary question'] if other_intent else []),
                'resume_condition': 'The original scheduled step runs or new input arrives.',
                'refs': [{'kind': 'message', 'id': current['id'], 'version': current['version']}]}))
            return {'outcome_id': job['run_id']}
        self.assertEqual(Worker(self.app, wait).run_once()['outcome'], 'waiting')
        self.assertFalse(notified(self.app, 'f', deliveries(self.app, 'f')[0]))
        self.app.mode = 'full'
        self.app.clock = Clock('2026-10-04T15:00:15+08:00')
        return event, message

    def step_report(self, event, stage='sent', other_intent=False):
        self.enqueue_step(event)
        self.app.mock_send_outcome = stage
        saved = {}
        def service(job, tools):
            self.assertEqual(job['kind'], 'step')
            op = self.ok(tools.call('wechat.send_message', {'request_id': self.key(),
                'expected_family_version': self.app.store.family('f')['version'],
                'action_kind': 'deliver_feedback', 'event_id': event['id'],
                'step_id': step_key(event), 'part_id': 'requested-text',
                'body': 'Actual synthetic text delivery under the original step.'}))['data']
            refs = [{'kind': 'operation', 'id': op['id'], 'version': op['version']}]
            report = self.ok(tools.call('progress.submit', {
                'request_id': self.key(), 'report_id': self.key('report'), 'event_ids': [event['id']],
                'facts': ['Program fixture: observe the original channel operation.'], 'evidence_refs': refs,
                'completion_assessments': [{'event_id': event['id'], 'complete': stage == 'sent',
                    'criterion': 'Original requested text reaches its required channel stage.',
                    'reason': 'The actual operation is the evidence.', 'evidence_refs': refs}]}))['data']
            outcome = {'request_id': self.key(), 'outcome': 'waiting',
                'reason': 'Original delivery report awaits progress; no repeated reply.',
                'remaining_work': ['Progress consumes the original report'] + (['Separate ordinary question'] if other_intent else []),
                'resume_condition': 'Original report consumed or original operation receipt changes.',
                'refs': refs + [{'kind': 'report', 'id': report['id'], 'version': report['version']}]}
            self.ok(tools.call('service.record_outcome', outcome))
            saved.update(operation=op, report=report)
            return {'outcome_id': job['run_id']}
        try:
            self.assertEqual(Worker(self.app, service).run_once()['outcome'], 'waiting')
        finally:
            self.app.mock_send_outcome = 'sent'
        return saved

    def test_sent_without_confirmation_holds_old_service_until_one_progress_completes(self):
        event, original = self.prepare_without_confirmation()
        saved = self.step_report(event)
        item = deliveries(self.app, 'f')[0]
        self.assertFalse(notified(self.app, 'f', item))
        self.assertTrue(actual_delivery_started(self.app, 'f', item))
        self.assertEqual(held_intents(self.app, 'f'), {(original['id'], 'request')})
        self.assertEqual(self.app.store.get('message', original['id'], 'f')['intents'][0]['reply_operation_refs'], [])
        # consume asserts the next real Worker claim is progress, never service.
        result, worker = self.consume(event, saved['report'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(worker['outcome'], 'succeeded')
        self.assertEqual(len(self.app.store.list('report', 'f')), 1)
        self.assertEqual(len(self.app.event('f', event['id'])['completion_history']), 1)
        current = self.app.store.get('message', original['id'], 'f')['intents'][0]
        self.assertEqual(current['reply_operation_refs'], [saved['operation']['id']])
        count, version = channel_for(self.app).count(), self.app.store.family('f')['version']
        for _ in range(5):
            self.assertFalse(Worker(self.app, lambda *_: self.fail('Duplicate service/report')).run_once()['ran'])
        self.assertEqual(channel_for(self.app).count(), count)
        self.assertEqual(self.app.store.family('f')['version'], version)

    def test_unknown_ack_still_recovers_same_job_after_delivery_and_progress(self):
        event, _, original, _, _ = self.accepted()
        ack = self.acknowledge(original, stage='unknown')['operation']
        self.app.mode = 'full'
        self.app.clock = Clock('2026-10-04T15:00:15+08:00')
        saved = self.step_report(event)
        result, _ = self.consume(event, saved['report'])
        self.assertTrue(result['ok'], result)
        intent = self.app.store.get('message', original['id'], 'f')['intents'][0]
        self.assertEqual(intent['reply_operation_refs'], [])
        self.assertEqual(intent['delegated_delivery_verified']['operation_ids'], [saved['operation']['id']])
        self.assertFalse(Worker(self.app, lambda *_: self.fail('Unchanged unknown ack')).run_once()['ran'])
        count = channel_for(self.app).count()
        verified = reconcile(self.app, 'f', ack['id'])
        def recover(job, tools):
            self.assertEqual(job['id'], 'message:' + original['id'])
            self.assertTrue(job['recovery_only'])
            context = self.ok(tools.call('context.read', {}))
            self.assertFalse(context['run_capabilities']['text_reply'])
            current = self.app.store.get('message', original['id'], 'f')
            outcome = self.ok(tools.call('service.record_outcome', {'request_id': self.key(),
                'outcome': 'action_taken', 'reason': 'Account existing delivery after original acknowledgement is known.',
                'refs': [{'kind': 'message', 'id': current['id'], 'version': current['version']},
                         {'kind': 'operation', 'id': verified['id'], 'version': verified['version']}]}))
            self.assertTrue(outcome['settled_delegated_request_refs'])
            return {'outcome_id': job['run_id']}
        self.assertEqual(Worker(self.app, recover).run_once()['outcome'], 'succeeded')
        self.assertEqual(channel_for(self.app).count(), count)
        self.assertEqual(len(self.app.store.list('report', 'f')), 1)

    def test_unknown_delivery_is_owned_by_original_step_recovery(self):
        event, original = self.prepare_without_confirmation()
        saved = self.step_report(event, stage='unknown')
        self.assertIn((original['id'], 'request'), held_intents(self.app, 'f'))
        result, _ = self.consume(event, saved['report'], done=False)
        self.assertTrue(result['ok'], result)
        self.assertIsNone(JobQueue(self.app).claim('unchanged-delivery'))
        count = channel_for(self.app).count()
        op = reconcile(self.app, 'f', saved['operation']['id'])
        self.assertEqual(op['stage'], 'sent')
        claim = JobQueue(self.app).claim('original-delivery-receipt')
        self.assertEqual(claim['job']['id'], 'original-step')
        self.assertTrue(claim['job']['recovery_only'])
        token = JobQueue(self.app).session(claim)
        _, cap = self.app.auth(token, {'service'})
        self.assertTrue(cap['no_contact'])
        self.assertEqual(cap['recovery_operation_ids'], [op['id']])
        self.assertEqual(channel_for(self.app).count(), count)
        self.assertEqual(self.app.store.get('message', original['id'], 'f')['intents'][0]['reply_operation_refs'], [])

    def test_non_delivery_or_unadmitted_operations_do_not_hold(self):
        event, original = self.prepare_without_confirmation()
        saved = self.step_report(event)
        op = saved['operation']
        variants = [{'stage': s} for s in ('queued', 'failed', 'cancelled')]
        variants += [{'part_id': 'wrong-part'}, {'action_kind': 'clarify_existing'},
                     {'event_id': 'other-event'}, {'recipient': 'mock:other'},
                     {'requirement_revision': event['requirement_revision'] + 1},
                     {'dispatch_count': 0}, {'admitted_at': None},
                     {'send_capability': {**op['send_capability'], 'commitment_id': 'wrong-grant'}}]
        for change in variants:
            with self.subTest(change=change):
                with self.app.store.transaction():
                    current = self.app.store.get('operation', op['id'], 'f')
                    changed = {**deepcopy(op), **change}
                    self.app.store.put('operation', op['id'], 'f', changed, current['version'])
                self.assertFalse(actual_delivery_started(self.app, 'f', deliveries(self.app, 'f')[0]))
                self.assertNotIn((original['id'], 'request'), held_intents(self.app, 'f'))

    def test_other_intent_and_new_message_remain_service_eligible(self):
        event, original = self.prepare_without_confirmation(other_intent=True)
        self.step_report(event, other_intent=True)
        self.assertEqual(reply_scope(self.app, 'f'), {original['id']: ['ordinary']})
        claim = JobQueue(self.app).claim('ordinary')
        self.assertEqual(claim['job']['message_id'], original['id'])
        token = JobQueue(self.app).session(claim)
        _, cap = self.app.auth(token, {'service'})
        self.assertEqual(cap['reply_scope'], {original['id']: ['ordinary']})
        self.ok(self.call(token, 'wechat.read_messages', {'mode': 'all'}))
        ordinary_op = self.ok(self.call(token, 'wechat.send_message', {'request_id': self.key(),
            'expected_family_version': self.app.store.family('f')['version'], 'action_kind': 'reply',
            'body': 'Answer to the separate ordinary question, not a second delivery.',
            'reply_to': [{'message_id': original['id'], 'intent_id': 'ordinary'}]}))['data']
        current = self.app.store.get('message', original['id'], 'f')
        self.ok(self.call(token, 'service.record_outcome', {'request_id': self.key(),
            'outcome': 'action_taken', 'reason': 'Separate ordinary intent answered by its own real reply.',
            'refs': [{'kind': 'message', 'id': current['id'], 'version': current['version']},
                     {'kind': 'operation', 'id': ordinary_op['id'], 'version': ordinary_op['version']}]}))
        JobQueue(self.app).finish(claim, 'waiting', reason='Only the original Event delivery remains')
        # A separate new inbound remains priority over progress or the held request.
        new, _ = self.message('A new ordinary question.')
        next_claim = JobQueue(self.app).claim('new-input')
        self.assertEqual(next_claim['job']['message_id'], new['id'])
        next_token = JobQueue(self.app).session(next_claim)
        _, next_cap = self.app.auth(next_token, {'service'})
        self.assertIn(new['id'], next_cap['reply_scope'])
        self.assertNotIn('request', next_cap['reply_scope'].get(original['id'], []))


if __name__ == '__main__':
    unittest.main()
