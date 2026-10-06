"""Independent L contract cases through ToolRegistry; no provider or production data.

Historical records and external facts are explicitly seeded only where named.
The parent runs this module on the isolated server; it is not executed locally.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from digital_tutor.application import Application, Clock
from digital_tutor.scheduling import baseline_committed, step_ready
from digital_tutor.storage import digest, encode
from digital_tutor.tools import ToolRegistry


class IntradayContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.tmp.name) / 'isolated.sqlite',
            Clock('2026-10-04T09:00:00+08:00'), mode='daily', strict_runtime=True,
            runtime_clock=Clock('2026-10-04T09:00:00+08:00'))
        self.app.create_family('f', 'synthetic-f', 'mock:f')
        self.app.create_family('g', 'synthetic-g', 'mock:g')
        self.sequence = 0

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def key(self, prefix='request'):
        self.sequence += 1
        return f'{prefix}-{self.sequence}'

    def call(self, token, name, args):
        return ToolRegistry(self.app, token).call(name, args)

    def applied(self, token, name, args):
        result = self.call(token, name, args)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['outcome'], 'applied', result)
        return result['result']['data']

    def rejected(self, token, name, args, code):
        before = self.business_snapshot()
        result = self.call(token, name, args)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], code, result)
        self.assertEqual(self.business_snapshot(), before)
        return result

    def business_snapshot(self):
        # Sessions are capability bookkeeping, not business commits. Include the
        # immutable history and receipts so half-created objects cannot hide.
        db = self.app.store.connection
        return {table: [tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in ('families', 'records', 'history', 'goals', 'plans', 'receipts', 'report_links')}

    def message(self, body='Please arrange a text response before 16:00.', family='f',
                service_kind='reply', service_authorization=None):
        channel = self.app.open_session(family, 'channel')
        msg = self.applied(channel, 'wechat.receive_message', {
            'external_id': self.key('inbound'), 'body': body, 'occurred_at': self.app.clock.iso()})
        service = self.app.open_session(family, 'service')
        self.applied(service, 'message.interpret', {
            'request_id': self.key(), 'message_id': msg['id'], 'expected_version': msg['version'],
            'intents': [{'intent_id': 'request', 'text': body, 'requires_reply': True,
                        'service_kind': service_kind, 'service_authorization': service_authorization}]})
        return self.app.store.get('message', msg['id'], family), service

    def pending(self, condition=None, end='2026-10-04T16:00:00+08:00'):
        body = 'Please arrange a text response before 16:00.'
        if condition:
            body += ' ' + condition
        msg, service = self.message(body, service_kind='future_delivery', service_authorization=body)
        pending = self.applied(service, 'pending.propose_service', {
            'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
            'source_message_id': msg['id'], 'source_version': msg['version'],
            'source_intent_id': 'request', 'source_excerpt': body, 'request': 'A synthetic text response',
            'requested_time': {'meaning': 'before', 'source_text': 'before 16:00', 'end': end},
            'completion_condition': condition})
        return pending, msg, service

    def admission(self, pending, execute_at='2026-10-04T15:00:00+08:00', accepted=True):
        token = self.app.open_session('f', 'planner_daily', planning_reason='admission', pending_id=pending['id'])
        _, binding = self.app.auth(token, {'planner_daily'})
        candidates = [c for c in self.app.store.list('candidate', 'f')
                      if c['id'] in binding['candidate_versions']]
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        ref = 'new-request'
        new = {k: deepcopy(candidate[k]) for k in ('goal_key', 'goal_text', 'completion', 'delivery_parts', 'due_at')}
        new.update(client_ref=ref, candidate_id=candidate['id'],
            completion_condition=candidate.get('completion_condition'),
            next_action={'action_kind': 'deliver_feedback', 'target_refs': ['requested-text'],
                         'purpose': 'Deliver the accepted text.', 'execute_at': execute_at})
        disposition = 'created' if accepted else 'retained'
        args = {'request_id': self.key('admission'), 'business_date': '2026-10-04',
            'expected_family_version': binding['family_version'], 'pending_id': pending['id'],
            'expected_plan_revision': binding['plan_revision'],
            'new_events': [new] if accepted else [], 'plan_refs': [ref] if accepted else [],
            'candidate_decisions': [{'candidate_id': candidate['id'], 'expected_version': candidate['version'],
                                    'disposition': disposition, 'event_ref': ref if accepted else None,
                                    'reason': 'Decision on the bound original request.'}],
            'pending_decisions': [{'input_id': pending['id'], 'disposition': disposition,
                                   'event_ref': ref if accepted else None, 'reason': 'Original request retained.'}]}
        return token, args

    def accepted(self, condition=None):
        pending, msg, service = self.pending(condition)
        token, args = self.admission(pending)
        data = self.applied(token, 'planner.commit_admission', args)
        event = self.app.event('f', data['event_ids']['new-request'])
        return event, pending, msg, service, data

    def baseline(self):
        token = self.app.open_session('f', 'planner_daily')
        _, binding = self.app.auth(token, {'planner_daily'})
        candidates = [c for c in self.app.store.list('candidate', 'f') if c['id'] in binding['candidate_versions']]
        self.assertTrue(all(c['existing_event_id'] for c in candidates))
        refs = list(dict.fromkeys(c['existing_event_id'] for c in candidates))
        args = {'request_id': self.key('baseline'), 'business_date': '2026-10-04',
            'expected_family_version': binding['family_version'], 'new_events': [], 'plan_refs': refs,
            'candidate_decisions': [{'candidate_id': c['id'], 'expected_version': c['version'],
                'disposition': 'linked', 'event_ref': c['existing_event_id'], 'reason': 'Retain the canonical obligation.'}
                for c in candidates], 'pending_decisions': []}
        return token, args

    def test_admission_role_and_trigger_are_host_bound(self):
        pending, _, service = self.pending()
        admission, args = self.admission(pending)
        self.rejected(service, 'planner.commit_admission', args, 'ROLE_FORBIDDEN')
        baseline = self.app.open_session('f', 'planner_daily')
        self.rejected(baseline, 'planner.commit_admission', args, 'PLANNING_SCOPE')
        names = {x['name'] for x in ToolRegistry(self.app, admission).describe()}
        self.assertIn('planner.commit_admission', names)
        self.assertNotIn('planner.commit_daily', names)

    def test_admission_cannot_replace_bound_pending(self):
        first, _, _ = self.pending()
        second, _, _ = self.pending()
        token, args = self.admission(first)
        args['pending_id'] = second['id']
        self.rejected(token, 'planner.commit_admission', args, 'PLANNING_SCOPE')

    def test_passive_mode_cannot_accept_future_contact(self):
        pending, _, _ = self.pending()
        token, args = self.admission(pending)
        self.app.mode = 'passive'
        self.rejected(token, 'planner.commit_admission', args, 'PASSIVE_MODE')

    def test_stale_plan_revision_rolls_back_every_business_table(self):
        pending, _, _ = self.pending()
        token, args = self.admission(pending)
        args['expected_plan_revision'] = 8
        self.rejected(token, 'planner.commit_admission', args, 'STALE_PLAN')

    def test_missing_selected_step_rolls_back_created_event(self):
        pending, _, _ = self.pending()
        token, args = self.admission(pending)
        args['new_events'][0].pop('next_action')
        self.rejected(token, 'planner.commit_admission', args, 'PLAN_STEP_REQUIRED')

    def test_explicit_completion_condition_cannot_be_silently_cleared(self):
        pending, _, _ = self.pending('Include the requested comparison.')
        token, args = self.admission(pending)
        args['new_events'][0]['completion_condition'] = None
        self.rejected(token, 'planner.commit_admission', args, 'COMPLETION_CONDITION_MISMATCH')

    def test_schedule_after_source_deadline_is_not_accepted(self):
        pending, _, _ = self.pending()
        token, args = self.admission(pending, '2026-10-04T17:00:00+08:00')
        self.rejected(token, 'planner.commit_admission', args, 'DEADLINE_PASSED')

    def test_admission_replay_and_changed_request_are_distinct(self):
        pending, _, _ = self.pending()
        token, args = self.admission(pending)
        original = self.applied(token, 'planner.commit_admission', args)
        before = self.business_snapshot()
        duplicate = self.call(token, 'planner.commit_admission', deepcopy(args))
        self.assertTrue(duplicate['ok'], duplicate)
        self.assertEqual(duplicate['result']['outcome'], 'duplicate')
        self.assertEqual(duplicate['result']['data'], original)
        self.assertEqual(self.business_snapshot(), before)
        changed = deepcopy(args)
        changed['new_events'][0]['next_action']['purpose'] = 'Different payload with the same request identity.'
        self.rejected(token, 'planner.commit_admission', changed, 'IDEMPOTENCY_CONFLICT')

    def test_admission_before_baseline_keeps_one_container_and_one_baseline(self):
        event, _, _, _, original = self.accepted()
        self.assertFalse(baseline_committed(self.app, 'f', '2026-10-04'))
        token, args = self.baseline()
        baseline = self.applied(token, 'planner.commit_daily', args)
        self.assertTrue(baseline_committed(self.app, 'f', '2026-10-04'))
        plans = self.app.store.list('plan', 'f')
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]['id'], original['plan']['id'])
        self.assertEqual(baseline['plan']['revision'], original['plan']['revision'] + 1)
        self.assertIn(event['id'], [item['event_id'] for item in plans[0]['items']])
        self.assertEqual(self.app.event('f', event['id'])['accepted_commitment'], event['accepted_commitment'])
        token2, args2 = self.baseline()
        self.rejected(token2, 'planner.commit_daily', args2, 'DAILY_ALREADY_COMMITTED')

    def test_today_membership_does_not_override_accepted_deadline(self):
        event, _, _, _, _ = self.accepted()
        token, args = self.baseline()
        self.applied(token, 'planner.commit_daily', args)
        self.app.mode = 'full'
        self.app.clock = Clock('2026-10-04T15:30:00+08:00')
        self.assertTrue(step_ready(self.app, 'f', self.app.event('f', event['id'])))
        self.app.clock = Clock('2026-10-04T17:00:00+08:00')
        self.assertFalse(step_ready(self.app, 'f', self.app.event('f', event['id'])))

    def test_whitespace_completion_conditions_persist_as_unspecified(self):
        pending, _, _ = self.pending(' \t\n ')
        self.assertIsNone(pending['completion_condition'])
        token, args = self.admission(pending)
        args['new_events'][0]['completion_condition'] = ' \t\n '
        args['new_events'][0]['next_action']['completion_condition'] = ' \t '
        result = self.applied(token, 'planner.commit_admission', args)
        event = self.app.event('f', result['event_ids']['new-request'])
        self.assertIsNone(event['completion_condition'])
        self.assertIsNone(event['next_action']['completion_condition'])
        self.assertEqual(event['completion']['required_part_ids'], ['requested-text'])
        self.assertEqual(event['completion_contract_version'], 1)

    def makeup_admission(self):
        # Reuse only the raw synthetic source helper, never another test class.
        from test_stage4_candidates import TrustedCandidateTests
        self.admin = self.app.open_session('f', 'admin')
        TrustedCandidateTests.learning_source(self, enabled=False)
        msg, service = self.message('请帮我安排今天下午四点前补完第1天。')
        pending = self.applied(service, 'pending.propose_makeup', {
            'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
            'source_message_id': msg['id'], 'source_version': msg['version'],
            'source_excerpt': msg['body'], 'batch_id': 'synthetic-batch', 'day': 1})
        token, args = self.admission(pending)
        args['new_events'][0]['next_action'] = {
            'action_kind': 'remind_makeup', 'target_refs': ['synthetic-lesson-1'],
            'purpose': 'Remind the original accepted lesson.', 'execute_at': '2026-10-04T15:00:00+08:00'}
        return token, args

    def historical_reminders(self, days):
        with self.app.store.transaction():
            for index, day in enumerate(days):
                self.app.store.put('operation', 'historical-reminder-'+str(index), 'f', {
                    'event_id': 'historical-goal', 'action_kind': 'remind_makeup', 'stage': 'sent',
                    'business_date': day, 'occurred_at': day+'T15:00:00+08:00',
                    'lesson_refs': ['synthetic-lesson-1'], 'step_id': 'historical-step-'+str(index)})

    def test_prior_days_reminders_exhaust_week_without_exhausting_today(self):
        self.historical_reminders(['2026-10-01', '2026-10-02'])
        token, args = self.makeup_admission()
        self.rejected(token, 'planner.commit_admission', args, 'CONTACT_LIMIT')
        self.assertEqual(self.app.store.list('event', 'f'), [])

    def test_previous_week_reminders_do_not_consume_this_weeks_capacity(self):
        self.historical_reminders(['2026-09-25', '2026-09-26'])
        token, args = self.makeup_admission()
        result = self.applied(token, 'planner.commit_admission', args)
        event = self.app.event('f', result['event_ids']['new-request'])
        self.assertEqual(event['accepted_commitment']['valid_until'], '2026-10-04T16:00:00+08:00')
        self.assertEqual(event['completion']['lesson_refs'], ['synthetic-lesson-1'])
    def reuse_pending_and_proposal(self, event, pending=None):
        if pending is None:
            body = 'Please arrange the same text response before 16:00.'
            msg, service = self.message(body, service_kind='future_delivery', service_authorization=body)
            pending = self.applied(service, 'pending.propose_service', {
                'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
                'source_message_id': msg['id'], 'source_version': msg['version'],
                'source_intent_id': 'request', 'source_excerpt': body,
                'request': 'Repeat the original accepted text responsibility',
                'requested_time': {'meaning': 'before', 'source_text': 'before 16:00',
                                   'end': '2026-10-04T16:00:00+08:00'},
                'existing_event_id': event['id']})
        token, args = self.admission(pending)
        args['new_events'] = []
        args['plan_refs'] = [event['id']]
        for decision in args['candidate_decisions'] + args['pending_decisions']:
            decision.update(disposition='linked', event_ref=event['id'])
        return pending, token, args

    def apply_reuse_control(self, event_id, action, release=None):
        msg, service = self.message('Release the previous pause.' if release else 'Pause this service action.')
        return self.applied(service, 'control.apply', {
            'request_id': self.key(), 'event_id': event_id, 'action_kinds': [action],
            'reason': 'External family pause evidence', 'source_message_id': msg['id'],
            'source_version': msg['version'], 'release_control_id': release})

    def assert_paused_reuse_is_atomic_then_released(self, family_scope):
        from digital_tutor.send_policy import ContactPolicy
        from digital_tutor.contact_capacity import committed_contacts
        self.app.contact_policy = ContactPolicy(proactive_per_day=1)
        event, _, _, _, _ = self.accepted()
        grant = deepcopy(event['accepted_commitment'])
        old_plan = deepcopy(self.app.store.list('plan', 'f')[0])
        scope = None if family_scope else event['id']
        control = self.apply_reuse_control(scope, 'deliver_feedback')
        pending, token, args = self.reuse_pending_and_proposal(event)
        self.assertEqual(committed_contacts(self.app, 'f', '2026-10-04'), 1)
        self.rejected(token, 'planner.commit_admission', args, 'CONTROL_ACTIVE')
        self.assertEqual(self.app.event('f', event['id'])['accepted_commitment'], grant)
        self.assertEqual(self.app.store.list('plan', 'f')[0], old_plan)
        still_pending = self.app.store.get('pending', pending['id'], 'f')
        self.assertEqual(still_pending['disposition'], 'pending')
        self.assertTrue(still_pending['await_decision'])
        self.assertIsNone(still_pending['event_id'])
        self.assertEqual(self.app.store.list('operation', 'f'), [])
        released = self.apply_reuse_control(scope, 'deliver_feedback', control['id'])
        self.assertEqual(released['status'], 'released')
        _, fresh, retry = self.reuse_pending_and_proposal(event, still_pending)
        result = self.applied(fresh, 'planner.commit_admission', retry)
        self.assertEqual(result['accepted_commitment_ids'], [grant['id']])
        current = self.app.event('f', event['id'])
        self.assertEqual(current['accepted_commitment'], grant)
        self.assertEqual(len(self.app.store.list('event', 'f')), 1)
        self.assertEqual(committed_contacts(self.app, 'f', '2026-10-04'), 1)
        plan = self.app.store.list('plan', 'f')[0]
        self.assertEqual(plan['revision'], old_plan['revision'] + 1)
        self.assertEqual([x['event_id'] for x in plan['items']], [event['id']])
        self.assertEqual(self.app.store.get('pending', pending['id'], 'f')['event_id'], event['id'])
        self.assertEqual(self.app.store.list('operation', 'f'), [])

    def test_family_pause_rejects_reused_grant_until_real_release(self):
        self.assert_paused_reuse_is_atomic_then_released(True)

    def test_event_pause_rejects_reused_grant_until_real_release(self):
        self.assert_paused_reuse_is_atomic_then_released(False)

    def test_unrelated_action_pause_does_not_block_reused_delivery_grant(self):
        from digital_tutor.send_policy import ContactPolicy
        from digital_tutor.contact_capacity import committed_contacts
        self.app.contact_policy = ContactPolicy(proactive_per_day=1)
        event, _, _, _, _ = self.accepted()
        control = self.apply_reuse_control(None, 'remind_makeup')
        pending, token, args = self.reuse_pending_and_proposal(event)
        result = self.applied(token, 'planner.commit_admission', args)
        self.assertEqual(result['accepted_commitment_ids'], [event['accepted_commitment']['id']])
        self.assertEqual(self.app.event('f', event['id'])['accepted_commitment'], event['accepted_commitment'])
        self.assertEqual(self.app.store.get('control', control['id'], 'f')['status'], 'active')
        self.assertEqual(self.app.store.get('pending', pending['id'], 'f')['event_id'], event['id'])
        self.assertEqual(committed_contacts(self.app, 'f', '2026-10-04'), 1)
        self.assertEqual(len(self.app.store.list('event', 'f')), 1)
        self.assertEqual(self.app.store.list('operation', 'f'), [])


    def test_retained_decision_does_not_create_an_event_or_contact_grant(self):
        pending, _, _ = self.pending()
        token, args = self.admission(pending, accepted=False)
        result = self.applied(token, 'planner.commit_admission', args)
        self.assertEqual(result['accepted_commitment_ids'], [])
        self.assertEqual(self.app.store.list('event', 'f'), [])
        saved = self.app.store.get('pending', pending['id'], 'f')
        self.assertEqual(saved['disposition'], 'pending')
        self.assertFalse(saved['await_decision'])

    def test_future_baseline_actions_reserve_capacity_before_new_acceptance(self):
        # Previously committed synthetic baseline, before the new request exists.
        with self.app.store.transaction() as db:
            ids=[]
            for index in range(2):
                eid=f'baseline-obligation-{index}'
                self.app.store.put('event',eid,'f',{
                    'uid':'synthetic-f','goal_key':eid,'goal_text':'Existing accepted text obligation.',
                    'status':'pending','waiting':None,'due_at':'2026-10-04T16:00:00+08:00',
                    'completion':{'kind':'delivery','lesson_refs':[],
                                  'required_part_ids':['text'],'required_stage':'sent'},
                    'remaining_work':['text'],'requirement_revision':1,
                    'next_action':{'action_kind':'deliver_feedback','target_refs':['text'],
                        'purpose':'Existing scheduled delivery.','execute_at':f'2026-10-04T{13+index}:00:00+08:00'}})
                ids.append(eid)
            self.app.store.put('plan','original-baseline','f',{'business_date':'2026-10-04',
                'baseline_committed':True,'revision':1,
                'items':[{'event_id':eid,'rank':i+1,'authority':'baseline'} for i,eid in enumerate(ids)]})
            db.execute('INSERT INTO plans VALUES(?,?,?)',('f','2026-10-04','original-baseline'))
        pending,_,_=self.pending()
        token,args=self.admission(pending)
        self.rejected(token,'planner.commit_admission',args,'CONTACT_LIMIT')

    def test_two_charged_operations_on_one_event_are_two_capacity_units(self):
        with self.app.store.transaction():
            for index in range(2):
                self.app.store.put('operation',f'external-charged-{index}','f',{
                    'event_id':'one-prior-event','stage':'sent','action_kind':'deliver_feedback',
                    'business_date':'2026-10-04','occurred_at':'2026-10-04T08:30:00+08:00',
                    'recipient':'mock:f','step_id':f'prior-step-{index}'})
        pending,_,_=self.pending()
        token,args=self.admission(pending)
        self.rejected(token,'planner.commit_admission',args,'CONTACT_LIMIT')

    def report(self, event, refs, complete=None, criterion='Actual source-supported result.', service=None):
        service = service or self.app.open_session('f', 'service')
        args = {'request_id': self.key('report-request'), 'report_id': self.key('report'), 'revision': 1,
            'event_ids': [event['id']], 'facts': ['An actual source observation was obtained.'],
            'evidence_refs': deepcopy(refs)}
        if complete is not None:
            args['completion_assessments'] = [{'event_id': event['id'], 'complete': complete,
                'criterion': criterion, 'reason': 'Judgment is limited to the cited source observation.',
                'evidence_refs': deepcopy(refs)}]
        return service, args

    def progress(self, event, report, refs, changes):
        token = self.app.open_session('f', 'planner_progress', report_id=report['report_id'], revision=report['revision'])
        args = {'request_id': self.key('progress'), 'report_id': report['report_id'], 'revision': report['revision'],
            'expected_family_version': self.app.store.family('f')['version'],
            'event_updates': [{'event_id': event['id'], 'expected_version': self.app.event('f', event['id'])['version'],
                'changes': changes, 'evidence_refs': deepcopy(refs)}]}
        return token, args

    def test_unconsumed_report_blocks_admission_before_mutation(self):
        event, _, msg, _, _ = self.accepted()
        ref = [{'kind': 'message', 'id': msg['id'], 'version': self.app.store.get('message', msg['id'], 'f')['version']}]
        service, report_args = self.report(event, ref)
        self.applied(service, 'progress.submit', report_args)
        pending, _, _ = self.pending()
        token, args = self.admission(pending)
        self.rejected(token, 'planner.commit_admission', args, 'REPORTS_PENDING')

    def test_omitted_report_remaining_is_unknown_not_empty(self):
        event, _, msg, _, _ = self.accepted()
        refs = [{'kind': 'message', 'id': msg['id'], 'version': msg['version']}]
        service, args = self.report(event, refs)
        saved = self.applied(service, 'progress.submit', args)
        self.assertIsNone(saved['payload']['remaining_work'])
        self.assertEqual(self.app.event('f', event['id'])['remaining_work'], ['requested-text'])

    def test_condition_mismatch_and_assessment_outside_report_are_rejected(self):
        event, _, msg, _, _ = self.accepted('Include the requested comparison.')
        refs = [{'kind': 'message', 'id': msg['id'], 'version': msg['version']}]
        service, args = self.report(event, refs, True, criterion='A weaker condition.')
        self.rejected(service, 'progress.submit', args, 'COMPLETION_CONDITION_MISMATCH')
        args['completion_assessments'][0]['criterion'] = event['completion_condition']
        args['completion_assessments'][0]['event_id'] = 'unrelated-event'
        self.rejected(service, 'progress.submit', args, 'PROGRESS_SCOPE_VIOLATION')

    def test_assessment_true_without_delivery_receipt_does_not_complete(self):
        event, _, msg, _, _ = self.accepted()
        self.assertIsNone(event['completion_condition'])
        self.assertEqual(event['completion_contract_version'], 1)
        refs = [{'kind': 'message', 'id': msg['id'], 'version': msg['version']}]
        service, args = self.report(event, refs, True)
        report = self.applied(service, 'progress.submit', args)
        planner, proposal = self.progress(event, report, refs, {'status': 'done'})
        self.rejected(planner, 'planner.commit_progress', proposal, 'COMPLETION_NOT_MET')
        self.assertEqual(self.app.event('f', event['id'])['status'], 'pending')

    def test_new_contract_requires_assessment_even_with_actual_receipt_fact(self):
        event, _, _, _, _ = self.accepted()
        # A trusted channel-fact fixture, not an assertion that a model delivered.
        with self.app.store.transaction():
            op = self.app.store.put('operation', 'external-confirmed-operation', 'f', {
                'event_id': event['id'], 'requirement_revision': event['requirement_revision'],
                'recipient': 'mock:f', 'part_id': 'requested-text', 'stage': 'sent'})
        refs = [{'kind': 'operation', 'id': op['id'], 'version': op['version']}]
        service, args = self.report(event, refs)
        report = self.applied(service, 'progress.submit', args)
        planner, proposal = self.progress(event, report, refs, {'status': 'done'})
        self.rejected(planner, 'planner.commit_progress', proposal, 'SERVICE_ASSESSMENT_REQUIRED')

    def test_null_condition_assessment_and_hard_delivery_evidence_can_complete(self):
        event, _, _, _, _ = self.accepted()
        with self.app.store.transaction():
            op = self.app.store.put('operation', 'external-confirmed-operation', 'f', {
                'event_id': event['id'], 'requirement_revision': event['requirement_revision'],
                'recipient': 'mock:f', 'part_id': 'requested-text', 'stage': 'sent'})
        refs = [{'kind': 'operation', 'id': op['id'], 'version': op['version']}]
        service, args = self.report(event, refs, True)
        report = self.applied(service, 'progress.submit', args)
        planner, proposal = self.progress(event, report, refs, {'status': 'done'})
        self.applied(planner, 'planner.commit_progress', proposal)
        actual = self.app.event('f', event['id'])
        self.assertEqual((actual['status'], actual['remaining_work'], actual['next_action']), ('done', [], None))
        self.assertFalse(actual['update_pending'])

    def test_negative_service_assessment_cannot_be_overridden_by_planner(self):
        event, _, _, _, _ = self.accepted()
        with self.app.store.transaction():
            op = self.app.store.put('operation', 'external-confirmed-operation', 'f', {
                'event_id': event['id'], 'requirement_revision': event['requirement_revision'],
                'recipient': 'mock:f', 'part_id': 'requested-text', 'stage': 'sent'})
        refs = [{'kind': 'operation', 'id': op['id'], 'version': op['version']}]
        service, args = self.report(event, refs, False)
        report = self.applied(service, 'progress.submit', args)
        planner, proposal = self.progress(event, report, refs, {'status': 'done'})
        self.rejected(planner, 'planner.commit_progress', proposal, 'SERVICE_ASSESSMENT_REQUIRED')

    def goal_fixture(self):
        # Explicit existing-obligation fixture: candidate creation is tested
        # separately. The assessment marker is the new completion contract.
        with self.app.store.transaction():
            return self.app.store.put('event', 'original-goal', 'f', {
                'uid': 'synthetic-f', 'goal_key': 'synthetic-goal', 'goal_text': 'Complete the original lesson.',
                'completion': {'kind': 'goal', 'lesson_refs': ['original-lesson'],
                               'required_part_ids': [], 'required_stage': 'sent'},
                'completion_condition': None, 'completion_contract_version': 1,
                'status': 'pending', 'waiting': None, 'due_at': None, 'next_action': None,
                'requirement_revision': 1, 'remaining_work': ['original-lesson'],
                'proposed_change': None, 'deadline_history': [], 'completion_history': [], 'evidence_refs': []})

    def test_goal_assessment_with_reminder_receipt_is_not_learning_completion(self):
        event = self.goal_fixture()
        with self.app.store.transaction():
            op = self.app.store.put('operation', 'external-reminder', 'f', {
                'event_id': event['id'], 'requirement_revision': 1, 'recipient': 'mock:f',
                'action_kind': 'remind_makeup', 'stage': 'sent'})
        refs = [{'kind': 'operation', 'id': op['id'], 'version': op['version']}]
        service, args = self.report(event, refs, True)
        report = self.applied(service, 'progress.submit', args)
        planner, proposal = self.progress(event, report, refs, {'status': 'done'})
        self.rejected(planner, 'planner.commit_progress', proposal, 'COMPLETION_NOT_MET')
        self.assertEqual(self.app.event('f', event['id'])['remaining_work'], ['original-lesson'])

    def test_goal_true_assessment_with_trusted_original_learning_evidence_can_complete(self):
        event = self.goal_fixture()
        admin = self.app.open_session('f', 'admin')
        evidence = self.app.add_evidence(admin, 'external-learning-completion', {
            'kind': 'lesson_completed', 'lesson_ref': 'original-lesson', 'valid': True})
        refs = [{'kind': 'evidence', 'id': evidence['id'], 'version': evidence['version']}]
        service, args = self.report(event, refs, True)
        report = self.applied(service, 'progress.submit', args)
        planner, proposal = self.progress(event, report, refs, {'status': 'done'})
        self.applied(planner, 'planner.commit_progress', proposal)
        self.assertEqual(self.app.event('f', event['id'])['status'], 'done')
        self.assertEqual(self.app.event('f', event['id'])['remaining_work'], [])

    def test_adapter_fact_with_same_lesson_label_but_other_course_version_is_not_applicable(self):
        from digital_tutor.course_binding import confirm_course_binding
        from digital_tutor.learning_evidence import sync_learning_evidence
        from digital_tutor.candidates import makeup_identity
        event=self.goal_fixture()
        with self.app.store.transaction() as db:
            event['source_identity']=makeup_identity('f','synthetic-course','v2',['original-lesson'])
            self.app.store.put('event',event['id'],'f',event,event['version'])
            meta={'synthetic':True,'batch_id':'adapter-v1','imported_at':'2026-10-04T08:00:00+08:00',
                'coverage_through':'2026-10-04T08:00:00+08:00','source_timezone':'Asia/Shanghai',
                'source_contract':{'kind':'versioned_learning_snapshot','source_id':'synthetic-source',
                    'revision':1,'supersedes_batch_id':None,'observed_at':'2026-10-04T08:00:00+08:00'}}
            db.execute('INSERT INTO imports VALUES(?,?)',('adapter-v1',encode(meta)))
            db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('adapter-v1','synthetic-f',1,'attended','completed','unknown','unknown','unknown',
                 None,None,'synthetic-source-row'))
        admin=self.app.open_session('f','admin')
        confirm_course_binding(self.app,admin,{'request_id':self.key(), 'batch_id':'adapter-v1',
            'expected_family_version':self.app.store.family('f')['version'],
            'source_note':'Synthetic v1 source binding, distinct from the original v2 goal.',
            'course_id':'synthetic-course','course_version':'v1',
            'lesson_map':[{'day':1,'lesson_ref':'original-lesson'}],
            'coverage_through':'2026-10-04T08:00:00+08:00','source_timezone':'Asia/Shanghai'})
        result=sync_learning_evidence(self.app,admin,'adapter-v1',1)
        evidence=self.app.store.get('evidence',result['evidence_ids'][0],'f')
        self.assertFalse(any(j.get('event_id')==event['id'] and j['kind']=='review'
                             for j in self.app.store.list('job','f')))
        service=self.app.open_session('f','service')
        context=self.call(service,'context.read',{'event_id':event['id']})
        self.assertTrue(context['ok'],context)
        self.assertNotIn(evidence['id'],[e['id'] for e in context['result']['evidence']])
        refs=[{'kind':'evidence','id':evidence['id'],'version':evidence['version']}]
        service,args=self.report(event,refs,True)
        report=self.applied(service,'progress.submit',args)
        planner,proposal=self.progress(event,report,refs,{'status':'done'})
        self.rejected(planner,'planner.commit_progress',proposal,'COMPLETION_NOT_MET')

    def test_message_receipt_metadata_can_advance_but_source_correction_cannot(self):
        event, _, msg, _, _ = self.accepted()
        refs = [{'kind': 'message', 'id': msg['id'], 'version': msg['version']}]
        service, args = self.report(event, refs)
        report = self.applied(service, 'progress.submit', args)
        # This fixture adds only a derived receipt reference, leaving source facts intact.
        with self.app.store.transaction():
            changed = self.app.store.get('message', msg['id'], 'f')
            changed['intents'][0]['reply_operation_refs'] = ['external-receipt-ref']
            self.app.store.put('message', changed['id'], 'f', changed, changed['version'])
            self.app.store.bump('f')
        planner, proposal = self.progress(event, report, refs, {})
        self.applied(planner, 'planner.commit_progress', proposal)
        current = self.app.store.get('message', msg['id'], 'f')
        refs2 = [{'kind': 'message', 'id': current['id'], 'version': current['version']}]
        service2, args2 = self.report(event, refs2)
        report2 = self.applied(service2, 'progress.submit', args2)
        with self.app.store.transaction():
            changed = self.app.store.get('message', msg['id'], 'f')
            changed['body'] += ' A genuine source correction.'
            self.app.store.put('message', changed['id'], 'f', changed, changed['version'])
            self.app.store.bump('f')
        planner2, proposal2 = self.progress(event, report2, refs2, {})
        self.rejected(planner2, 'planner.commit_progress', proposal2, 'EVIDENCE_SUPERSEDED')

    def test_progress_cannot_clear_selected_action_without_waiting(self):
        event, _, msg, _, _ = self.accepted()
        refs = [{'kind': 'message', 'id': msg['id'], 'version': msg['version']}]
        service, args = self.report(event, refs)
        report = self.applied(service, 'progress.submit', args)
        planner, proposal = self.progress(event, report, refs, {'next_action': None})
        self.rejected(planner, 'planner.commit_progress', proposal, 'PLAN_STEP_REQUIRED')

    def test_historical_report_receipt_replays_original_default_list(self):
        event, _, msg, service, _ = self.accepted()
        request_id, report_id = self.key('old-request'), self.key('old-report')
        old_payload = {'request_id': request_id, 'report_id': report_id, 'revision': 1,
            'event_ids': [event['id']], 'facts': ['Historical source fact.'], 'suggested_update': '',
            'remaining_work': [], 'evidence_refs': [{'kind': 'message', 'id': msg['id'], 'version': msg['version']}],
            'supersedes_revision': None}
        key = self.app.report_key('f', report_id, 1)
        with self.app.store.transaction() as db:
            saved = self.app.store.put('report', key, 'f', {
                'payload': {k: v for k, v in old_payload.items() if k != 'request_id'},
                'event_ids': [event['id']], 'report_id': report_id, 'revision': 1, 'resolution': None})
            old_result = {'request_id': request_id, 'outcome': 'applied', 'data': saved,
                'record_versions': {'family_input_version': self.app.store.family('f')['version']}, 'error': None}
            db.execute('INSERT INTO receipts VALUES(?,?,?,?)', ('f', request_id,
                digest({'command': 'submit_report', 'payload': old_payload}), encode(old_result)))
        retry = deepcopy(old_payload)
        retry.pop('remaining_work')
        before = self.business_snapshot()
        result = self.call(service, 'progress.submit', retry)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result'], {**old_result, 'outcome': 'duplicate'})
        self.assertEqual(self.business_snapshot(), before)


if __name__ == '__main__':
    unittest.main()
