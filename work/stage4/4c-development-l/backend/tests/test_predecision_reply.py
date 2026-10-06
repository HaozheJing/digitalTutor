"""Planning registration is partial until a real decision; server-only execution."""
from copy import deepcopy
import unittest

import test_intraday_contracts as fixtures
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import dispatch
from digital_tutor.scheduler import Worker
from digital_tutor.wechat import prepare_message


class PredecisionReplyTests(unittest.TestCase):
    setUp = fixtures.IntradayContracts.setUp
    tearDown = fixtures.IntradayContracts.tearDown
    key = fixtures.IntradayContracts.key
    call = fixtures.IntradayContracts.call
    applied = fixtures.IntradayContracts.applied
    rejected = fixtures.IntradayContracts.rejected
    business_snapshot = fixtures.IntradayContracts.business_snapshot
    message = fixtures.IntradayContracts.message
    pending = fixtures.IntradayContracts.pending
    admission = fixtures.IntradayContracts.admission
    accepted = fixtures.IntradayContracts.accepted
    makeup_admission = fixtures.IntradayContracts.makeup_admission

    def ok(self, result):
        self.assertTrue(result['ok'], result)
        return result['result']

    def send_args(self, message, resolves=True, intent='request'):
        return {'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
                'action_kind': 'reply', 'body': 'Synthetic response to this original intent.',
                'reply_to': [{'message_id': message['id'], 'intent_id': intent, 'resolves_intent': resolves}]}

    def finish_outcome(self, job, tools, message, operation=None, waiting=True):
        current = self.app.store.get('message', message['id'], 'f')
        refs = [{'kind': 'message', 'id': current['id'], 'version': current['version']}]
        if operation:
            refs.append({'kind': 'operation', 'id': operation['id'], 'version': operation['version']})
        args = {'request_id': self.key(), 'outcome': 'waiting' if waiting else 'action_taken',
                'reason': 'Actual program fixture response; pending responsibility is tracked separately.', 'refs': refs}
        if waiting:
            args.update(remaining_work=['Original planning decision or delivery'],
                        resume_condition='Original pending decision or actual delivery changes.')
        self.ok(tools.call('service.record_outcome', args))
        return {'outcome_id': job['run_id']}

    def assert_worker_send(self, message, resolves=True, expected_error=None, fallback_partial=False):
        captured = {}
        def service(job, tools):
            self.assertEqual(job['kind'], 'service')
            self.ok(tools.call('wechat.read_messages', {'mode': 'all'}))
            before = self.business_snapshot()
            response = tools.call('wechat.send_message', self.send_args(message, resolves))
            if expected_error:
                self.assertFalse(response['ok'], response)
                self.assertEqual(response['error']['code'], expected_error)
                self.assertEqual(self.business_snapshot(), before)
                if fallback_partial:
                    response = tools.call('wechat.send_message', self.send_args(message, False))
                else:
                    return self.finish_outcome(job, tools, message)
            captured['operation'] = self.ok(response)['data']
            return self.finish_outcome(job, tools, message, captured['operation'],
                                       waiting=bool(expected_error or not resolves))
        captured['worker'] = Worker(self.app, service).run_once()
        return captured

    def test_passive_registration_rejects_final_and_preserves_partial_responsibility(self):
        pending, message, _ = self.pending()
        self.app.mode = 'passive'
        saved = self.assert_worker_send(message, expected_error='PLANNING_DECISION_PENDING', fallback_partial=True)
        intent = self.app.store.get('message', message['id'], 'f')['intents'][0]
        self.assertEqual(intent['reply_operation_refs'], [])
        self.assertEqual(intent['partial_reply_operation_refs'], [saved['operation']['id']])
        self.assertTrue(self.app.store.get('pending', pending['id'], 'f')['await_decision'])
        self.assertEqual(self.app.store.list('event', 'f'), [])
        self.assertEqual(channel_for(self.app).count(), 1)
        for _ in range(3):
            self.assertFalse(Worker(self.app, lambda *_: self.fail('Passive must not run Planner')).run_once()['ran'])

    def test_queued_old_capability_rechecks_new_pending_before_dispatch(self):
        body = 'Please send a separate written summary later.'
        message, _ = self.message(body, service_kind='future_delivery', service_authorization=body)
        def service(job, tools):
            self.ok(tools.call('wechat.read_messages', {'mode': 'all'}))
            # Valid queued reply predates registration. No direct mutation of its
            # request or cap: dispatch must recheck the new actual pending.
            op = prepare_message(self.app, tools.token, self.send_args(message))['data']
            pending = self.ok(tools.call('pending.propose_service', {
                'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
                'source_message_id': message['id'], 'source_version': message['version'],
                'source_intent_id': 'request', 'source_excerpt': body, 'request': 'Requested later text',
                'requested_time': {'meaning': 'unspecified', 'source_text': ''}}))['data']
            cancelled = dispatch(self.app, 'f', op['id'])
            self.assertEqual(cancelled['stage'], 'cancelled')
            self.assertEqual(cancelled['cancel_reason'], 'PLANNING_DECISION_PENDING')
            self.assertEqual(cancelled['dispatch_count'], 0)
            self.assertTrue(pending['await_decision'])
            self.assertEqual(channel_for(self.app).count(), 0)
            return self.finish_outcome(job, tools, message)
        self.assertEqual(Worker(self.app, service).run_once()['outcome'], 'waiting')
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])

    def test_goal_registration_waits_but_real_admission_allows_final_confirmation(self):
        planner, args = self.makeup_admission()
        pending = self.app.store.get('pending', args['pending_id'], 'f')
        message = self.app.store.get('message', pending['source_message_id'], 'f')
        self.assert_worker_send(message, expected_error='PLANNING_DECISION_PENDING', fallback_partial=True)
        # The reply changed the family version. A real fresh Planner binding,
        # not a weakened version check, submits the original candidate decision.
        planner, args = self.admission(self.app.store.get('pending', pending['id'], 'f'))
        args['new_events'][0]['next_action'] = {
            'action_kind': 'remind_makeup', 'target_refs': ['synthetic-lesson-1'],
            'purpose': 'Remind the accepted original lesson.', 'execute_at': '2026-10-04T15:00:00+08:00'}
        data = self.applied(planner, 'planner.commit_admission', args)
        saved = self.assert_worker_send(message)
        self.assertEqual(saved['worker']['outcome'], 'succeeded')
        current = self.app.store.get('message', message['id'], 'f')['intents'][0]
        self.assertEqual(current['reply_operation_refs'], [saved['operation']['id']])
        event = self.app.event('f', data['event_ids']['new-request'])
        self.assertEqual(event['completion']['kind'], 'goal')
        self.assertEqual(event['status'], 'pending')

    def test_accepted_delivery_keeps_existing_final_reply_guard(self):
        _, _, message, _, _ = self.accepted()
        saved = self.assert_worker_send(message, expected_error='DELIVERY_RESPONSIBILITY_DELEGATED', fallback_partial=True)
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])
        self.assertFalse(saved['operation']['reply_to'][0]['resolves_intent'])

    def test_real_retained_or_rejected_decision_and_cancelled_pending_allow_reply(self):
        for state in ('retained', 'not_adopted', 'cancelled'):
            with self.subTest(state=state):
                # Distinct source per iteration. Cancellation has no command in
                # this version, so its already-terminal state is a named fixture.
                body = 'Please provide later text for separate request ' + state + '.'
                message, token = self.message(body, service_kind='future_delivery', service_authorization=body)
                pending = self.applied(token, 'pending.propose_service', {
                    'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
                    'source_message_id': message['id'], 'source_version': message['version'],
                    'source_intent_id': 'request', 'source_excerpt': body, 'request': 'Separate requested text',
                    'requested_time': {'meaning': 'unspecified', 'source_text': ''}})
                if state == 'cancelled':
                    with self.app.store.transaction():
                        pending.update(disposition='cancelled', await_decision=False)
                        self.app.store.put('pending', pending['id'], 'f', pending, pending['version'])
                        self.app.store.bump('f')
                else:
                    planner, args = self.admission(pending, accepted=False)
                    for field in ('candidate_decisions', 'pending_decisions'):
                        args[field][0]['disposition'] = state
                    self.applied(planner, 'planner.commit_admission', args)
                saved = self.assert_worker_send(message)
                self.assertEqual(saved['worker']['outcome'], 'succeeded')
                self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'],
                                 [saved['operation']['id']])

    def test_same_message_ordinary_intent_can_finish_without_clearing_future(self):
        future = 'Please send a separate written summary later.'
        channel = self.app.open_session('f', 'channel')
        message = self.applied(channel, 'wechat.receive_message', {'external_id': self.key(),
            'body': 'A current question. ' + future, 'occurred_at': self.app.clock.iso()})
        def service(job, tools):
            self.ok(tools.call('wechat.read_messages', {'mode': 'all'}))
            current = self.ok(tools.call('message.interpret', {'request_id': self.key(),
                'message_id': message['id'], 'expected_version': message['version'], 'intents': [
                    {'intent_id': 'ordinary', 'text': 'A current question.'},
                    {'intent_id': 'future', 'text': future, 'service_kind': 'future_delivery',
                     'service_authorization': future, 'requires_reply': True}]}))['data']
            self.ok(tools.call('pending.propose_service', {'request_id': self.key(),
                'expected_family_version': self.app.store.family('f')['version'],
                'source_message_id': current['id'], 'source_version': current['version'],
                'source_intent_id': 'future', 'source_excerpt': current['body'], 'request': 'Requested later text',
                'requested_time': {'meaning': 'unspecified', 'source_text': ''}}))
            args = self.send_args(message, intent='ordinary')
            args['reply_to'].append({'message_id': message['id'], 'intent_id': 'future', 'resolves_intent': False})
            op = self.ok(tools.call('wechat.send_message', args))['data']
            return self.finish_outcome(job, tools, message, op)
        self.assertEqual(Worker(self.app, service).run_once()['outcome'], 'waiting')
        ordinary, future_intent = self.app.store.get('message', message['id'], 'f')['intents']
        self.assertTrue(ordinary['reply_operation_refs'])
        self.assertEqual(future_intent['reply_operation_refs'], [])
        self.assertTrue(future_intent['partial_reply_operation_refs'])


if __name__ == '__main__':
    unittest.main()
