"""One operation's persisted request identifies the step already sent."""
from copy import deepcopy
import unittest

import test_intraday_runtime as fixtures
from digital_tutor.scheduling import step_key, step_ready
from digital_tutor.contact_capacity import committed_contacts, committed_reminders
from digital_tutor.commitment_review import observe_commitment
from digital_tutor.domain import timestamp
from digital_tutor.tools import ToolRegistry


class OperationStepIdentityTests(unittest.TestCase):
    setUp = fixtures.IntradayRuntimeTests.setUp
    tearDown = fixtures.IntradayRuntimeTests.tearDown
    put = fixtures.IntradayRuntimeTests.put
    plan = fixtures.IntradayRuntimeTests.plan
    event = fixtures.IntradayRuntimeTests.event
    enqueue_step = fixtures.IntradayRuntimeTests.enqueue_step
    send_args = fixtures.IntradayRuntimeTests.send_args

    def test_actual_sent_request_blocks_same_step_but_not_a_different_step(self):
        event = self.event('original')
        self.plan([event['id']]);self.enqueue_step(event)
        claim = self.queue.claim('original-step')
        token = self.queue.session(claim)
        sent = ToolRegistry(self.app, token).call('wechat.send_message', self.send_args(event))
        self.assertTrue(sent['ok'], sent)
        operation = sent['result']['data']
        self.assertNotIn('step_id', operation)
        self.assertEqual(operation['request']['step_id'], step_key(event))
        self.assertFalse(step_ready(self.app, 'f', event))
        self.assertEqual(committed_contacts(self.app, 'f', '2026-10-05'), 1)
        self.assertEqual(committed_reminders(self.app, 'f', '2026-10-05', ['lesson-original']), {'lesson-original': 1})
        revised = deepcopy(event)
        revised['next_action']['purpose'] = 'An explicitly different approved step fixture.'
        self.assertNotEqual(step_key(event), step_key(revised))
        self.assertTrue(step_ready(self.app, 'f', revised))
        self.put('event', event['id'], revised)
        self.assertEqual(committed_contacts(self.app, 'f', '2026-10-05'), 2)
        self.assertEqual(committed_reminders(self.app, 'f', '2026-10-05', ['lesson-original']), {'lesson-original': 2})

    def test_legacy_top_level_is_fallback_only_and_never_overrides_request(self):
        event = self.event('original');self.plan([event['id']])
        current = step_key(event)
        historical = {'event_id': event['id'], 'stage': 'sent', 'step_id': current}
        self.put('operation', 'historical-operation', historical)
        self.assertFalse(step_ready(self.app, 'f', event))
        self.put('operation', 'historical-operation', {**historical, 'request': {'step_id': 'different'}})
        self.assertTrue(step_ready(self.app, 'f', event))
        self.put('operation', 'historical-operation', {**historical, 'step_id': 'different', 'request': {'step_id': current}})
        self.assertFalse(step_ready(self.app, 'f', event))
        self.put('operation', 'historical-operation', {**historical, 'request': {'step_id': None}})
        self.assertTrue(step_ready(self.app, 'f', event))

    def test_expiration_observes_request_identity_and_preserves_explicit_grant_fallback(self):
        event = self.event('original')
        grant = {'id': 'historical-grant', 'status': 'active', 'schedule_revision': 1,
            'authorized_step_key': step_key(event), 'valid_from': '2026-10-05T10:00:00+08:00',
            'valid_until': '2026-10-05T16:00:00+08:00'}
        event = self.put('event', event['id'], {**event, 'accepted_commitment': grant})
        self.app.clock.fixed = timestamp('2026-10-05T17:00:00+08:00')
        # Explicit original-operation fixture for the read-only expiration projection.
        operation = {'event_id': event['id'], 'stage': 'sent', 'dispatch_count': 1,
                     'request': {'step_id': grant['authorized_step_key']}}
        self.put('operation', 'original-operation', operation)
        self.assertEqual(observe_commitment(self.app, 'f', event)['execution_fact'], 'original_delivery_confirmed')
        conflicting = {**operation, 'step_id': grant['authorized_step_key'], 'request': {'step_id': 'other-step'}}
        self.put('operation', 'original-operation', conflicting)
        self.assertEqual(observe_commitment(self.app, 'f', event)['execution_fact'], 'no_recorded_external_execution')
        self.put('operation', 'original-operation', {**conflicting,
            'send_capability': {'commitment_id': grant['id'], 'schedule_revision': 1}})
        self.assertEqual(observe_commitment(self.app, 'f', event)['execution_fact'], 'original_delivery_confirmed')


if __name__ == '__main__': unittest.main()
