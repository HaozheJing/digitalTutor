"""Source availability can resume original debt without business version/self wake."""
from datetime import timedelta
from digital_tutor.application import Application,Clock
from digital_tutor.jobs import JobQueue
from digital_tutor.domain import BusinessError
from digital_tutor.material_recovery import record_read,observe
from digital_tutor.wechat import read_messages,send_message
from digital_tutor.outbox import reconcile
from digital_tutor.runtime_outcome import record_outcome
import test_stage1 as baseline


class SourceRecoveryTests(baseline.CoreTests):
    def start_wait(self):
        self.message=self.incoming()
        self.app.strict_runtime=True; self.app.mode='passive'
        self.app.runtime_clock=Clock('2026-10-04T10:00:00+08:00')
        queue=JobQueue(self.app);claim=queue.claim('source-worker',120);token=queue.session(claim)
        record_read(self.app,token,'knowledge.read',{'document_id':'LESSON-L2-FOX-D01'},
                    {'ok':False,'error':{'code':'KNOWLEDGE_UNAVAILABLE'}})
        queue.finish(claim,'waiting','source_unavailable');self.app.revoke_session(token)
        return claim

    def test_unchanged_failure_then_actual_availability_resumes_original_once(self):
        original=self.start_wait();version=self.app.store.family('f')['version'];calls=[]
        def unavailable(wait):
            calls.append(wait['id']);raise BusinessError('KNOWLEDGE_UNAVAILABLE','source unavailable')
        for _ in range(20):
            observe(self.app,unavailable)
            self.assertIsNone(JobQueue(self.app).claim('idle'))
        self.assertEqual(calls,[])
        self.app.runtime_clock.fixed+=timedelta(seconds=31)
        observe(self.app,unavailable)
        self.assertEqual(len(calls),1)
        self.assertIsNone(JobQueue(self.app).claim('idle'))
        self.app.close()
        self.app=Application(self.path,Clock('2026-10-04T10:00:00+08:00'),mode='passive',strict_runtime=True)
        self.app.runtime_clock=Clock('2026-10-04T10:01:02+08:00')
        observe(self.app,lambda wait:'verified-original-body-sha')
        resumed=JobQueue(self.app).claim('restored')
        self.assertEqual(resumed['job']['id'],original['job']['id'])
        self.assertFalse(resumed['job']['recovery_only'])
        self.assertEqual(resumed['job']['attempt'],2)
        self.assertEqual(self.app.store.family('f')['version'],version)
        JobQueue(self.app).finish(resumed,'waiting','still_has_debt')
        for _ in range(20):
            observe(self.app,lambda wait:self.fail('No repeat probe after consumed transition'))
            self.assertIsNone(JobQueue(self.app).claim('idle'))
        self.assertEqual(self.app.store.family('f')['version'],version)

    def test_scope_or_schema_failure_cannot_become_source_recovery(self):
        self.incoming();self.app.strict_runtime=True;self.app.mode='passive'
        queue=JobQueue(self.app);claim=queue.claim('source-worker');token=queue.session(claim)
        record_read(self.app,token,'knowledge.read',{'document_id':'OUTSIDE'},
                    {'ok':False,'error':{'code':'KNOWLEDGE_SCOPE_FORBIDDEN'}})
        self.assertEqual(self.app.store.list('runtime_material_wait','f'),[])

    def test_internal_receipt_accounting_does_not_consume_available_source(self):
        message=self.incoming();self.app.strict_runtime=True;self.app.mode='passive'
        self.app.runtime_clock=Clock('2026-10-04T10:00:00+08:00')
        queue=JobQueue(self.app);claim=queue.claim('original',120);token=queue.session(claim)
        read_messages(self.app,token,{'mode':'all'})
        record_read(self.app,token,'knowledge.read',{'document_id':'LESSON-L2-FOX-D01'},
                    {'ok':False,'error':{'code':'KNOWLEDGE_UNAVAILABLE'}})
        self.app.mock_send_outcome='unknown';args=self.reply(message)
        args['reply_to'][0]['resolves_intent']=False
        operation=send_message(self.app,token,args)['data']
        def outcome(capability,request):
            msg=self.app.store.get('message',message['id'],'f')
            op=self.app.store.get('operation',operation['id'],'f')
            return record_outcome(self.app,capability,{'request_id':request,'outcome':'waiting',
                'reason':'Original answer still needs source','remaining_work':['Original substantive answer'],
                'resume_condition':'Approved source available',
                'refs':[{'kind':'message','id':msg['id'],'version':msg['version']},
                        {'kind':'operation','id':op['id'],'version':op['version']}]})
        outcome(token,'first-wait');queue.finish(claim,'waiting');self.app.revoke_session(token)
        self.app.runtime_clock.fixed+=timedelta(seconds=31)
        observe(self.app,lambda wait:'approved-original-source')
        self.assertIsNone(queue.claim('still-unknown'))
        reconcile(self.app,'f',operation['id'])
        internal=queue.claim('receipt-accounting');self.assertTrue(internal['job']['recovery_only'])
        self.assertEqual(self.app.store.list('runtime_material_wait','f')[0]['status'],'available')
        internal_token=queue.session(internal);outcome(internal_token,'receipt-wait')
        queue.finish(internal,'waiting');self.app.revoke_session(internal_token)
        answer=queue.claim('source-restored')
        self.assertEqual(answer['job']['id'],claim['job']['id']);self.assertFalse(answer['job']['recovery_only'])
        self.assertEqual(answer['job']['source_recovery_attempt'],1)
        self.assertEqual(self.app.store.list('runtime_material_wait','f')[0]['status'],'resolved')


for name in baseline.CoreTests.__dict__:
    if name.startswith('test_'):setattr(SourceRecoveryTests,name,None)
