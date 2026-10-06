"""Host-observed receipt wait regression; deterministic tool drivers, zero API."""
import unittest
import test_manual_demo_proposal as manual
from digital_tutor.jobs import JobQueue,job_operation_observation,recovery_state
from digital_tutor.mock_channel import channel_for
from digital_tutor.application import Clock
from digital_tutor.runtime_outcome import ServiceOutcome


class ReceiptSnapshotTests(unittest.TestCase):
    setUp=manual.ManualDemoTests.setUp
    tearDown=manual.ManualDemoTests.tearDown
    create=manual.ManualDemoTests.create

    def uncertain(self,host_factory=manual.FakeHost):
        demo=self.create(clock='2026-10-04T10:00:00+08:00',host_factory=host_factory)
        demo.app.mock_send_outcome='unknown';channel=channel_for(demo.app);channel.query=lambda _:None
        return demo

    def test_wait_snapshot_is_generated_from_actual_original_job_operations(self):
        demo=self.uncertain();result=demo.turn('Fictional unknown response');self.assertEqual(result['model_runs'],1)
        app=demo.app;family='synthetic-4b1-a';job=app.store.list('job',family)[0]
        outcome=app.store.get('run_outcome',job['run_id'],family)
        self.assertEqual(outcome['observed_job_operations'],job_operation_observation(app,family,job['id']))
        self.assertEqual(outcome['observed_job_operations'][0]['stage'],'unknown')
        self.assertNotIn('observed_job_operations',ServiceOutcome.model_fields)
        self.assertIsNone(recovery_state(app,family,job))

    def test_needs_input_observed_unknown_does_not_self_wake(self):
        class NeedsInput(manual.FakeHost):
            def __call__(host,job,tools):
                actual=tools.call
                def call(name,args):
                    if name=='service.record_outcome' and args['outcome']=='waiting':args={**args,'outcome':'needs_input'}
                    return actual(name,args)
                tools.call=call
                return super().__call__(job,tools)
        demo=self.uncertain(NeedsInput);first=demo.turn('Fictional uncertain needs input')
        self.assertEqual(first['model_runs'],1);self.assertEqual(demo.turn()['model_runs'],0)

    def test_actual_operation_version_change_allows_one_internal_recheck(self):
        demo=self.uncertain();demo.turn('Fictional uncertain question');app=demo.app;family='synthetic-4b1-a'
        op=app.store.list('operation',family)[0]
        with app.store.transaction():
            op['receipts'].append({'stage':'unknown','source':'new_independent_receipt','observed_at':app.clock.iso()})
            app.store.put('operation',op['id'],family,op,op['version'])
        changed=demo.turn();self.assertEqual(changed['model_runs'],1)
        self.assertTrue(demo.host.calls[-1]['recovery_only'])
        self.assertEqual(demo.turn()['model_runs'],0);self.assertEqual(channel_for(app).count(),1)

    def test_old_outcome_without_snapshot_keeps_bounded_legacy_recovery(self):
        demo=self.uncertain();demo.turn('Fictional previous-version run');app=demo.app;family='synthetic-4b1-a'
        job=app.store.list('job',family)[0];outcome=app.store.get('run_outcome',job['run_id'],family)
        with app.store.transaction():
            outcome.pop('observed_job_operations')
            app.store.put('run_outcome',outcome['id'],family,outcome,outcome['version'])
        self.assertEqual(demo.turn()['model_runs'],1);self.assertTrue(demo.host.calls[-1]['recovery_only'])
        self.assertEqual(demo.turn()['model_runs'],0)

    def test_missing_outcome_still_allows_finite_internal_accounting(self):
        class CrashOnce(manual.FakeHost):
            def __call__(host,job,tools):
                actual=tools.call
                def call(name,args):
                    if name=='service.record_outcome' and not getattr(host,'crashed',False):
                        host.crashed=True;raise RuntimeError('Injected exit before outcome')
                    return actual(name,args)
                tools.call=call
                return super().__call__(job,tools)
        demo=self.uncertain(CrashOnce);first=demo.turn('Fictional interrupted send');self.assertEqual(first['exit_code'],1)
        job=demo.app.store.list('job','synthetic-4b1-a')[0]
        demo.app.runtime_clock=Clock(job['not_before'])
        second=demo.turn();self.assertEqual(second['model_runs'],1)
        self.assertTrue(demo.host.calls[-1]['recovery_only']);self.assertEqual(demo.turn()['model_runs'],0)

    def test_known_sent_partial_wait_can_take_new_message_without_recovery_mode(self):
        class PartialHost(manual.FakeHost):
            def __call__(host,job,tools):
                actual=tools.call
                def call(name,args):
                    if name=='wechat.send_message':
                        args={**args,'reply_to':[{**ref,'resolves_intent':False} for ref in args['reply_to']]}
                    if name=='service.record_outcome':
                        args={**args,'outcome':'waiting','remaining_work':['Answer requires source'],
                              'resume_condition':'New message or approved source'}
                    return actual(name,args)
                tools.call=call
                return super().__call__(job,tools)
        demo=self.create(clock='2026-10-04T10:00:00+08:00',host_factory=PartialHost)
        first=demo.turn('Fictional source needed');self.assertEqual(first['model_runs'],1)
        app=demo.app;family='synthetic-4b1-a';oldjob=app.store.list('job',family)[0]
        self.assertEqual(app.store.list('operation',family)[0]['stage'],'sent')
        self.assertEqual(demo.turn()['model_runs'],0)
        token=app.open_session(family,'channel')
        try:msg=app.ingest_message(token,{'external_id':'new-source','body':'Fictional supplementary source','occurred_at':app.clock.iso()})['data']
        finally:app.revoke_session(token)
        claim=JobQueue(app).claim('fresh-inbound')
        self.assertIsNotNone(claim);self.assertFalse(claim['job']['recovery_only'])
        self.assertEqual(claim['job']['id'],oldjob['id'])
        service=JobQueue(app).session(claim);_,binding=app.auth(service,{'service'})
        self.assertIn(msg['id'],binding['reply_scope'])
        JobQueue(app).finish(claim,'waiting','test-finished');app.revoke_session(service)

    def test_unrelated_business_version_cannot_repeat_an_observed_unknown_wait(self):
        demo=self.uncertain();demo.turn('Fictional uncertain answer');app=demo.app;family='synthetic-4b1-a'
        with app.store.transaction():app.store.bump(family)
        self.assertEqual(demo.turn()['model_runs'],0)

    def test_snapshot_excludes_different_job_and_family_operations(self):
        demo=self.uncertain();demo.turn('Fictional original answer');app=demo.app;family='synthetic-4b1-a'
        job=app.store.list('job',family)[0];expected=job_operation_observation(app,family,job['id'])
        app.create_family('other-synthetic','other-synthetic-uid','mock:other-synthetic')
        with app.store.transaction():
            # Explicit history fixtures test scope; they are never dispatched.
            app.store.put('operation','foreign-scope','other-synthetic',{'stage':'unknown','send_capability':{'job_id':job['id']}})
            app.store.put('operation','different-job',family,{'stage':'unknown','send_capability':{'job_id':'different-original-job'}})
        self.assertEqual(job_operation_observation(app,family,job['id']),expected)
