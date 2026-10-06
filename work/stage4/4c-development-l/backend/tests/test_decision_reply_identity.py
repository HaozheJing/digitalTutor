"""Distinct host-confirmed decision phase; fixture code never calls a provider."""
from copy import deepcopy
import unittest

import test_request_responsibility as fixtures
from digital_tutor.domain import Send
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import dispatch
from digital_tutor.request_responsibility import acknowledgements, decision_reply_refs, deliveries, notified
from digital_tutor.scheduler import Worker
from digital_tutor.storage import digest
from digital_tutor.wechat import prepare_message


class DecisionReplyIdentityTests(unittest.TestCase):
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

    def args(self, message, body):
        return {'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
                'action_kind': 'reply', 'body': body,
                'reply_to': [{'message_id': message['id'], 'intent_id': 'request', 'resolves_intent': False}]}

    def outcome(self, job, tools, message, operation=None):
        current = self.app.store.get('message', message['id'], 'f')
        refs = [{'kind': 'message', 'id': current['id'], 'version': current['version']}]
        if operation:
            refs.append({'kind': 'operation', 'id': operation['id'], 'version': operation['version']})
        saved = self.ok(tools.call('service.record_outcome', {'request_id': self.key(), 'outcome': 'waiting',
            'reason': 'Original requested delivery remains outstanding.', 'refs': refs,
            'remaining_work': ['Original delivery'], 'resume_condition': 'Original decision or operation changes.'}))
        return saved

    def send_worker(self, message, body, stage='sent', retries=False):
        saved = {}
        self.app.mock_send_outcome = stage
        def service(job, tools):
            self.assertEqual(job['kind'], 'service')
            self.ok(tools.call('wechat.read_messages', {'mode': 'all'}))
            args = self.args(message, body)
            op = self.ok(tools.call('wechat.send_message', args))['data']
            saved['operation'] = op
            if retries:
                count = channel_for(self.app).count()
                for _ in range(2):
                    # Neither unrelated version nor a new request id is a new decision.
                    with self.app.store.transaction(): self.app.store.bump('f')
                    retry = self.ok(tools.call('wechat.send_message', self.args(message, body)))['data']
                    self.assertEqual(retry['id'], op['id'])
                    self.assertEqual(retry['dispatch_count'], 1)
                    self.assertEqual(channel_for(self.app).count(), count)
                before = self.business_snapshot()
                changed = tools.call('wechat.send_message', self.args(message, body + ' Different wording.'))
                self.assertEqual(changed['error']['code'], 'OPERATION_ALREADY_EXISTS', changed)
                self.assertEqual(self.business_snapshot(), before)
            saved['outcome'] = self.outcome(job, tools, message, op)
            return {'outcome_id': job['run_id']}
        try:
            self.assertEqual(Worker(self.app, service).run_once()['outcome'], 'waiting')
        finally:
            self.app.mock_send_outcome = 'sent'
        return saved

    def register_and_accept(self, registration_stage='sent'):
        pending, message, _ = self.pending()
        registered = self.send_worker(message, 'Registered; actual planning decision remains pending.', registration_stage)
        planner, args = self.admission(self.app.store.get('pending', pending['id'], 'f'))
        data = self.applied(planner, 'planner.commit_admission', args)
        return message, registered, data

    def test_single_pair_registration_then_real_decision_confirmation_are_distinct(self):
        message, registered, data = self.register_and_accept()
        self.assertEqual(registered['operation']['decision_reply_refs'], [])
        self.assertEqual(registered['outcome']['delegation_acknowledgements'], [])
        confirmed = self.send_worker(message, 'Original requested text has now been formally accepted.')
        op = confirmed['operation']
        self.assertNotEqual(op['purpose_key'], registered['operation']['purpose_key'])
        self.assertNotEqual(op['id'], registered['operation']['id'])
        self.assertEqual(op['decision_reply_refs'][0]['event_id'], data['event_ids']['new-request'])
        self.assertTrue(confirmed['outcome']['delegation_acknowledgements'])
        self.assertEqual(channel_for(self.app).count(), 2)
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])
        self.assertTrue(notified(self.app, 'f', deliveries(self.app, 'f')[0]))
        self.assertEqual(self.app.store.get('operation', registered['operation']['id'], 'f')['purpose_key'],
                         registered['operation']['purpose_key'])
        self.assertFalse(Worker(self.app, lambda *_: self.fail('Already confirmed')).run_once()['ran'])

    def test_sent_confirmation_retries_cannot_send_again_or_change_wording(self):
        message, _, _ = self.register_and_accept()
        self.send_worker(message, 'Formal original arrangement confirmation.', retries=True)
        self.assertEqual(channel_for(self.app).count(), 2)

    def test_unknown_confirmation_retries_keep_original_operation(self):
        message, _, _ = self.register_and_accept()
        saved = self.send_worker(message, 'Formal original arrangement confirmation.', stage='unknown', retries=True)
        self.assertEqual(saved['operation']['stage'], 'unknown')
        self.assertEqual(saved['outcome']['delegation_acknowledgements'], [])
        self.assertEqual(len(self.app.store.list('operation', 'f')), 2)

    def test_accepted_confirmation_retries_keep_original_operation(self):
        message, _, _ = self.register_and_accept()
        saved = self.send_worker(message, 'Formal original arrangement confirmation.', stage='accepted', retries=True)
        self.assertEqual(saved['operation']['stage'], 'accepted')
        self.assertEqual(saved['outcome']['delegation_acknowledgements'], [])
        self.assertEqual(len(self.app.store.list('operation', 'f')), 2)

    def test_unknown_registration_still_preoccupies_pair_after_admission(self):
        message, registered, _ = self.register_and_accept(registration_stage='unknown')
        # Unchanged unknown wait correctly does not wake. A real separate inbound
        # supplies a service run; its scope still cannot bypass the old uncertainty.
        new, _ = self.message('A separate current question.')
        def service(job, tools):
            self.assertEqual(job['message_id'], new['id'])
            self.ok(tools.call('wechat.read_messages', {'mode': 'all'}))
            denied = tools.call('wechat.send_message', self.args(message, 'Formal acceptance confirmation.'))
            self.assertEqual(denied['error']['code'], 'OPERATION_UNCERTAIN', denied)
            self.assertEqual(channel_for(self.app).count(), 1)
            self.outcome(job, tools, new)
            return {'outcome_id': job['run_id']}
        self.assertEqual(Worker(self.app, service).run_once()['outcome'], 'waiting')
        old = self.app.store.get('operation', registered['operation']['id'], 'f')
        self.assertEqual(old['stage'], 'unknown')
        self.assertEqual(old['decision_reply_refs'], [])

    def test_queued_registration_cannot_be_relabelled_confirmation_after_decision(self):
        pending, message, _ = self.pending()
        def service(job, tools):
            self.ok(tools.call('wechat.read_messages', {'mode': 'all'}))
            op = prepare_message(self.app, tools.token, self.args(message, 'Only registered, not yet decided.'))['data']
            self.assertEqual(op['decision_reply_refs'], [])
            # Explicit adversarial interleaving via the real commit transaction;
            # not a second production queue or a mutated operation/capability.
            planner, args = self.admission(pending)
            self.applied(planner, 'planner.commit_admission', args)
            cancelled = dispatch(self.app, 'f', op['id'])
            self.assertEqual(cancelled['stage'], 'cancelled')
            self.assertEqual(cancelled['cancel_reason'], 'DECISION_REPLY_STALE')
            self.assertEqual(cancelled['dispatch_count'], 0)
            self.assertEqual(cancelled['purpose_key'], op['purpose_key'])
            self.assertEqual(acknowledgements(self.app, 'f', job['run_id']), [])
            self.assertEqual(channel_for(self.app).count(), 0)
            self.outcome(job, tools, message)
            return {'outcome_id': job['run_id']}
        self.assertEqual(Worker(self.app, service).run_once()['outcome'], 'waiting')

    def test_phase_is_exact_source_bound_and_unavailable_in_legacy(self):
        message, _, _ = self.register_and_accept()
        cmd = Send.model_validate(self.args(message, 'Formal confirmation.'))
        actual = decision_reply_refs(self.app, 'f', cmd)
        self.assertEqual(len(actual), 1)
        self.assertEqual(decision_reply_refs(self.app, 'g', cmd), [])
        other, _ = self.message('A separate ordinary question.')
        self.assertEqual(decision_reply_refs(self.app, 'f', Send.model_validate(self.args(other, 'Ordinary reply.'))), [])
        with self.app.store.transaction(): self.app.store.bump('f')
        self.assertEqual(decision_reply_refs(self.app, 'f', cmd), actual)
        self.app.strict_runtime = False
        self.assertEqual(decision_reply_refs(self.app, 'f', cmd), [])
        self.app.strict_runtime = True
        with self.app.store.transaction():
            current = self.app.store.get('message', message['id'], 'f')
            current['body'] += ' Source corrected externally.'
            self.app.store.put('message', current['id'], 'f', current, current['version'])
        self.assertEqual(decision_reply_refs(self.app, 'f', cmd), [])

    def test_only_existing_host_ack_preserves_legacy_confirmation(self):
        message, _, _ = self.register_and_accept()
        saved = self.send_worker(message, 'Formal accepted arrangement confirmation.')
        with self.app.store.transaction():
            op = self.app.store.get('operation', saved['operation']['id'], 'f')
            op.pop('decision_reply_refs')  # Explicit historical pre-field fixture.
            self.app.store.put('operation', op['id'], 'f', op, op['version'])
        item = deliveries(self.app, 'f')[0]
        self.assertTrue(notified(self.app, 'f', item))  # Existing host ack is preserved.
        self.assertEqual(acknowledgements(self.app, 'f', saved['outcome']['run_id']), [])
        with self.app.store.transaction():
            outcome = self.app.store.get('run_outcome', saved['outcome']['id'], 'f')
            outcome['delegation_acknowledgements'] = []
            self.app.store.put('run_outcome', outcome['id'], 'f', outcome, outcome['version'])
        # Merely observing a decided pending in an old outcome must not promote
        # an untagged old partial operation into a new confirmed decision.
        self.assertFalse(notified(self.app, 'f', deliveries(self.app, 'f')[0]))


if __name__ == '__main__':
    unittest.main()
