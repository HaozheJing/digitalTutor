"""Independent candidate scope / corrected-source priority contracts, no model API.

Only helper methods are reused; no inherited test methods duplicate test counts.
The parent executes these tests on the isolated server, not locally.
"""
import json
import re
import unittest

import test_stage4_candidates as candidate_fixtures
import test_learning_evidence_adapter as learning_fixtures
from digital_tutor.application import Clock
from digital_tutor.context import build_context
from digital_tutor.jobs import JobQueue
from digital_tutor.scheduler import Worker
from digital_tutor.tools import ToolRegistry


class CandidateDecisionRecoveryTests(unittest.TestCase):
    setUp = candidate_fixtures.TrustedCandidateTests.setUp
    tearDown = candidate_fixtures.TrustedCandidateTests.tearDown
    key = candidate_fixtures.TrustedCandidateTests.key
    delivery_source = candidate_fixtures.TrustedCandidateTests.delivery_source
    command = candidate_fixtures.TrustedCandidateTests.command

    def test_blocked_gaps_are_not_decisions_and_same_binding_can_correct_extra(self):
        self.delivery_source()
        token, args = self.command()
        self.app.commit_daily(token, args)
        # Explicit prior terminal-state fixture: this test targets candidate
        # applicability, not proof of delivery (covered by separate contracts).
        with self.app.store.transaction():
            event = self.app.store.list('event', 'f')[0]
            event.update(status='done', next_action=None, remaining_work=[])
            self.app.store.put('event', event['id'], 'f', event, event['version'])
        self.app.clock = Clock('2026-10-05T09:00:00+08:00')
        token = self.app.open_session('f', 'planner_daily')
        context = build_context(self.app, token)
        self.assertEqual(context['candidates'], [])
        self.assertEqual(context['candidate_versions'], {})
        self.assertEqual(context['candidate_decision_scope'], {
            'required_candidate_refs': [], 'source_gaps_require_decisions': False})
        self.assertEqual(len(context['candidate_source_gaps']), 1)
        gap = context['candidate_source_gaps'][0]
        self.assertEqual(gap['gaps'], ['original_goal_already_terminal'])
        args = {'request_id': self.key(), 'business_date': '2026-10-05',
                'expected_family_version': context['family_version'], 'new_events': [],
                'event_updates': [], 'plan_refs': [], 'pending_decisions': [],
                'candidate_decisions': [{'candidate_id': gap['id'], 'expected_version': gap['version'],
                    'disposition': 'not_adopted', 'reason': 'Historical source is terminal.'}]}
        before = {table: [tuple(r) for r in self.app.store.connection.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                  for table in ('records', 'families', 'history', 'receipts', 'plans', 'goals')}
        tools = ToolRegistry(self.app, token)
        denied = tools.call('planner.commit_daily', args)
        self.assertFalse(denied['ok'], denied)
        self.assertEqual(denied['error']['code'], 'CANDIDATE_COVERAGE_INCOMPLETE')
        for name, expected in [('expected_ids', []), ('missing_ids', []), ('unexpected_ids', [gap['id']])]:
            match = re.search(r'"?'+name+r'"?\s*[:=]\s*(\[[^\]]*\])', denied['error']['message'])
            self.assertIsNotNone(match, denied)
            self.assertEqual(json.loads(match.group(1)), expected)
        after = {table: [tuple(r) for r in self.app.store.connection.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                 for table in before}
        self.assertEqual(after, before)
        args.update(request_id=self.key(), candidate_decisions=[])
        result = tools.call('planner.commit_daily', args)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['data']['plan']['items'], [])
        self.assertEqual([e['id'] for e in self.app.store.list('event', 'f')], [event['id']])
        self.assertEqual(self.app.event('f', event['id'])['status'], 'done')


class CorrectedSourcePriorityTests(unittest.TestCase):
    setUp = learning_fixtures.LearningEvidenceTests.setUp
    tearDown = learning_fixtures.LearningEvidenceTests.tearDown
    key = learning_fixtures.LearningEvidenceTests.key
    ref = learning_fixtures.LearningEvidenceTests.ref
    source = learning_fixtures.LearningEvidenceTests.source
    sync = learning_fixtures.LearningEvidenceTests.sync
    historical_event = learning_fixtures.LearningEvidenceTests.historical_event
    report = learning_fixtures.LearningEvidenceTests.report
    progress_worker = learning_fixtures.LearningEvidenceTests.progress_worker
    completed_original = learning_fixtures.LearningEvidenceTests.completed_original

    def correction_and_daily(self):
        eid, old = self.completed_original()
        self.source('batch-2', 2, 'incomplete', 'batch-1')
        corrected = self.sync('batch-2')
        self.app.clock = Clock('2026-10-05T09:00:00+08:00')
        with self.app.store.transaction():
            JobQueue(self.app).enqueue('f', 'independent-next-day-daily', {
                'kind': 'daily', 'business_date': '2026-10-05'})
        return eid, old, self.app.store.get('evidence', corrected['correction_ids'][0], 'f')

    def commit_empty_daily(self, job, tools):
        self.assertEqual(job['kind'], 'daily')
        context = tools.call('context.read', {})['result']
        self.assertEqual(context['candidates'], [])
        result = tools.call('planner.commit_daily', {
            'request_id': self.key(), 'business_date': '2026-10-05',
            'expected_family_version': context['family_version'],
            'new_events': [], 'plan_refs': [], 'candidate_decisions': [], 'pending_decisions': []})
        self.assertTrue(result['ok'], result)
        return {}

    def test_valid_correction_review_then_report_progress_precedes_new_daily(self):
        eid, old, correction = self.correction_and_daily()
        observed = []
        def review(job, tools):
            observed.append(job['kind'])
            self.assertEqual(job['kind'], 'review')
            self.assertEqual(job['correction_evidence_id'], correction['id'])
            _, binding = self.app.auth(tools.token, {'service'})
            self.assertTrue(binding['no_contact'])
            report = tools.call('progress.submit', {
                'request_id': self.key(), 'report_id': 'independent-correction-report',
                'event_ids': [eid], 'facts': ['The authoritative source withdraws prior completion.'],
                'remaining_work': ['synthetic-lesson-1'], 'evidence_refs': [self.ref(correction)]})
            self.assertTrue(report['ok'], report)
            saved = report['result']['data']
            result = tools.call('service.record_outcome', {
                'request_id': self.key(), 'outcome': 'action_taken',
                'reason': 'Persisted original source correction report without contact.',
                'refs': [{'kind': 'event', 'id': eid, 'version': self.app.event('f', eid)['version']},
                         {'kind': 'report', 'id': saved['id'], 'version': saved['version']}]})
            self.assertTrue(result['ok'], result)
            return {'outcome_id': binding['run_id']}
        first = Worker(self.app, review).run_once()
        self.assertEqual(first['outcome'], 'succeeded', first)
        self.assertEqual(observed, ['review'])
        self.assertEqual(self.app.store.get('job', 'independent-next-day-daily', 'f')['attempt'], 0)
        self.assertEqual(self.app.event('f', eid)['status'], 'done')
        self.assertTrue(self.app.event('f', eid)['update_pending'])
        self.progress_worker('in_progress')
        self.assertEqual(self.app.event('f', eid)['status'], 'in_progress')
        self.assertFalse(self.app.event('f', eid)['update_pending'])
        self.assertEqual(self.app.event('f', eid)['remaining_work'], ['synthetic-lesson-1'])
        self.assertFalse(self.app.store.get('evidence', old['id'], 'f')['valid'])
        self.assertEqual(Worker(self.app, self.commit_empty_daily).run_once()['outcome'], 'succeeded')
        self.assertEqual(len(self.app.store.list('event', 'f')), 1)
        self.assertEqual(self.app.store.list('operation', 'f'), [])
        self.assertFalse(self.app.store.connection.execute('SELECT 1 FROM report_links WHERE resolved=0').fetchone())

    def test_invalid_correction_is_skipped_without_blocking_daily_or_self_waking(self):
        eid, _, correction = self.correction_and_daily()
        with self.app.store.transaction():
            job = next(j for j in self.app.store.list('job', 'f') if j.get('correction_evidence_id') == correction['id'])
            job['correction_evidence_version'] = 999
            self.app.store.put('job', job['id'], 'f', job, job['version'])
        result = Worker(self.app, self.commit_empty_daily).run_once()
        self.assertEqual(result['outcome'], 'succeeded', result)
        # Invalid review may be filtered before or just after the eligible daily;
        # either way it must never invoke the Service handler or retain a lease.
        self.assertFalse(Worker(self.app, lambda *_: self.fail('Invalid review invoked handler')).run_once()['ran'])
        stale = self.app.store.get('job', job['id'], 'f')
        self.assertEqual(stale['status'], 'superseded')
        self.assertEqual(stale['attempt'], 0)
        self.assertEqual(self.app.event('f', eid)['status'], 'done')
        before = (self.app.store.family('f')['version'],
                  self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0])
        calls = []
        worker = Worker(self.app, lambda *_: calls.append(True))
        for _ in range(20):
            self.assertFalse(worker.run_once()['ran'])
        after = (self.app.store.family('f')['version'],
                 self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0])
        self.assertEqual(after, before)
        self.assertEqual(calls, [])
        self.assertEqual(self.app.store.list('operation', 'f'), [])


if __name__ == '__main__':
    unittest.main()
