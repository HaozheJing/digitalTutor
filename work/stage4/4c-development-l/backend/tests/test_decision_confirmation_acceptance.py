"""Post-run confirmation checks: actual candidate helper, no provider/runtime imports."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

scripts = Path(__file__).resolve().parents[1] / 'scripts'
def load_file(name):
    spec = importlib.util.spec_from_file_location(name, scripts / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
source_helper = load_file('stage4_predecision_checks')
old = sys.modules.get('stage4_predecision_checks')
sys.modules['stage4_predecision_checks'] = source_helper
try:
    helper = load_file('stage4_decision_confirmation_checks')
finally:
    if old is None:
        del sys.modules['stage4_predecision_checks']
    else:
        sys.modules['stage4_predecision_checks'] = old


class DecisionConfirmationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.message = {'id':'message-a','body':'Please send a summary tomorrow.',
            'direction':'inbound','interpreted':True,'intents':[{'intent_id':'intent-a',
            'requires_reply':True,'reply_operation_refs':[], 'partial_reply_operation_refs':['operation-a']}]}
        self.decision = {'pending_id':'pending-a','decision_revision':1,
                         'decision_request_id':'decision-a','event_id':'event-a'}
        phase = dict(self.decision,source_message_id='message-a',source_intent_id='intent-a',commitment_id='grant-a')
        self.records = {
            'event':[{'id':'event-a','completion':{'kind':'delivery'},'accepted_commitment':{'id':'grant-a'}}],
            'pending':[dict(id='pending-a',planning_requested=True,await_decision=False,decision_state='created',
                        source_message_id='message-a',source_intent_id='intent-a',candidate_source_id='source-a',
                        **{k:v for k,v in self.decision.items() if k!='pending_id'})],
            'message':[self.message],
            'candidate_source':[{'id':'source-a','enabled':True,'source_kind':'family_service_request',
                'pending_id':'pending-a','source_message_id':'message-a','source_intent_id':'intent-a',
                'source_message_digest':source_helper.source_digest(self.message)}],
            'operation':[{'id':'operation-a','action_kind':'reply','stage':'sent','dispatch_count':1,
                'body':'Arrangement accepted.','recipient':'mock-a','admitted_at':'2026-10-05T10:00:00+08:00',
                'run_id':'run-a','decision_reply_refs':[phase],
                'reply_to':[{'message_id':'message-a','intent_id':'intent-a','resolves_intent':False}]}],
            'run_outcome':[{'run_id':'run-a','delegation_acknowledgements':[
                dict(self.decision,operation_ids=['operation-a'],source_digest=source_helper.source_digest(self.message))]}]}
        self.channel = [{'operation_id':'operation-a','stage':'sent','payload':
            {k:self.records['operation'][0][k] for k in ('body','recipient','admitted_at')}}]
        self.receipts = {'decision-a':{'data':{'accepted_commitment_ids':['grant-a']}}}

    def result(self):
        return helper.decision_confirmation_evidence(self.records,self.channel,self.receipts,'mock-a')

    def test_actual_postdecision_receipt_and_channel(self):
        self.assertTrue(self.result()['accepted_delivery_has_decision_confirmation'])

    def test_predecision_partial_cannot_count_even_with_host_claim(self):
        self.records['operation'][0]['decision_reply_refs'] = []
        self.assertFalse(self.result()['accepted_delivery_has_decision_confirmation'])

    def test_missing_independent_channel_fails(self):
        self.channel = []
        self.assertFalse(self.result()['accepted_delivery_has_decision_confirmation'])

    def test_wrong_committed_grant_fails(self):
        self.receipts['decision-a']['data']['accepted_commitment_ids'] = ['other-grant']
        self.assertFalse(self.result()['accepted_delivery_has_decision_confirmation'])

    def test_ack_from_other_run_cannot_count(self):
        self.records['run_outcome'][0]['run_id'] = 'other-run'
        self.assertFalse(self.result()['accepted_delivery_has_decision_confirmation'])

    def test_stale_source_fails(self):
        self.records['candidate_source'][0]['source_message_digest'] = 'stale'
        self.assertFalse(self.result()['accepted_delivery_has_decision_confirmation'])

    def test_empty_expected_set_is_not_success(self):
        self.records['pending'] = []
        self.assertFalse(self.result()['accepted_delivery_has_decision_confirmation'])
