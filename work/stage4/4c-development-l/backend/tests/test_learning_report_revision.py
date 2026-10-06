"""Source -> obsolete report -> bounded Service revision -> original progress."""
import unittest

import test_learning_evidence_adapter as fixtures
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue
from digital_tutor.learning_review import report_review_allowed
from digital_tutor.scheduler import Worker, Scheduler
from digital_tutor.mock_channel import channel_for


class LearningReportRevisionTests(fixtures.LearningEvidenceTests):
    def stale_report(self):
        # Sync before historical Event creation avoids an unrelated initial review.
        self.source();source=self.sync()
        evidence=self.app.store.get('evidence',source['evidence_ids'][0],'f')
        eid=self.historical_event();rid=self.report(eid,evidence)
        original=self.app.store.get('report',self.app.report_key('f',rid,1),'f')
        self.source('batch-2',2,'incomplete','batch-1');self.sync('batch-2')
        return eid,rid,original,evidence

    def revision_args(self,job,original):
        return {'request_id':self.key(),'report_id':original['report_id'],
            'revision':original['revision']+1,'supersedes_revision':original['revision'],
            'event_ids':original['event_ids'],'facts':original['payload']['facts']+['Current source withdrew completion'],
            'remaining_work':['Original lesson remains unverified'],
            'evidence_refs':job['report_review_evidence_refs']}

    def test_real_worker_revision_unblocks_progress_without_forging_or_erasing_report(self):
        eid,rid,original,evidence=self.stale_report()
        observed=[]
        def service(job,tools):
            self.assertEqual(job['kind'],'review');observed.append(job['id'])
            _,binding=self.app.auth(tools.token,{'service'})
            self.assertTrue(binding['no_contact'])
            context=tools.call('context.read',{})['result']
            self.assertEqual(context['report_revision_review']['report_review_id'],rid)
            self.assertEqual(context['reports'][0]['observation']['facts'],original['payload']['facts'])
            refs={x['reference']['id'] for x in context['evidence']}
            self.assertTrue(all(r['id'] in refs for r in job['report_review_evidence_refs']))
            deny=tools.call('wechat.send_message',{'request_id':self.key(),
                'expected_family_version':context['family_version'],'event_id':eid,
                'action_kind':'remind_makeup','body':'Internal review cannot send'})
            self.assertFalse(deny['ok']);self.assertEqual(deny['error']['code'],'NO_CONTACT_RUN')
            result=tools.call('progress.submit',self.revision_args(job,original))
            self.assertTrue(result['ok'],result);report=result['result']['data']
            outcome=tools.call('service.record_outcome',{'request_id':self.key(),'outcome':'action_taken',
                'reason':'Persisted continuous revision from corrected source',
                'refs':[{'kind':'event','id':eid,'version':self.app.event('f',eid)['version']},
                        {'kind':'report','id':report['id'],'version':report['version']}]})
            self.assertTrue(outcome['ok'],outcome)
            return {'outcome_id':binding['run_id']}
        result=Worker(self.app,service).run_once()
        self.assertEqual(result['outcome'],'succeeded',result);self.assertEqual(len(observed),1)
        old=self.app.store.get('report',original['id'],'f')
        self.assertEqual(old['payload'],original['payload']);self.assertEqual(old['resolution'],'superseded')
        oldjob=self.app.store.get('job','progress:'+original['id'],'f')
        self.assertEqual(oldjob.get('attempt',0),0)
        def progress(job,tools):
            self.assertEqual(job['kind'],'progress')
            context=tools.call('context.read',{})['result'];event=context['events'][0]
            self.assertEqual(context['reports'][0]['revision'],2)
            result=tools.call('planner.commit_progress',{'request_id':self.key(),
                'expected_family_version':context['family_version'],'report_id':rid,'revision':2,
                'event_updates':[{'event_id':eid,'expected_version':event['version'],
                    'changes':{'status':'in_progress','next_action':{'action_kind':'verify_learning',
                        'target_refs':event['completion']['lesson_refs'],'purpose':'Verify original goal',
                        'execute_at':self.app.clock.iso()}},
                    'evidence_refs':context['reports'][0]['payload']['evidence_refs']}]})
            self.assertTrue(result['ok'],result)
            return {}
        self.assertEqual(Worker(self.app,progress).run_once()['outcome'],'succeeded')
        self.assertFalse(self.app.event('f',eid)['update_pending'])
        self.assertEqual(self.app.event('f',eid)['status'],'in_progress')
        self.assertEqual(channel_for(self.app).count(),0)
        self.assertEqual(len(self.app.store.list('event','f')),1)
        self.assertFalse(self.app.store.get('evidence',evidence['id'],'f')['valid'])

    def test_revision_scope_and_continuity_and_current_sources_are_enforced(self):
        _,_,original,_=self.stale_report()
        queue=JobQueue(self.app);claim=queue.claim('review');token=queue.session(claim)
        args=self.revision_args(claim['job'],original)
        for change,code in [({'report_id':'another'},'INVALID_REVISION'),
                            ({'revision':4,'supersedes_revision':3},'INVALID_REVISION'),
                            ({'event_ids':['different']},'PROGRESS_SCOPE_VIOLATION'),
                            ({'evidence_refs':original['payload']['evidence_refs']},'EVIDENCE_MISSING')]:
            with self.subTest(change=change):
                with self.assertRaises(BusinessError) as error:
                    self.app.submit_report(token,{**args,**change,'request_id':self.key()})
                self.assertEqual(error.exception.code,code)
        self.assertIsNone(self.app.store.get('report',original['id'],'f')['resolution'])

    def test_waiting_unconsumed_report_blocks_daily_without_calling_old_progress(self):
        _,_,original,_=self.stale_report()
        with self.app.store.transaction():
            plan=self.app.store.list('plan','f')[0]
            self.app.store.put('plan',plan['id'],'f',{**plan,'baseline_committed':False},plan['version'])
        Scheduler(self.app).tick()
        queue=JobQueue(self.app)
        # Verified source revision work now precedes baseline. Keep f leased
        # while claiming g to prove serialization is per family, not global.
        claim=queue.claim('review')
        self.assertEqual(claim['family_id'],'f')
        self.assertEqual(claim['job']['report_review_key'],original['id'])
        other=queue.claim('independent-family')
        self.assertEqual(other['family_id'],'g')
        self.assertEqual(other['job']['kind'],'daily')
        token=queue.session(other)
        self.app.commit_daily(token,{'request_id':self.key(),
            'expected_family_version':self.app.store.family('g')['version'],
            'business_date':self.app.clock.now().date().isoformat(),
            'new_events':[],'plan_refs':[],'pending_decisions':[],'candidate_decisions':[]})
        queue.finish(other)
        self.app.revoke_session(token)
        self.assertEqual(len(self.app.store.list('plan','g')),1)
        # This queue-level test leaves the original review waiting; the separate
        # real Worker test proves its successful revision/report/progress chain.
        queue.finish(claim,'waiting','TEST_REVIEW_WAIT')
        self.assertIsNone(queue.claim('blocked-family-scan'))
        jobs=self.app.store.list('job','f')
        self.assertTrue(any(j['kind']=='daily' and j['reason']=='REPORTS_PENDING' and j.get('attempt',0)==0 for j in jobs))
        self.assertEqual(self.app.store.get('job','progress:'+original['id'],'f').get('attempt',0),0)
        saved=self.app.store.get('report',original['id'],'f')
        self.assertEqual(saved['payload'],original['payload'])
        self.assertIsNone(saved['resolution'])
        before=(self.app.store.family('f')['version'],self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0])
        for _ in range(20):
            self.assertIsNone(queue.claim('unchanged-scan'))
        after=(self.app.store.family('f')['version'],self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0])
        self.assertEqual(after,before)
        self.assertEqual(self.app.store.get('job','progress:'+original['id'],'f').get('attempt',0),0)

    def test_new_source_supersedes_old_review_and_uses_latest_fact(self):
        _,_,original,_=self.stale_report()
        old=next(j for j in self.app.store.list('job','f') if j.get('report_review_key'))
        self.source('batch-3',3,'completed','batch-2');self.sync('batch-3')
        self.assertFalse(report_review_allowed(self.app,'f',old))
        queue=JobQueue(self.app);claim=queue.claim('latest-review')
        # A host completion review may first be skipped as obsolete because the
        # report is pending; it cannot replace the report revision authority.
        if not claim['job'].get('report_review_key'):
            queue.finish(claim,'succeeded');claim=queue.claim('latest-review')
        self.assertNotEqual(claim['job']['id'],old['id'])
        self.assertEqual(claim['job']['report_review_key'],original['id'])
        self.assertEqual(self.app.store.get('job',old['id'],'f')['status'],'superseded')
        self.assertTrue(all(self.app.store.get('evidence',r['id'],'f')['source_revision']==3
                            for r in claim['job']['report_review_evidence_refs']))

    def test_repeated_sync_is_stable_and_cannot_create_foreign_review(self):
        self.stale_report();before=self.app.store.family('f')['version']
        history=self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0]
        self.assertFalse(self.sync('batch-2')['changed'])
        self.assertEqual(self.app.store.family('f')['version'],before)
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0],history)
        self.assertEqual(self.app.store.list('job','g'),[])


for name in fixtures.LearningEvidenceTests.__dict__:
    if name.startswith('test_') and name not in LearningReportRevisionTests.__dict__:
        setattr(LearningReportRevisionTests,name,None)

if __name__=='__main__':unittest.main()
