"""Original accepted window expiry never creates new contact or moves its deadline."""
from datetime import timedelta
import unittest

import test_intraday_runtime as fixtures
from digital_tutor.commitment_review import observe_commitment, validate_expiration_evidence, ensure_expiration_evidence
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue
from digital_tutor.mock_channel import channel_for
from digital_tutor.scheduler import Scheduler, Worker
from digital_tutor.scheduling import step_key


class CommitmentExpirationTests(fixtures.IntradayRuntimeTests):
    def expired(self):
        event=self.event('original','2026-10-05T10:00:00+08:00','2026-10-05T16:00:00+08:00')
        event=self.put('event',event['id'],{**event,'original_deadline':'Original family deadline',
            'evidence_refs':[],'completion_history':[],'deadline_history':[],
            'remaining_work':['lesson-original'],'proposed_change':None,
            'accepted_commitment':{'id':'historical-accepted-grant','status':'active','schedule_revision':1,
                'requirement_revision':1,'authorized_step_key':step_key(event),
                'valid_from':'2026-10-05T10:00:00+08:00','valid_until':'2026-10-05T16:00:00+08:00',
                'authorized_target_refs':['lesson-original'],'authorized_action_kinds':['remind_makeup','verify_learning']}})
        self.plan([event['id']]);self.app.clock.fixed+=timedelta(hours=8)
        return event

    def test_expiry_schedules_once_without_business_versions_or_deadline_changes(self):
        event=self.expired();version=self.app.store.family('f')['version']
        self.assertEqual(Scheduler(self.app).tick()['created'],1)
        history=self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0]
        for _ in range(20):self.assertEqual(Scheduler(self.app).tick()['created'],0)
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0],history)
        self.assertEqual(self.app.store.family('f')['version'],version)
        self.assertEqual(self.app.store.get('event',event['id'],'f'),event)

    def test_unknown_and_confirmed_original_operations_are_not_called_unexecuted(self):
        event=self.expired()
        self.assertEqual(observe_commitment(self.app,'f',event)['execution_fact'],'no_recorded_external_execution')
        # Persisted original channel facts are fixtures; no new send is manufactured.
        operation=self.put('operation','old-operation',{'event_id':event['id'],'step_id':step_key(event),
            'stage':'unknown','dispatch_count':1,'send_capability':{'commitment_id':'historical-accepted-grant',
            'schedule_revision':1}})
        self.assertEqual(observe_commitment(self.app,'f',event)['execution_fact'],'original_receipt_uncertain')
        self.put('operation',operation['id'],{**operation,'stage':'sent'})
        self.assertEqual(observe_commitment(self.app,'f',event)['execution_fact'],'original_delivery_confirmed')
        Scheduler(self.app).tick();claim=JobQueue(self.app).claim('expiry')
        self.assertEqual(claim['job']['kind'],'review')
        _,binding=self.app.auth(JobQueue(self.app).session(claim),{'service'})
        self.assertTrue(binding['no_contact']);self.assertEqual(binding['reply_scope'],{})
        self.assertEqual(channel_for(self.app).count(),0)

    def test_expiry_service_report_then_progress_wait_preserves_original_grant(self):
        event=self.expired();grant=event['accepted_commitment'];Scheduler(self.app).tick()
        report_id='original-expiry-report'
        def service(job,tools):
            context=tools.call('context.read',{})['result']
            self.assertTrue(context['commitment_expiration_review']['grant_expired'])
            self.assertEqual(context['commitment_expiration_review']['valid_until'],grant['valid_until'])
            evidence_ref=context['commitment_expiration_review']['evidence_ref']
            denied=tools.call('wechat.send_message',self.send_args(event))
            self.assertEqual(denied['error']['code'],'NO_CONTACT_RUN')
            report=tools.call('progress.submit',{'request_id':'expiry-report','report_id':report_id,
                'event_ids':[event['id']],'facts':['Original accepted window elapsed; no execution recorded'],
                'remaining_work':['Original responsibility awaits feasible new authorization'],
                'evidence_refs':[evidence_ref]})
            self.assertTrue(report['ok'],report);saved=report['result']['data']
            self.assertEqual(saved['commitment_observation']['execution_fact'],'no_recorded_external_execution')
            result=tools.call('service.record_outcome',{'request_id':'expiry-outcome','outcome':'action_taken',
                'reason':'Original lateness facts reported internally','refs':[
                    {'kind':'event','id':event['id'],'version':event['version']},
                    {'kind':'report','id':saved['id'],'version':saved['version']}]})
            self.assertTrue(result['ok'],result)
            return {'outcome_id':job['run_id']}
        self.assertEqual(Worker(self.app,service).run_once()['outcome'],'succeeded')
        def progress(job,tools):
            context=tools.call('context.read',{})['result'];current=context['events'][0]
            self.assertEqual(context['reports'][0]['commitment_observation']['commitment_id'],grant['id'])
            result=tools.call('planner.commit_progress',{'request_id':'wait-new-source',
                'expected_family_version':context['family_version'],'report_id':report_id,'revision':1,
                'event_updates':[{'event_id':current['id'],'expected_version':current['version'],
                    'changes':{'status':'waiting','next_action':None,'waiting':{'reason':'Original authorization expired',
                        'resume_condition':'New authorized reschedule source','review_at':None}},
                    'evidence_refs':context['reports'][0]['payload']['evidence_refs']}]})
            self.assertTrue(result['ok'],result)
            return {}
        self.assertEqual(Worker(self.app,progress).run_once()['outcome'],'succeeded')
        current=self.app.event('f',event['id'])
        self.assertEqual(current['status'],'waiting');self.assertEqual(current['due_at'],event['due_at'])
        self.assertEqual(current['accepted_commitment'],grant)
        for _ in range(3):
            Scheduler(self.app).tick();self.assertFalse(Worker(self.app,lambda *_:self.fail('No repeated expiry model')).run_once()['ran'])
        self.assertEqual(channel_for(self.app).count(),0)

    def test_passive_and_daily_never_start_expiry_review(self):
        self.expired()
        for mode in ('passive','daily'):
            self.app.mode=mode
            self.assertEqual(Scheduler(self.app).tick()['created'],0)
            self.assertFalse(Worker(self.app,lambda *_:self.fail('Mode cannot execute')).run_once()['ran'])

    def test_host_expiration_evidence_is_stable_and_not_goal_completion(self):
        event=self.expired();Scheduler(self.app).tick()
        claim=JobQueue(self.app).claim('expiry');token=JobQueue(self.app).session(claim)
        from digital_tutor.context import build_context
        first=build_context(self.app,token,claim['job'])
        ref=first['commitment_expiration_review']['evidence_ref']
        version=self.app.store.family('f')['version']
        history=self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0]
        for _ in range(5):
            self.assertEqual(build_context(self.app,token,claim['job'])['commitment_expiration_review']['evidence_ref'],ref)
        self.assertEqual(self.app.store.family('f')['version'],version)
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0],history)
        self.assertFalse(self.app.completion_met('f',event,[ref]))

    def test_expiration_evidence_tampering_and_changed_grant_rejected(self):
        event=self.expired()
        with self.app.store.transaction():ref=ensure_expiration_evidence(self.app,'f',event)
        evidence=self.app.store.get('evidence',ref['id'],'f')
        changed={**evidence,'observation':{**evidence['observation'],'execution_fact':'original_delivery_confirmed'}}
        with self.assertRaises(BusinessError):validate_expiration_evidence(self.app,'f',changed)
        self.put('event',event['id'],{**event,'accepted_commitment':{
            **event['accepted_commitment'],'valid_until':'2026-10-05T17:00:00+08:00'}})
        with self.assertRaises(BusinessError):self.app.validate_refs('f',[ref])

    def test_changed_original_source_and_clock_rewind_reject_evidence(self):
        event=self.expired();message=self.interpret(self.inbound())
        event=self.put('event',event['id'],{**event,'accepted_commitment':{**event['accepted_commitment'],
            'source_message_ref':{'id':message['id'],'version':message['version']}}})
        with self.app.store.transaction():ref=ensure_expiration_evidence(self.app,'f',event)
        self.put('message',message['id'],{**message,'body':'Corrected original family instruction'})
        with self.assertRaises(BusinessError):self.app.validate_refs('f',[ref])
        self.put('message',message['id'],message)
        self.app.validate_refs('f',[ref])
        self.app.clock.fixed-=timedelta(hours=3)
        with self.assertRaises(BusinessError):self.app.validate_refs('f',[ref])

    def test_receipt_change_needs_fresh_report_but_preserves_historical_observation(self):
        event=self.expired()
        op=self.put('operation','prior-operation',{'event_id':event['id'],'step_id':step_key(event),
            'stage':'unknown','dispatch_count':1})
        with self.app.store.transaction():ref=ensure_expiration_evidence(self.app,'f',event)
        proof=self.app.store.get('evidence',ref['id'],'f')
        self.put('operation',op['id'],{**op,'stage':'sent'})
        with self.assertRaises(BusinessError):self.app.validate_refs('f',[ref])
        validate_expiration_evidence(self.app,'f',proof,historical_operations=True)
        with self.app.store.transaction():fresh=ensure_expiration_evidence(self.app,'f',event)
        self.assertNotEqual(fresh['id'],ref['id'])
        self.assertEqual(proof['observation']['execution_fact'],'original_receipt_uncertain')


for name in fixtures.IntradayRuntimeTests.__dict__:
    if name.startswith('test_') and name not in CommitmentExpirationTests.__dict__:
        setattr(CommitmentExpirationTests,name,None)

if __name__=='__main__':unittest.main()
