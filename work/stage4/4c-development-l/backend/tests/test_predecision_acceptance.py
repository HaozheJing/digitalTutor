"""Negative controls over an immutable evidence copy; no runtime imports or DB writes."""
import copy
import importlib.util
import unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('intraday_predecision_candidate', Path(__file__).resolve().parents[1] / 'scripts' / 'stage4_predecision_checks.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
coverage, source_digest = helper.coverage, helper.source_digest


class PredecisionCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        message = {'id':'message-a','body':'Please send a summary tomorrow.',
                   'direction':'inbound','interpreted':True,
                   'intents':[{'intent_id':'intent-a','text':'future summary','requires_reply':True,
                    'service_kind':'future_delivery','service_authorization':'send a summary tomorrow',
                    'reply_operation_refs':[],'partial_reply_operation_refs':['operation-a']}]}
        cls.base = {
            'message':[message],
            'pending':[{'id':'pending-a','version':1,'source_message_id':'message-a','source_intent_id':'intent-a',
                        'candidate_source_id':'source-a','planning_requested':True,'await_decision':True,
                        'disposition':'pending','event_id':None,'decision_revision':1}],
            'candidate_source':[{'id':'source-a','source_kind':'family_service_request','enabled':True,
                      'pending_id':'pending-a','source_message_id':'message-a','source_intent_id':'intent-a',
                      'source_message_digest':source_digest(message)}],
            'operation':[{'id':'operation-a','stage':'sent','body':'Registered; decision pending.',
                    'reply_to':[{'message_id':'message-a','intent_id':'intent-a','resolves_intent':False}]}],
            'job':[{'id':'service-a','kind':'service','message_id':'message-a','status':'waiting','run_id':'run-a'},
                   {'id':'admission-a','kind':'daily','planning_reason':'admission','pending_id':'pending-a',
                    'decision_revision':1,'status':'ready','attempt':0}],
            'run_outcome':[{'id':'run-a','job_id':'service-a','outcome':'waiting',
                 'remaining_work':['arrangement decision'],'resume_condition':'pending decision changes',
                 'observed_planning_inputs':[{'id':'pending-a','version':1,'decision_revision':1,'await_decision':True}]}]}
        cls.channel_base=[{'operation_id':'operation-a','stage':'sent','payload':{'body':'Registered; decision pending.'}}]

    def setUp(self):
        self.records = copy.deepcopy(self.base)
        self.channel = copy.deepcopy(self.channel_base)
        self.pending = self.records['pending'][0]
        self.msg = next(m for m in self.records['message'] if m['id'] == self.pending['source_message_id'])
        self.intent = next(i for i in self.msg['intents'] if i['intent_id'] == self.pending['source_intent_id'])
        self.source = next(s for s in self.records['candidate_source'] if s['id'] == self.pending['candidate_source_id'])
        self.service = next(j for j in self.records['job'] if j.get('message_id') == self.msg['id'])
        self.admission = next(j for j in self.records['job'] if j.get('planning_reason') == 'admission')
        self.outcome = next(o for o in self.records['run_outcome'] if o['id'] == self.service['run_id'])
        self.op = next(o for o in self.records['operation'] if o['id'] in self.intent['partial_reply_operation_refs'])

    def rejected(self):
        actual = coverage(self.records, self.channel)
        self.assertFalse(actual['all_unresolved_covered'])
        self.assertTrue(actual['uncovered'])

    def test_frozen_actual_pair_is_covered(self):
        actual = coverage(self.records, self.channel)
        self.assertTrue(actual['all_unresolved_covered'])
        self.assertEqual(len(actual['covered']), 1)

    def test_extra_uncovered_intent_cannot_borrow_pending(self):
        self.msg['intents'].append({'intent_id': 'negative-extra', 'text': 'independent responsibility',
                                   'requires_reply': True, 'reply_operation_refs': []})
        self.source['source_message_digest'] = source_digest(self.msg)
        actual = coverage(self.records, self.channel)
        self.assertEqual(len(actual['covered']), 1)
        self.rejected()

    def test_fake_pending_without_real_candidate_source_fails(self):
        self.pending['candidate_source_id'] = 'negative-nonexistent'
        self.rejected()

    def test_wrong_source_pair_fails(self):
        self.source['source_intent_id'] = 'negative-other'
        self.rejected()

    def test_stale_source_digest_fails(self):
        self.source['source_message_digest'] = 'negative-stale'
        self.rejected()

    def test_decided_but_unanswered_is_not_predecision_exception(self):
        self.pending.update(await_decision=False, disposition='created', event_id='negative-event')
        self.rejected()

    def test_unknown_partial_is_not_confirmed_explanation(self):
        self.op['stage'] = 'unknown'
        self.rejected()

    def test_partial_without_independent_channel_fails(self):
        self.channel = []
        self.rejected()

    def test_final_reply_is_not_partial_evidence(self):
        for ref in self.op['reply_to']:
            ref['resolves_intent'] = True
        self.rejected()

    def test_outdated_outcome_not_current_wait(self):
        self.service['run_id'] = 'negative-new-run'
        self.rejected()

    def test_stale_observed_pending_version_fails(self):
        for item in self.outcome['observed_planning_inputs']:
            item['version'] += 1
        self.rejected()

    def test_missing_admission_owner_fails(self):
        self.records['job'].remove(self.admission)
        self.rejected()

    def test_other_decision_revision_fails(self):
        self.admission['decision_revision'] += 1
        self.rejected()

    def test_ordinary_question_cannot_borrow_future_pending(self):
        self.intent.update(service_kind='reply', service_authorization=None)
        self.rejected()


    def test_another_family_unanswered_blocks_global_exception(self):
        other = {'message':[{'id':'message-other','direction':'inbound','interpreted':True,
                 'intents':[{'intent_id':'other','requires_reply':True,'reply_operation_refs':[]}]}]}
        passed, details = helper.all_waiting_covered({'a':self.records,'b':other}, self.channel)
        self.assertFalse(passed)
        self.assertTrue(details['a']['all_unresolved_covered'])
        self.assertTrue(details['b']['uncovered'])

    def test_missing_intent_id_is_uncovered_not_exception(self):
        self.intent.pop('intent_id')
        actual = coverage(self.records, self.channel)
        self.assertFalse(actual['all_unresolved_covered'])
        self.assertEqual(actual['uncovered'], [{'message_id': self.msg['id'], 'reason': 'missing_intent_id'}])

    def test_empty_set_does_not_claim_predecision_coverage(self):
        passed, details = helper.all_waiting_covered({'a':{'message':[]}}, [])
        self.assertFalse(passed)

    def test_resolved_other_family_does_not_invalidate_exact_wait(self):
        passed, details = helper.all_waiting_covered({'a':self.records,'b':{'message':[]}}, self.channel)
        self.assertTrue(passed)


if __name__ == '__main__':
    unittest.main(verbosity=2)

