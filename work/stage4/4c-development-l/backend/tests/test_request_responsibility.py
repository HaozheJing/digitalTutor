"""Program contract fixtures only; real acceptance models must author their own reports."""
from datetime import timedelta
from copy import deepcopy
import unittest

import test_intraday_contracts as fixtures
from digital_tutor.application import Clock
from digital_tutor.domain import BusinessError, Send
from digital_tutor.jobs import JobQueue
from digital_tutor.mock_channel import channel_for
from digital_tutor.request_responsibility import held_intents, projection, settle_completed
from digital_tutor.runtime_config import reply_scope
from digital_tutor.scheduler import Worker
from digital_tutor.scheduling import step_key
from digital_tutor.send_policy import check_send


class RequestResponsibilityTests(unittest.TestCase):
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
    baseline = fixtures.IntradayContracts.baseline

    def ok(self, result):
        self.assertTrue(result['ok'], result)
        return result['result']

    def acknowledge(self, message, stage='sent'):
        self.app.mock_send_outcome = stage
        captured = {}
        def service(job, tools):
            self.assertEqual(job['kind'], 'service')
            self.ok(tools.call('wechat.read_messages', {'mode': 'all'}))
            op = self.ok(tools.call('wechat.send_message', {
                'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
                'action_kind': 'reply', 'body': 'The original arrangement is accepted; delivery remains pending.',
                'reply_to': [{'message_id': message['id'], 'intent_id': 'request', 'resolves_intent': False}]}))['data']
            captured['operation'] = op
            current = self.app.store.get('message', message['id'], 'f')
            outcome = self.ok(tools.call('service.record_outcome', {'request_id': self.key(), 'outcome': 'waiting',
                'reason': 'Arrangement acknowledgement is not the delivery.', 'remaining_work': ['Original delivery'],
                'resume_condition': 'Original accepted Event executes with its real authority.',
                'refs': [{'kind': 'message', 'id': current['id'], 'version': current['version']},
                         {'kind': 'operation', 'id': op['id'], 'version': op['version']}]}))
            captured['outcome'] = outcome
            return {'outcome_id': job['run_id']}
        result = Worker(self.app, service).run_once()
        self.assertEqual(result['outcome'], 'waiting', result)
        self.app.mock_send_outcome = 'sent'
        return captured

    def setup_delivery(self):
        event, pending, message, _, _ = self.accepted()
        self.acknowledge(message)
        self.app.mode = 'full'
        self.app.clock = Clock('2026-10-04T15:00:15+08:00')
        return event, pending, message

    def enqueue_step(self, event):
        with self.app.store.transaction():
            JobQueue(self.app).enqueue('f', 'original-step', {'kind': 'step', 'event_id': event['id'],
                'event_version': event['version'], 'step_key': step_key(event)})

    def run_step_report(self, event, send=True, stage='sent', complete=True):
        self.enqueue_step(event)
        self.app.mock_send_outcome = stage
        saved = {}
        def service(job, tools):
            self.assertEqual(job['kind'], 'step')
            refs = []
            if send:
                op = self.ok(tools.call('wechat.send_message', {'request_id': self.key(),
                    'expected_family_version': self.app.store.family('f')['version'], 'action_kind': 'deliver_feedback',
                    'event_id': event['id'], 'step_id': step_key(event), 'part_id': 'requested-text',
                    'body': 'Synthetic requested text, actually sent under the original step.'}))['data']
                refs.append({'kind': 'operation', 'id': op['id'], 'version': op['version']})
                saved['operation'] = op
            else:
                message = self.app.store.get('message', event['accepted_commitment']['source_message_ref']['id'], 'f')
                refs.append({'kind': 'message', 'id': message['id'], 'version': message['version']})
            args = {'request_id': self.key(), 'report_id': self.key('report'), 'event_ids': [event['id']],
                'facts': ['Program fixture observations of the original operation, not acceptance model output.'],
                'evidence_refs': refs,
                'completion_assessments': [{'event_id': event['id'], 'complete': complete,
                    'criterion': 'Original requested text reaches its required channel stage.',
                    'reason': 'Fixture assessment checks the actual operation.', 'evidence_refs': refs}]}
            report = self.ok(tools.call('progress.submit', args))['data']
            saved['report'] = report
            current = self.app.event('f', event['id'])
            self.ok(tools.call('service.record_outcome', {'request_id': self.key(), 'outcome': 'action_taken',
                'reason': 'Original Event report persisted; business completion still requires progress.',
                'refs': [{'kind': 'event', 'id': event['id'], 'version': current['version']},
                         {'kind': 'report', 'id': report['id'], 'version': report['version']}]}))
            return {'outcome_id': job['run_id']}
        result = Worker(self.app, service).run_once()
        self.assertEqual(result['outcome'], 'waiting', result)
        self.app.mock_send_outcome = 'sent'
        return saved

    def consume(self, event, report, done=True):
        saved = {}
        def planner(job, tools):
            self.assertEqual(job['kind'], 'progress')
            context = self.ok(tools.call('context.read', {}))
            current = self.app.event('f', event['id'])
            args = {'request_id': self.key(), 'expected_family_version': context['family_version'],
                'report_id': report['report_id'], 'revision': report['revision'], 'event_updates': [{
                    'event_id': event['id'], 'expected_version': current['version'],
                    'changes': {'status': 'done' if done else 'in_progress'},
                    'evidence_refs': report['payload']['evidence_refs']}]}
            saved['result'] = tools.call('planner.commit_progress', args)
            if saved['result']['ok']:
                before = self.business_snapshot()
                replay = tools.call('planner.commit_progress', args)
                self.assertTrue(replay['ok'], replay)
                self.assertEqual(self.business_snapshot(), before)
            return {}
        result = Worker(self.app, planner).run_once()
        return saved['result'], result

    def test_confirmation_delegates_without_answering_and_unrelated_baseline_does_not_wake(self):
        event, pending, message, _, _ = self.accepted()
        confirmed = self.acknowledge(message)
        self.assertEqual(len(confirmed['outcome']['delegation_acknowledgements']), 1)
        current = self.app.store.get('message', message['id'], 'f')
        self.assertEqual(current['intents'][0]['reply_operation_refs'], [])
        self.assertTrue(current['intents'][0]['requires_reply'])
        self.assertEqual(reply_scope(self.app, 'f'), {})
        token, args = self.baseline()
        self.applied(token, 'planner.commit_daily', args)
        with self.app.store.transaction(): self.app.store.bump('f')
        version = self.app.store.family('f')['version']
        for _ in range(20):
            self.assertFalse(Worker(self.app, lambda *_: self.fail('Delegated old message woke')).run_once()['ran'])
        self.assertEqual(self.app.store.family('f')['version'], version)
        job = self.app.store.get('job', 'message:'+message['id'], 'f')
        self.assertEqual(job['reason'], 'DELIVERY_DELEGATED')

    def test_unknown_confirmation_does_not_establish_acknowledgement(self):
        _, _, message, _, _ = self.accepted()
        saved = self.acknowledge(message, stage='unknown')
        self.assertEqual(saved['outcome']['delegation_acknowledgements'], [])
        self.assertEqual(held_intents(self.app, 'f'), set())
        self.assertEqual(reply_scope(self.app, 'f'), {message['id']: ['request']})

    def test_partial_before_decision_does_not_count_as_arrangement_confirmation(self):
        pending, message, _ = self.pending()
        prior = self.acknowledge(message)
        self.assertEqual(prior['outcome']['delegation_acknowledgements'], [])
        token, args = self.admission(pending)
        self.applied(token, 'planner.commit_admission', args)
        self.assertEqual(held_intents(self.app, 'f'), set())
        self.assertEqual(reply_scope(self.app, 'f'), {message['id']: ['request']})

    def test_final_reply_cannot_claim_accepted_delivery_before_confirmation(self):
        _, _, message, _, _ = self.accepted()
        claim = JobQueue(self.app).claim('confirm')
        token = JobQueue(self.app).session(claim)
        self.ok(self.call(token, 'wechat.read_messages', {'mode': 'all'}))
        denied = self.call(token, 'wechat.send_message', {'request_id': self.key(),
            'expected_family_version': self.app.store.family('f')['version'], 'action_kind': 'reply',
            'body': 'Cannot turn an accepted delivery into a plain final reply.',
            'reply_to': [{'message_id': message['id'], 'intent_id': 'request'}]})
        self.assertEqual(denied['error']['code'], 'DELIVERY_RESPONSIBILITY_DELEGATED')
        self.assertEqual(channel_for(self.app).count(), 0)

    def test_new_message_stays_prioritized_and_old_delegated_intent_not_in_capability(self):
        event, _, old = self.setup_delivery()
        self.enqueue_step(event)
        new, _ = self.message('A new ordinary question or pause request.')
        claim = JobQueue(self.app).claim('new-message')
        self.assertEqual(claim['job']['message_id'], new['id'])
        token = JobQueue(self.app).session(claim)
        _, cap = self.app.auth(token, {'service'})
        self.assertEqual(cap['reply_scope'], {new['id']: ['request']})
        self.assertNotIn(old['id'], cap['reply_scope'])
        # Preparation/dispatch policy also rechecks ownership independently of
        # an older, overbroad scope snapshot. It does not trust the cap alone.
        old_scope_cap = {**cap, 'reply_scope': {old['id']: ['request']}}
        with self.assertRaises(BusinessError) as raised:
            check_send(self.app, 'f', Send.model_validate({'request_id': self.key(),
                'expected_family_version': self.app.store.family('f')['version'], 'action_kind': 'reply',
                'body': 'Stale attempt to repeat the original acknowledgement.',
                'reply_to': [{'message_id': old['id'], 'intent_id': 'request', 'resolves_intent': False}]}),
                capability=old_scope_cap)
        self.assertEqual(raised.exception.code, 'DELIVERY_RESPONSIBILITY_DELEGATED')

    def test_other_intent_in_same_message_retains_answering_scope(self):
        pending, message, service = self.pending()
        # Explicit multi-intent source fixture, before acceptance: preserve the
        # first intent and add a distinct ordinary question, then recreate source digest.
        with self.app.store.transaction():
            current = self.app.store.get('message', message['id'], 'f')
            current['intents'].append({'intent_id': 'ordinary', 'text': 'Separate ordinary question',
                                      'requires_reply': True, 'reply_operation_refs': []})
            current = self.app.store.put('message', current['id'], 'f', current, current['version'])
            source = self.app.store.get('candidate_source', pending['candidate_source_id'], 'f')
            from digital_tutor.report_contract import message_source_digest
            source['source_message_digest'] = message_source_digest(current)
            source['source_message_version'] = current['version']
            self.app.store.put('candidate_source', source['id'], 'f', source, source['version'])
            self.app.store.bump('f')
        token, args = self.admission(pending)
        self.applied(token, 'planner.commit_admission', args)
        self.acknowledge(message)
        self.assertEqual(reply_scope(self.app, 'f'), {message['id']: ['ordinary']})
        with self.app.store.transaction(): self.app.store.bump('f')
        claim = JobQueue(self.app).claim('ordinary')
        self.assertEqual(claim['job']['message_id'], message['id'])

    def test_actual_step_progress_atomically_returns_delivery_to_original_intent(self):
        event, _, message = self.setup_delivery()
        saved = self.run_step_report(event)
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])
        result, worker = self.consume(event, saved['report'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(worker['outcome'], 'succeeded')
        current = self.app.store.get('message', message['id'], 'f')['intents'][0]
        self.assertEqual(current['reply_operation_refs'], [saved['operation']['id']])
        self.assertEqual(current['delegated_completion']['event_id'], event['id'])
        self.assertEqual(self.app.event('f', event['id'])['status'], 'done')
        count = channel_for(self.app).count()
        for _ in range(5): self.assertFalse(Worker(self.app, lambda *_: self.fail('No duplicate reply')).run_once()['ran'])
        self.assertEqual(channel_for(self.app).count(), count)

    def test_dispatch_proof_uses_persisted_request_step_and_rejects_mismatched_authority(self):
        event, _, _ = self.setup_delivery()
        saved = self.run_step_report(event)
        operation = saved['operation']
        from digital_tutor.request_responsibility import _dispatched_under_grant
        self.assertNotIn('step_id', operation)
        self.assertEqual(operation['request']['step_id'], operation['send_capability']['current_step_key'])
        self.assertTrue(_dispatched_under_grant(self.app, event, operation))
        altered = deepcopy(operation)
        altered['request']['step_id'] = 'different-step'
        self.assertFalse(_dispatched_under_grant(self.app, event, altered))
        altered = deepcopy(operation)
        altered['send_capability']['commitment_id'] = 'different-commitment'
        self.assertFalse(_dispatched_under_grant(self.app, event, altered))
        altered = deepcopy(operation)
        altered['admitted_at'] = '2026-10-04T21:00:00+08:00'
        self.assertFalse(_dispatched_under_grant(self.app, event, altered))

    def test_unknown_delivery_cannot_complete_or_clear_original_request(self):
        event, _, message = self.setup_delivery()
        saved = self.run_step_report(event, stage='unknown')
        before = self.business_snapshot()
        result, _ = self.consume(event, saved['report'])
        self.assertEqual(result['error']['code'], 'COMPLETION_NOT_MET')
        self.assertEqual(self.app.event('f', event['id'])['status'], 'pending')
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])
        # The rejected progress callback cannot consume its report or amend the source.
        self.assertIsNone(self.app.store.get('report', saved['report']['id'], 'f')['resolution'])

    def test_failed_delivery_cannot_complete_or_clear_original_request(self):
        event, _, message = self.setup_delivery()
        saved = self.run_step_report(event, stage='failed')
        result, _ = self.consume(event, saved['report'])
        self.assertEqual(result['error']['code'], 'COMPLETION_NOT_MET')
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])

    def test_sent_receipt_without_positive_assessment_does_not_complete(self):
        event, _, message = self.setup_delivery()
        saved = self.run_step_report(event, complete=False)
        result, _ = self.consume(event, saved['report'])
        self.assertEqual(result['error']['code'], 'SERVICE_ASSESSMENT_REQUIRED')
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])

    def test_changed_unknown_receipt_recovers_original_step_without_send_permission(self):
        event, _, message = self.setup_delivery()
        saved = self.run_step_report(event, stage='unknown', complete=False)
        result, _ = self.consume(event, saved['report'], done=False)
        self.assertTrue(result['ok'], result)
        count = channel_for(self.app).count()
        self.assertIsNone(JobQueue(self.app).claim('unchanged-unknown'))
        from digital_tutor.outbox import reconcile
        op = reconcile(self.app, 'f', saved['operation']['id'])
        self.assertEqual(op['stage'], 'sent')
        claim = JobQueue(self.app).claim('original-receipt')
        self.assertEqual(claim['job']['id'], 'original-step')
        self.assertTrue(claim['job']['recovery_only'])
        token = JobQueue(self.app).session(claim)
        _, cap = self.app.auth(token, {'service'})
        self.assertTrue(cap['no_contact'])
        self.assertEqual(cap['recovery_operation_ids'], [saved['operation']['id']])
        self.assertEqual(channel_for(self.app).count(), count)
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])

    def test_consumed_report_same_step_waits_for_real_fact_not_family_version(self):
        event, _, _ = self.setup_delivery()
        saved = self.run_step_report(event, send=False, complete=False)
        result, _ = self.consume(event, saved['report'], done=False)
        self.assertTrue(result['ok'], result)
        job = self.app.store.get('job', 'original-step', 'f')
        self.assertEqual(job['status'], 'waiting')
        self.assertEqual(job['reason'], 'STEP_WAITING_NEW_FACT')
        with self.app.store.transaction(): self.app.store.bump('f')
        for _ in range(20): self.assertFalse(Worker(self.app, lambda *_: self.fail('No unchanged retry')).run_once()['ran'])
        # A scoped external control observation changes the dependency fingerprint.
        with self.app.store.transaction():
            self.app.store.put('control', 'historical-release-observation', 'f', {'event_id': event['id'],
                'action_kinds': ['deliver_feedback'], 'status': 'released'})
            self.app.store.bump('f')
        claim = JobQueue(self.app).claim('new-real-fact')
        self.assertEqual(claim['job']['id'], 'original-step')
        self.assertEqual(claim['job']['step_resume_count'], 1)

    def test_source_correction_prevents_host_settlement_and_preserves_responsibility(self):
        event, _, message = self.setup_delivery()
        saved = self.run_step_report(event)
        with self.app.store.transaction():
            current = self.app.store.get('message', message['id'], 'f')
            current['body'] += ' Source correction.'
            self.app.store.put('message', current['id'], 'f', current, current['version'])
            settle_completed(self.app, 'f', [{**event, 'status': 'done'}], saved['report'])
        current = self.app.store.get('message', message['id'], 'f')['intents'][0]
        self.assertEqual(current['reply_operation_refs'], [])
        self.assertFalse(projection(self.app, 'f')[0]['source_current'])
        self.assertIn((message['id'], 'request'), held_intents(self.app, 'f'))

    def test_pause_itself_cannot_settle_and_other_family_cannot_supply_receipts(self):
        event, _, message = self.setup_delivery()
        saved = self.run_step_report(event, send=False, complete=False)
        with self.app.store.transaction():
            self.app.store.put('control', 'historical-current-pause', 'f', {'event_id': event['id'],
                'action_kinds': ['deliver_feedback'], 'status': 'active'})
            settle_completed(self.app, 'f', [{**event, 'status': 'done'}], saved['report'])
        self.assertEqual(self.app.store.get('message', message['id'], 'f')['intents'][0]['reply_operation_refs'], [])
        self.assertEqual(held_intents(self.app, 'g'), set())
        self.assertEqual(projection(self.app, 'g'), [])
        with self.assertRaises(BusinessError):
            with self.app.store.transaction(): settle_completed(self.app, 'g', [{**event, 'status': 'done'}], saved['report'])

    def test_real_sent_then_pause_then_progress_accounts_without_another_delivery(self):
        event, _, original = self.setup_delivery()
        saved = self.run_step_report(event)
        pause, _ = self.message('Please pause future delivery actions.')
        def service(job, tools):
            self.assertEqual(job['message_id'], pause['id'])
            self.ok(tools.call('wechat.read_messages', {'mode': 'all'}))
            self.ok(tools.call('control.apply', {'request_id': self.key(), 'event_id': event['id'],
                'action_kinds': ['deliver_feedback'], 'reason': 'Pause future sends, preserve past receipts.',
                'source_message_id': pause['id'], 'source_version': pause['version']}))
            op = self.ok(tools.call('wechat.send_message', {'request_id': self.key(), 'action_kind': 'reply',
                'body': 'Future deliveries are paused.', 'expected_family_version': self.app.store.family('f')['version'],
                'reply_to': [{'message_id': pause['id'], 'intent_id': 'request'}]}))['data']
            current = self.app.store.get('message', pause['id'], 'f')
            self.ok(tools.call('service.record_outcome', {'request_id': self.key(), 'outcome': 'action_taken',
                'reason': 'Pause saved and actual ordinary response sent.',
                'refs': [{'kind': 'message', 'id': current['id'], 'version': current['version']},
                         {'kind': 'operation', 'id': op['id'], 'version': op['version']}]}))
            return {'outcome_id': job['run_id']}
        self.assertEqual(Worker(self.app, service).run_once()['outcome'], 'succeeded')
        count = channel_for(self.app).count()
        result, _ = self.consume(event, saved['report'])
        self.assertTrue(result['ok'], result)
        intent = self.app.store.get('message', original['id'], 'f')['intents'][0]
        self.assertEqual(intent['reply_operation_refs'], [saved['operation']['id']])
        self.assertEqual(self.app.event('f', event['id'])['status'], 'done')
        for _ in range(5): self.assertFalse(Worker(self.app, lambda *_: self.fail('No new delivery')).run_once()['ran'])
        self.assertEqual(channel_for(self.app).count(), count)

    def test_unknown_ack_delivery_progress_then_original_ack_recovery_closes_both_accounts(self):
        event, _, original, _, _ = self.accepted()
        ack = self.acknowledge(original, stage='unknown')['operation']
        self.app.mode = 'full'
        self.app.clock = Clock('2026-10-04T15:00:15+08:00')
        saved = self.run_step_report(event)
        result, _ = self.consume(event, saved['report'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.app.event('f', event['id'])['status'], 'done')
        intent = self.app.store.get('message', original['id'], 'f')['intents'][0]
        self.assertEqual(intent['reply_operation_refs'], [])
        self.assertEqual(intent['delegated_delivery_verified']['operation_ids'], [saved['operation']['id']])
        count = channel_for(self.app).count()
        reports = len(self.app.store.list('report', 'f'))
        for _ in range(5): self.assertFalse(Worker(self.app, lambda *_: self.fail('Unknown ack unchanged')).run_once()['ran'])
        from digital_tutor.outbox import reconcile
        verified_ack = reconcile(self.app, 'f', ack['id'])
        self.assertEqual(verified_ack['stage'], 'sent')
        def recover(job, tools):
            self.assertEqual(job['id'], 'message:'+original['id'])
            self.assertTrue(job['recovery_only'])
            context = self.ok(tools.call('context.read', {}))
            self.assertTrue(context['run_contract']['recovery_only'])
            self.assertTrue(context['request_responsibilities'][0]['delivery_verified_pending_accounting'])
            message = self.app.store.get('message', original['id'], 'f')
            outcome = self.ok(tools.call('service.record_outcome', {'request_id': self.key(), 'outcome': 'action_taken',
                'reason': 'Original acknowledgement now verified; existing completed delivery accounted internally.',
                'refs': [{'kind': 'message', 'id': message['id'], 'version': message['version']},
                         {'kind': 'operation', 'id': verified_ack['id'], 'version': verified_ack['version']}]}))
            self.assertTrue(outcome['settled_delegated_request_refs'])
            return {'outcome_id': job['run_id']}
        self.assertEqual(Worker(self.app, recover).run_once()['outcome'], 'succeeded')
        intent = self.app.store.get('message', original['id'], 'f')['intents'][0]
        self.assertEqual(intent['reply_operation_refs'], [saved['operation']['id']])
        self.assertEqual(intent['delegated_completion']['confirmation_observations'][0]['stage'], 'sent')
        self.assertEqual(len(self.app.store.list('report', 'f')), reports)
        self.assertEqual(channel_for(self.app).count(), count)
        for _ in range(5): self.assertFalse(Worker(self.app, lambda *_: self.fail('Already settled')).run_once()['ran'])

    def test_step_recovery_limit_is_explicit_and_never_a_model_storm(self):
        event, _, _ = self.setup_delivery()
        saved = self.run_step_report(event, send=False, complete=False)
        result, _ = self.consume(event, saved['report'], done=False)
        self.assertTrue(result['ok'], result)
        queue = JobQueue(self.app)
        for number in range(4):
            with self.app.store.transaction():
                self.app.store.put('control', 'external-release-observation-'+str(number), 'f', {
                    'event_id': event['id'], 'action_kinds': ['deliver_feedback'], 'status': 'released'})
                self.app.store.bump('f')
            claim = queue.claim('bounded-recovery')
            if number < 3:
                self.assertEqual(claim['job']['id'], 'original-step')
                queue.finish(claim, 'waiting', 'No new executable result in this program lease fixture.')
            else:
                self.assertIsNone(claim)
        job = self.app.store.get('job', 'original-step', 'f')
        self.assertEqual(job['reason'], 'STEP_RECOVERY_LIMIT_REACHED')
        self.assertEqual(job['step_resume_count'], 3)
        self.assertNotEqual(self.app.event('f', event['id'])['status'], 'done')
        for _ in range(20): self.assertFalse(Worker(self.app, lambda *_: self.fail('Recovery limit')).run_once()['ran'])


if __name__ == '__main__': unittest.main()
