"""First-pass intent invariants; parent executes these on the isolated server."""
from copy import deepcopy
import unittest

import test_service_request_authorization as fixtures
from digital_tutor.scheduler import Worker


class FutureReplyInvariantTests(unittest.TestCase):
    setUp = fixtures.ServiceRequestAuthorizationTests.setUp
    tearDown = fixtures.ServiceRequestAuthorizationTests.tearDown
    key = fixtures.ServiceRequestAuthorizationTests.key
    call = fixtures.ServiceRequestAuthorizationTests.call
    applied = fixtures.ServiceRequestAuthorizationTests.applied
    rejected = fixtures.ServiceRequestAuthorizationTests.rejected
    business_snapshot = fixtures.ServiceRequestAuthorizationTests.business_snapshot
    raw = fixtures.ServiceRequestAuthorizationTests.raw
    interpretation = fixtures.ServiceRequestAuthorizationTests.interpretation
    proposal = fixtures.ServiceRequestAuthorizationTests.proposal

    def intents(self, future, requires_reply=False):
        return [{'intent_id': 'question', 'text': 'Please explain this current question.',
                 'requires_reply': True},
                {'intent_id': 'future', 'text': future, 'requires_reply': requires_reply,
                 'service_kind': 'future_delivery', 'service_authorization': future}]

    def test_invalid_future_rejects_entire_mixed_interpretation_without_receipt(self):
        future = 'Please send a separate text summary tomorrow, not today.'
        message, token = self.raw('Please explain this current question. ' + future)
        args = self.interpretation(message, self.intents(future))
        self.rejected(token, 'message.interpret', args, 'FUTURE_DELIVERY_REPLY_REQUIRED')
        current = self.app.store.get('message', message['id'], 'f')
        self.assertFalse(current['interpreted'])
        self.assertEqual(current['intents'], [])
        self.assertEqual(current['version'], message['version'])
        self.assertIsNone(self.app.store.connection.execute(
            'SELECT 1 FROM receipts WHERE family_id=? AND request_id=?',
            ('f', args['request_id'])).fetchone())
        self.assertEqual(self.app.store.list('pending', 'f'), [])

    def test_same_worker_run_can_correct_rejected_interpretation_and_request_admission(self):
        future = 'Please send a separate written summary later; we can agree the time afterward.'
        message, _ = self.raw('Please explain this current question. ' + future)
        args = self.interpretation(message, self.intents(future))
        captured = {}

        def service(job, tools):
            self.assertEqual(job['kind'], 'service')
            read = tools.call('wechat.read_messages', {'mode': 'all'})
            self.assertTrue(read['ok'], read)
            before = self.business_snapshot()
            failed = tools.call('message.interpret', args)
            self.assertFalse(failed['ok'], failed)
            self.assertEqual(failed['error']['code'], 'FUTURE_DELIVERY_REPLY_REQUIRED')
            self.assertEqual(self.business_snapshot(), before)
            corrected = deepcopy(args)
            corrected['intents'][1]['requires_reply'] = True
            accepted = tools.call('message.interpret', corrected)
            self.assertTrue(accepted['ok'], accepted)
            current = accepted['result']['data']
            proposed = tools.call('pending.propose_service', self.proposal(current, 'future'))
            self.assertTrue(proposed['ok'], proposed)
            pending = proposed['result']['data']
            captured['pending'] = pending
            outcome = tools.call('service.record_outcome', {
                'request_id': self.key(), 'outcome': 'waiting',
                'reason': 'The requested future delivery awaits its actual admission decision.',
                'remaining_work': ['Answer the current question.', 'Decide the requested future delivery.'],
                'resume_condition': 'Original pending receives its admission decision or new source arrives.',
                'refs': [{'kind': 'message', 'id': current['id'], 'version': current['version']},
                         {'kind': 'pending', 'id': pending['id'], 'version': pending['version']}]})
            self.assertTrue(outcome['ok'], outcome)
            return {'outcome_id': job['run_id']}

        result = Worker(self.app, service).run_once()
        self.assertEqual(result['outcome'], 'waiting', result)
        self.assertTrue(captured['pending']['await_decision'])
        self.assertEqual(captured['pending']['source_intent_id'], 'future')
        jobs = [job for job in self.app.store.list('job', 'f')
                if job.get('planning_reason') == 'admission']
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['pending_id'], captured['pending']['id'])
        current = self.app.store.get('message', message['id'], 'f')
        self.assertTrue(all(i['requires_reply'] and not i['reply_operation_refs'] for i in current['intents']))
        self.assertEqual(self.app.store.list('event', 'f'), [])
        self.assertEqual(self.app.store.list('operation', 'f'), [])

    def test_ordinary_nonreply_intent_remains_legal_and_immutable(self):
        message, token = self.raw('Thank you, there is no further request.')
        args = self.interpretation(message, [{'intent_id': 'thanks', 'text': message['body'],
                                             'requires_reply': False}])
        current = self.applied(token, 'message.interpret', args)
        self.assertFalse(current['intents'][0]['requires_reply'])
        replacement = self.interpretation(current, [{'intent_id': 'thanks', 'text': message['body'],
            'requires_reply': True, 'service_kind': 'future_delivery',
            'service_authorization': message['body']}])
        self.rejected(token, 'message.interpret', replacement, 'ALREADY_INTERPRETED')


if __name__ == '__main__':
    unittest.main()
