import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from digital_tutor.application import Application, Clock
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue
from digital_tutor.runtime_outcome import record_outcome
from digital_tutor.runtime_assembly import approval, material_snapshot
from digital_tutor.tools import ToolRegistry
from digital_tutor.wechat import prepare_message
from digital_tutor.outbox import dispatch

class HostContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.clock=self.root/'clock.json';self.clock.write_text(json.dumps({'now':'2026-10-04T10:00:00+08:00'}))
        self.app=Application(self.root/'business.sqlite',Clock(shared_path=self.clock),mode='passive',strict_runtime=True)
        self.app.create_family('A','mock-A','mock-chat-A')
        channel=self.app.open_session('A','channel')
        self.msg=self.app.ingest_message(channel,{'external_id':'m1','body':'请回答课程问题','occurred_at':self.app.clock.iso()})['data']
        self.app.revoke_session(channel)
        self.q=JobQueue(self.app);self.claim=self.q.claim('host-test');self.token=self.q.session(self.claim)
        self.registry=ToolRegistry(self.app,self.token)

    def tearDown(self):
        self.app.close();self.tmp.cleanup()

    def interpret(self):
        self.app.interpret(self.token,{'request_id':'interpret','message_id':self.msg['id'],'expected_version':self.msg['version'],
            'intents':[{'intent_id':'q','text':'课程问题','requires_reply':True}]})
        self.msg=self.app.store.get('message',self.msg['id'],'A')

    def args(self):
        return {'request_id':'send','expected_family_version':self.app.store.family('A')['version'],'body':'依据课程的回答',
            'action_kind':'reply','reply_to':[{'message_id':self.msg['id'],'intent_id':'q'}]}

    def outcome(self,kind='no_contact'):
        return {'request_id':'outcome','outcome':kind,'reason':'本次只做内部核实',
            'refs':[{'kind':'message','id':self.msg['id'],'version':self.msg['version']}]}

    def test_internal_audit_does_not_change_business_version(self):
        before=self.app.store.family('A')['version']
        value=record_outcome(self.app,self.token,self.outcome())
        self.assertEqual(value['outcome'],'no_contact')
        self.assertEqual(before,self.app.store.family('A')['version'])
        self.assertFalse(self.app.store.list('operation','A'))
        self.assertEqual(value,record_outcome(self.app,self.token,self.outcome()))

    def test_text_claim_without_real_effect_is_rejected(self):
        value=self.registry.call('service.record_outcome',self.outcome('action_taken'))
        self.assertEqual(value['error']['code'],'OUTCOME_EVIDENCE_MISSING')

    def test_stale_or_foreign_result_ref_rejected(self):
        args=self.outcome();args['refs'][0]['version']=100
        self.assertEqual(self.registry.call('service.record_outcome',args)['error']['code'],'EVIDENCE_SUPERSEDED')
        args['refs'][0]['id']='not-real'
        self.assertEqual(self.registry.call('service.record_outcome',args)['error']['code'],'RECORD_NOT_FOUND')

    def test_wait_requires_remaining_and_resume(self):
        value=self.registry.call('service.record_outcome',self.outcome('waiting'))
        self.assertEqual(value['error']['code'],'OUTCOME_INCOMPLETE')

    def test_non_worker_cannot_record_outcome(self):
        token=self.app.open_session('A','service')
        value=ToolRegistry(self.app,token).call('service.record_outcome',self.outcome())
        self.assertEqual(value['error']['code'],'RUNTIME_BINDING_REQUIRED')

    def test_service_can_locate_existing_responsibility_without_id(self):
        with self.app.store.transaction():
            self.app.store.put('event','old','A',{'goal_text':'原目标','status':'waiting','completion':{'kind':'goal'},'waiting':{'reason':'资料不足'}})
        value=self.registry.call('context.read',{})
        self.assertEqual(value['result']['responsibility_index'][0]['id'],'old')

    def test_passive_denies_unbound_old_capability(self):
        self.interpret();token=self.app.open_session('A','service')
        value=ToolRegistry(self.app,token).call('wechat.send_message',self.args())
        self.assertEqual(value['error']['code'],'SEND_CAPABILITY_REQUIRED')

    def test_reply_scope_cannot_borrow_message_received_after_claim(self):
        self.interpret()
        channel=self.app.open_session('A','channel')
        late=self.app.ingest_message(channel,{'external_id':'later','body':'另一个问题','occurred_at':self.app.clock.iso()})['data']
        args=self.args();args['reply_to']=[{'message_id':late['id'],'intent_id':'late'}]
        value=self.registry.call('wechat.send_message',args)
        self.assertEqual(value['error']['code'],'RUN_REVOKED')

    def test_new_process_reads_authoritative_cutoff_before_dispatch(self):
        self.interpret();op=prepare_message(self.app,self.token,self.args())['data']
        self.clock.write_text(json.dumps({'now':'2026-10-04T21:00:00+08:00'}))
        code='''import json,sys
from digital_tutor.application import Application,Clock
from digital_tutor.outbox import dispatch
x=json.load(sys.stdin)
a=Application(x['db'],Clock(shared_path=x['clock']),mode='passive',strict_runtime=True)
v=dispatch(a,'A',x['op'])
print(json.dumps({'stage':v['stage'],'reason':v.get('cancel_reason'),'dispatch_count':v['dispatch_count']}))
a.close()
'''
        got=subprocess.run([sys.executable,'-c',code],input=json.dumps({'db':self.app.store.path,'clock':str(self.clock),'op':op['id']}),text=True,capture_output=True,check=True)
        value=json.loads(got.stdout)
        self.assertEqual(value,{'stage':'cancelled','reason':'WINDOW_CLOSED','dispatch_count':0})

    def test_pending_legacy_operation_cannot_bypass_passive_gate(self):
        self.interpret();op=prepare_message(self.app,self.token,self.args())['data']
        with self.app.store.transaction():
            op.pop('send_capability');self.app.store.put('operation',op['id'],'A',op,op['version'])
        value=dispatch(self.app,'A',op['id'])
        self.assertEqual(value['cancel_reason'],'SEND_CAPABILITY_REQUIRED')
        self.assertEqual(value['dispatch_count'],0)

    def test_material_snapshot_only_contains_approved_files(self):
        project=Path(__file__).resolve().parents[2]
        approved=approval(project)
        snap=material_snapshot(project,self.root,approved)
        self.assertEqual({p.name for p in (snap/'skills').iterdir()},{'explain-lesson','plan-proactive-service'})
        expected={str(Path(f).relative_to('agent')) for f in approved['files']}
        self.assertEqual({str(f.relative_to(snap)) for f in snap.rglob('*') if f.is_file()},expected)

if __name__=='__main__':unittest.main()
