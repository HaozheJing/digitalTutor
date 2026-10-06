import test_stage4_host as fixtures
import unittest
from digital_tutor.runtime_outcome import record_outcome
from digital_tutor.runtime_assembly import assemble
from digital_tutor.wechat import prepare_message
from digital_tutor.outbox import dispatch
from pathlib import Path
import json

class ReviewRegressions(unittest.TestCase):
    setUp=fixtures.HostContractTests.setUp
    tearDown=fixtures.HostContractTests.tearDown
    interpret=fixtures.HostContractTests.interpret
    args=fixtures.HostContractTests.args
    outcome=fixtures.HostContractTests.outcome
    def test_finalized_no_contact_cannot_send_or_write(self):
        self.interpret()
        record_outcome(self.app,self.token,self.outcome())
        value=self.registry.call('wechat.send_message',self.args())
        self.assertEqual(value['error']['code'],'OUTCOME_FINALIZED')
        value=self.registry.call('control.apply',{'request_id':'late-write'})
        self.assertEqual(value['error']['code'],'OUTCOME_FINALIZED')
        self.assertFalse(self.app.store.list('operation','A'))

    def test_direct_revocation_fences_queued_dispatch(self):
        self.interpret();op=prepare_message(self.app,self.token,self.args())['data']
        self.app.revoke_session(self.token)
        value=dispatch(self.app,'A',op['id'])
        self.assertEqual(value['stage'],'cancelled')
        self.assertEqual(value['cancel_reason'],'RUN_REVOKED')
        self.assertEqual(value['dispatch_count'],0)

    def test_assembly_removes_lease_before_prompt_serialization(self):
        self.interpret();op=prepare_message(self.app,self.token,self.args())['data']
        project=Path(__file__).resolve().parents[2]
        context,system=assemble(project,self.app,self.token,self.claim['job'])
        encoded=json.dumps(context)
        self.assertNotIn(self.claim['lease']['token'],encoded)
        self.assertNotIn(self.token,encoded)
        self.assertNotIn('"lease"',encoded)
        self.assertIn(op['id'],encoded)

    def test_knowledge_fault_is_explicit_strict_and_has_no_effect(self):
        from digital_tutor.runtime_hooks import gate
        from digital_tutor.domain import BusinessError
        self.app.test_hooks={'before:knowledge.read':{'fault':'knowledge_unavailable'}}
        before=self.app.store.family('A')['version']
        with self.assertRaises(BusinessError) as exc:
            gate(self.app,self.token,'before:knowledge.read')
        self.assertEqual(exc.exception.code,'KNOWLEDGE_UNAVAILABLE')
        self.assertEqual(self.app.store.family('A')['version'],before)
        self.assertFalse(self.app.store.list('operation','A'))
        self.app.strict_runtime=False
        with self.assertRaises(BusinessError) as exc:
            gate(self.app,self.token,'before:knowledge.read')
        self.assertEqual(exc.exception.code,'FAULT_SCOPE')

    def test_strict_navigation_does_not_supply_lesson_summary(self):
        from digital_tutor.materials import dispatch_material
        class Navigation:
            def dispatch(self,*args):
                return {'documents':[{'document_id':'lesson','title':'Locator','navigation_summary':'Body facts'}], 'navigation_is_evidence':False}
        self.app.knowledge=Navigation();self.app.knowledge_bindings={}
        value=dispatch_material(self.app,self.token,'knowledge.list',{})
        self.assertNotIn('navigation_summary',value['documents'][0])
        self.assertEqual(value['documents'][0]['document_id'],'lesson')
        self.assertFalse(value['navigation_is_evidence'])
