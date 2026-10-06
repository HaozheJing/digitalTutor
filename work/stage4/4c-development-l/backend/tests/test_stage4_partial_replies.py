import unittest
import test_stage4_host as fixtures
from digital_tutor.jobs import recovery_state
from digital_tutor.runtime_outcome import record_outcome
from digital_tutor.wechat import send_message
from digital_tutor.tools import ToolRegistry
from digital_tutor.mock_channel import channel_for


class PartialReplyTests(unittest.TestCase):
    setUp=fixtures.HostContractTests.setUp
    tearDown=fixtures.HostContractTests.tearDown
    interpret=fixtures.HostContractTests.interpret
    args=fixtures.HostContractTests.args

    def partial(self):
        self.interpret();args=self.args()
        args['reply_to'][0]['resolves_intent']=False
        args['body']='正文读取暂不可用，这个问题仍待回答。'
        op=send_message(self.app,self.token,args)['data']
        self.msg=self.app.store.get('message',self.msg['id'],'A')
        return op,args

    def test_clarification_retains_question_and_can_later_answer_once(self):
        op,args=self.partial()
        self.assertEqual(self.msg['intents'][0]['reply_operation_refs'],[])
        self.assertEqual(self.msg['intents'][0]['partial_reply_operation_refs'],[op['id']])
        outcome=record_outcome(self.app,self.token,{'request_id':'wait','outcome':'waiting',
            'refs':[{'kind':'message','id':self.msg['id'],'version':self.msg['version']},
                    {'kind':'operation','id':op['id'],'version':op['version']}],
            'reason':'正文不可用','remaining_work':['回答原课程问题'],'resume_condition':'正文恢复或收到补充资料'})
        self.assertEqual(outcome['awaiting_operation_ids'],[])
        self.assertIsNone(recovery_state(self.app,'A',self.app.store.get('job',self.claim['job']['id'],'A')))
        self.q.finish(self.claim,'waiting');self.app.revoke_session(self.token)
        for _ in range(20):self.assertIsNone(self.q.claim('idle'))
        channel=self.app.open_session('A','channel')
        self.app.ingest_message(channel,{'external_id':'source-added','body':'补充提供课程原文。','occurred_at':self.app.clock.iso()})
        self.app.revoke_session(channel)
        claim=self.q.claim('resume');self.assertEqual(claim['job']['id'],self.claim['job']['id'])
        token=self.q.session(claim);self.assertFalse(claim['job']['recovery_only'])
        registry=ToolRegistry(self.app,token)
        context=registry.call('context.read',{})['result']
        self.assertTrue(context['service_continuations'])
        read=registry.call('wechat.read_messages',{'mode':'all','direction':'inbound'})
        self.assertTrue(read['ok'],read)
        answer=self.args();answer['request_id']='answer';answer['body']='依据补充正文回答原问题。'
        result=send_message(self.app,token,answer)['data']
        self.assertNotEqual(result['id'],op['id'])
        msg=self.app.store.get('message',self.msg['id'],'A')
        self.assertEqual(msg['intents'][0]['reply_operation_refs'],[result['id']])
        self.assertEqual(channel_for(self.app).count(),2)
        self.assertEqual(send_message(self.app,token,answer)['outcome'],'duplicate')
        self.assertEqual(channel_for(self.app).count(),2)

    def test_partial_delivery_cannot_be_final_action_taken(self):
        op,_=self.partial()
        result=ToolRegistry(self.app,self.token).call('service.record_outcome',{
            'request_id':'bad-end','outcome':'action_taken','reason':'说明已送达',
            'refs':[{'kind':'message','id':self.msg['id'],'version':self.msg['version']},
                    {'kind':'operation','id':op['id'],'version':op['version']}]})
        self.assertEqual(result['error']['code'],'OUTCOME_INCOMPLETE')

    def test_repeated_partial_wording_cannot_create_second_dispatch(self):
        op,args=self.partial();args['request_id']='again'
        args['body']='换句话说资料暂缺。';args['expected_family_version']=self.app.store.family('A')['version']
        result=ToolRegistry(self.app,self.token).call('wechat.send_message',args)
        self.assertEqual(result['error']['code'],'OPERATION_ALREADY_EXISTS')
        self.assertEqual(channel_for(self.app).count(),1)

    def test_unknown_partial_receipt_recovery_keeps_waiting_without_self_wakeup(self):
        from digital_tutor.outbox import reconcile
        self.app.mock_send_outcome='unknown'
        op,_=self.partial()
        refs=[{'kind':'message','id':self.msg['id'],'version':self.msg['version']},
              {'kind':'operation','id':op['id'],'version':op['version']}]
        record_outcome(self.app,self.token,{'request_id':'unknown-wait','outcome':'waiting',
            'refs':refs,'reason':'澄清回执未知且原问题缺资料','remaining_work':['原问题待答'],
            'resume_condition':'核实原回执并补齐资料'})
        self.q.finish(self.claim,'waiting');self.app.revoke_session(self.token)
        reconcile(self.app,'A',op['id'])
        claim=self.q.claim('receipt-recovery');self.assertTrue(claim['job']['recovery_only'])
        token=self.q.session(claim);registry=ToolRegistry(self.app,token)
        message=self.app.store.get('message',self.msg['id'],'A')
        operation=self.app.store.get('operation',op['id'],'A')
        refs=[{'kind':'message','id':message['id'],'version':message['version']},
              {'kind':'operation','id':operation['id'],'version':operation['version']}]
        bad=registry.call('service.record_outcome',{'request_id':'wrong-completion',
            'outcome':'action_taken','refs':refs,'reason':'仅澄清回执已确认'})
        self.assertEqual(bad['error']['code'],'OUTCOME_INCOMPLETE')
        good=registry.call('service.record_outcome',{'request_id':'keep-original-wait',
            'outcome':'needs_input','refs':refs,'reason':'澄清已送达，原问题仍缺资料',
            'remaining_work':['原问题待答'],'resume_condition':'补齐原资料'})
        self.assertTrue(good['ok'])
        self.q.finish(claim,'waiting');self.app.revoke_session(token)
        self.assertEqual(message['intents'][0]['reply_operation_refs'],[])
        self.assertEqual(message['intents'][0]['partial_reply_operation_refs'],[op['id']])
        for _ in range(20):self.assertIsNone(self.q.claim('no-change'))
        self.assertEqual(channel_for(self.app).count(),1)

