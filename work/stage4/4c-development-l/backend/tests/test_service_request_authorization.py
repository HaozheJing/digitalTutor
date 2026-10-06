"""Same-pass source intent authorization, without a classifier or model loop."""
from copy import deepcopy
import unittest

import test_intraday_contracts as fixtures
from digital_tutor.report_contract import message_source_digest
from digital_tutor.storage import digest


class ServiceRequestAuthorizationTests(unittest.TestCase):
    setUp = fixtures.IntradayContracts.setUp
    tearDown = fixtures.IntradayContracts.tearDown
    key = fixtures.IntradayContracts.key
    call = fixtures.IntradayContracts.call
    applied = fixtures.IntradayContracts.applied
    rejected = fixtures.IntradayContracts.rejected
    business_snapshot = fixtures.IntradayContracts.business_snapshot
    message = fixtures.IntradayContracts.message

    def raw(self, body):
        channel = self.app.open_session('f', 'channel')
        message = self.applied(channel, 'wechat.receive_message', {
            'external_id': self.key(), 'body': body, 'occurred_at': self.app.clock.iso()})
        return message, self.app.open_session('f', 'service')

    def interpretation(self, message, intents):
        return {'request_id': self.key(), 'message_id': message['id'],
                'expected_version': message['version'], 'intents': intents}

    def proposal(self, message, intent='request'):
        return {'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
                'source_message_id': message['id'], 'source_version': message['version'],
                'source_intent_id': intent, 'source_excerpt': message['body'],
                'request': 'Source-bound future text service',
                'requested_time': {'meaning': 'unspecified', 'source_text': ''}}

    def test_ordinary_missing_material_question_cannot_create_admission(self):
        message, token = self.message('What does the unavailable policy say about this question?')
        self.rejected(token, 'pending.propose_service', self.proposal(message), 'FUTURE_DELIVERY_NOT_AUTHORIZED')
        self.assertEqual(self.app.store.list('pending', 'f'), [])
        self.assertEqual(self.app.store.list('candidate_source', 'f'), [])
        self.assertFalse(any(j.get('planning_reason') == 'admission' for j in self.app.store.list('job', 'f')))
        current = self.app.store.get('message', message['id'], 'f')
        self.assertTrue(current['intents'][0]['requires_reply'])
        self.assertEqual(current['intents'][0]['reply_operation_refs'], [])

    def test_explicit_future_without_precise_time_can_be_recorded(self):
        body = 'Please send me a separate text summary later; we can agree the time afterward.'
        message, token = self.message(body, service_kind='future_delivery', service_authorization=body)
        pending = self.applied(token, 'pending.propose_service', self.proposal(message))
        self.assertEqual(pending['requested_time']['meaning'], 'unspecified')
        self.assertTrue(pending['await_decision'])
        self.assertEqual(self.app.store.list('event', 'f'), [])
        self.assertEqual(sum(j.get('planning_reason') == 'admission' for j in self.app.store.list('job', 'f')), 1)

    def test_two_intents_only_the_authorized_future_request_is_admissible(self):
        future = 'Please send a separate written summary later.'
        message, token = self.raw('What is the policy? ' + future)
        args = self.interpretation(message, [
            {'intent_id': 'question', 'text': 'What is the policy?'},
            {'intent_id': 'future', 'text': future, 'service_kind': 'future_delivery',
             'service_authorization': future}])
        message = self.applied(token, 'message.interpret', args)
        self.rejected(token, 'pending.propose_service', self.proposal(message, 'question'), 'FUTURE_DELIVERY_NOT_AUTHORIZED')
        pending = self.applied(token, 'pending.propose_service', self.proposal(message, 'future'))
        self.assertEqual(pending['source_intent_id'], 'future')
        original = self.app.store.get('message', message['id'], 'f')
        self.assertTrue(all(i['requires_reply'] and not i['reply_operation_refs'] for i in original['intents']))

    def test_future_classification_needs_nonblank_exact_source_authorization(self):
        message, token = self.raw('An ordinary question without the claimed future authorization.')
        for excerpt in (None, '', '   ', 'Please prepare a report tomorrow.'):
            args = self.interpretation(message, [{'intent_id': 'request', 'text': message['body'],
                'service_kind': 'future_delivery', 'service_authorization': excerpt}])
            self.rejected(token, 'message.interpret', args, 'SERVICE_AUTHORIZATION_REQUIRED')
        args = self.interpretation(message, [{'intent_id': 'request', 'text': message['body'],
            'service_kind': 'reply', 'service_authorization': message['body']}])
        self.rejected(token, 'message.interpret', args, 'SERVICE_AUTHORIZATION_SCOPE')

    def test_existing_reply_intent_cannot_be_reinterpreted_to_obtain_admission(self):
        message, token = self.message('An unanswered question whose source is currently missing.')
        args = self.interpretation(message, [{'intent_id': 'request', 'text': message['body'],
            'service_kind': 'future_delivery', 'service_authorization': message['body']}])
        self.rejected(token, 'message.interpret', args, 'ALREADY_INTERPRETED')
        self.rejected(token, 'pending.propose_service', self.proposal(message), 'FUTURE_DELIVERY_NOT_AUTHORIZED')

    def test_semantic_digest_preserves_legacy_default_and_binds_future_authorization(self):
        message, _ = self.message('Original request body.')
        old = deepcopy(message)
        old['intents'][0].pop('service_kind')
        old['intents'][0].pop('service_authorization')
        self.assertEqual(message_source_digest(old), message_source_digest(message))
        future = deepcopy(old)
        future['intents'][0].update(service_kind='future_delivery', service_authorization='Original request body.')
        self.assertNotEqual(message_source_digest(old), message_source_digest(future))
        changed = deepcopy(future)
        changed['intents'][0]['service_authorization'] = 'request body.'
        self.assertNotEqual(message_source_digest(future), message_source_digest(changed))
        future['intents'][0]['reply_operation_refs'] = ['derived-operation']
        future['intents'][0]['delegated_completion'] = {'authority': 'host_fixture'}
        future_without_effect = deepcopy(future)
        future_without_effect['intents'][0]['reply_operation_refs'] = []
        future_without_effect['intents'][0].pop('delegated_completion')
        self.assertEqual(message_source_digest(future), message_source_digest(future_without_effect))

    def test_default_interpret_receipt_preserves_historical_payload_digest(self):
        message, token = self.raw('An ordinary question.')
        args = self.interpretation(message, [{'intent_id': 'request', 'text': message['body'], 'requires_reply': True}])
        self.applied(token, 'message.interpret', args)
        row = self.app.store.connection.execute('SELECT digest FROM receipts WHERE family_id=? AND request_id=?',
                                               ('f', args['request_id'])).fetchone()
        self.assertEqual(row['digest'], digest({'command': 'interpret', 'payload': args}))
        explicit = deepcopy(args)
        explicit['intents'][0].update(service_kind='reply', service_authorization=None)
        before = self.business_snapshot()
        replay = self.call(token, 'message.interpret', explicit)
        self.assertTrue(replay['ok'], replay)
        self.assertEqual(replay['result']['outcome'], 'duplicate')
        self.assertEqual(self.business_snapshot(), before)


if __name__ == '__main__': unittest.main()
