"""Program contract tests; source facts are raw SQL rows, never add_evidence fixtures."""
from pathlib import Path
import json,tempfile,unittest
from digital_tutor.application import Application,Clock
from digital_tutor.course_binding import confirm_course_binding
from digital_tutor.context import build_context
from digital_tutor.domain import BusinessError
from digital_tutor.learning_evidence import sync_learning_evidence
from digital_tutor.storage import encode
from digital_tutor.scheduler import Worker
from digital_tutor.jobs import JobQueue
from digital_tutor.candidates import makeup_identity


class LearningEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=Application(Path(self.tmp.name)/'test.sqlite',
            Clock('2026-10-04T12:00:00+08:00'),strict_runtime=True)
        self.app.create_family('f','synthetic-f','mock:f');self.app.create_family('g','synthetic-g','mock:g')
        self.admin=self.app.open_session('f','admin');self.service=self.app.open_session('f','service');self.seq=0

    def tearDown(self):self.app.close();self.tmp.cleanup()
    def key(self):self.seq+=1;return 'test-'+str(self.seq)
    def ref(self,record):return {'kind':'evidence','id':record['id'],'version':record['version']}

    def source(self,batch='batch-1',revision=1,status='completed',supersedes=None,coverage=True,bind=True,family='f'):
        at=f'2026-10-04T{7+revision:02d}:00:00+08:00'
        meta={'synthetic':True,'batch_id':batch,'imported_at':at,'coverage_through':at if coverage else None,
            'source_timezone':'Asia/Shanghai','source_contract':{'kind':'versioned_learning_snapshot',
            'source_id':'synthetic-progress','revision':revision,'supersedes_batch_id':supersedes,'observed_at':at}}
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO imports VALUES(?,?)',(batch,encode(meta)))
            db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (batch,'synthetic-'+family,1,'attended',status,'unknown','unknown','unknown',None,None,batch+'!row-2'))
        if bind:
            token=self.admin if family=='f' else self.app.open_session(family,'admin')
            confirm_course_binding(self.app,token,{'request_id':self.key(),'batch_id':batch,
                'expected_family_version':self.app.store.family(family)['version'],'source_note':'Synthetic authoritative binding',
                'course_id':'synthetic-course','course_version':'v1','lesson_map':[{'day':1,'lesson_ref':'synthetic-lesson-1'}],
                'coverage_through':at,'source_timezone':'Asia/Shanghai'})
        return batch

    def sync(self,batch='batch-1'):return sync_learning_evidence(self.app,self.admin,batch,1)

    def historical_event(self,goal='goal-original'):
        # Explicit historical fixture only; the adapter never creates an Event.
        self.app.strict_runtime=False
        try:
            token=self.app.open_session('f','planner_daily')
            result=self.app.commit_daily(token,{'request_id':self.key(),'business_date':'2026-10-04',
                'expected_family_version':self.app.store.family('f')['version'],
                'new_events':[{'client_ref':'a','goal_key':goal,'goal_text':'Original synthetic lesson goal',
                    'completion':{'kind':'goal','lesson_refs':['synthetic-lesson-1']}}],'plan_refs':['a']})
            eid=result['data']['event_ids']['a']
            # The historical obligation is for the same synthetic curriculum
            # declared by source(); a bare reused lesson label is not identity.
            with self.app.store.transaction():
                event=self.app.event('f',eid)
                event['source_identity']=makeup_identity('f','synthetic-course','v1',['synthetic-lesson-1'])
                self.app.store.put('event',eid,'f',event,event['version'])
            return eid
        finally:self.app.strict_runtime=True

    def report(self,event_id,evidence):
        report_id=self.key()
        self.app.submit_report(self.service,{'request_id':self.key(),'report_id':report_id,
            'event_ids':[event_id],'facts':['Source record observed'],'evidence_refs':[self.ref(evidence)]})
        return report_id

    def progress_worker(self,status):
        def handler(job,tools):
            self.assertEqual(job['kind'],'progress')
            context=tools.call('context.read',{})['result'];event=context['events'][0]
            refs=context['reports'][0]['payload']['evidence_refs']
            changes={'status':status}
            if status=='in_progress':
                # A reopened selected responsibility needs its own new step;
                # do not pretend the completed Event kept an old executable step.
                changes['next_action']={'action_kind':'verify_learning',
                    'target_refs':event['completion']['lesson_refs'],
                    'purpose':'Verify the original lesson after its source correction.',
                    'execute_at':self.app.clock.iso()}
            result=tools.call('planner.commit_progress',{'request_id':self.key(),
                'expected_family_version':context['family_version'],'report_id':context['reports'][0]['report_id'],
                'revision':1,'event_updates':[{'event_id':event['id'],'expected_version':event['version'],
                    'changes':changes,'evidence_refs':refs}]})
            self.assertTrue(result['ok'],result)
            # Strict Worker still owns a live lease after transactional progress commit.
            self.assertEqual(self.app.store.get('job',job['id'],'f')['status'],'leased')
            return {}
        result=Worker(self.app,handler,lease_seconds=3).run_once()
        self.assertEqual(result['outcome'],'succeeded',result)

    def completed_original(self):
        self.source();synced=self.sync();evidence=self.app.store.get('evidence',synced['evidence_ids'][0],'f')
        event=self.historical_event();self.report(event,evidence);self.progress_worker('done')
        return event,evidence

    def test_raw_completed_row_creates_referencable_stable_evidence(self):
        self.source();result=self.sync();e=self.app.store.get('evidence',result['evidence_ids'][0],'f')
        self.assertTrue(e['trusted'] and e['valid']);self.assertEqual(e['lesson_ref'],'synthetic-lesson-1')
        self.assertEqual(e['source_locator'],'batch-1!row-2');self.assertEqual(e['source_revision'],1)
        eid=self.historical_event()
        context=build_context(self.app,self.service,{'event_id':eid})
        self.assertIn(self.ref(e),[row['reference'] for row in context['evidence']])

    def test_repeat_sync_no_family_version_or_record_changes(self):
        self.source();first=self.sync();version=self.app.store.family('f')['version']
        history=self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0]
        again=self.sync();self.assertFalse(again['changed']);self.assertEqual(first['evidence_ids'],again['evidence_ids'])
        self.assertEqual(version,self.app.store.family('f')['version'])
        self.assertEqual(history,self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0])

    def test_attendance_does_not_prove_completion(self):
        self.source(status='unknown');result=self.sync();self.assertEqual(result['evidence_ids'],[])
        self.assertEqual(self.app.store.list('evidence','f'),[])

    def test_no_coverage_rejected_without_any_evidence(self):
        self.source(coverage=False)
        with self.assertRaises(BusinessError) as caught:self.sync()
        self.assertEqual(caught.exception.code,'COVERAGE_MISSING');self.assertEqual(self.app.store.list('evidence','f'),[])

    def test_missing_binding_rejected(self):
        self.source(bind=False)
        with self.assertRaises(BusinessError) as caught:self.sync()
        self.assertEqual(caught.exception.code,'COURSE_BINDING_MISSING')

    def test_cross_family_source_rejected(self):
        self.source(family='g')
        with self.assertRaises(BusinessError) as caught:self.sync()
        self.assertEqual(caught.exception.code,'LEARNING_SOURCE_MISSING')
        self.assertEqual(self.app.store.list('evidence','f'),[])

    def test_service_cannot_call_host_adapter(self):
        self.source()
        with self.assertRaises(BusinessError) as caught:sync_learning_evidence(self.app,self.service,'batch-1',1)
        self.assertEqual(caught.exception.code,'ROLE_FORBIDDEN')

    def test_unversioned_raw_mutation_cannot_replace_evidence(self):
        self.source();self.sync();eid=self.historical_event()
        self.app.store.connection.execute("UPDATE learning_days SET completion_status='incomplete'")
        with self.assertRaises(BusinessError) as caught:self.sync()
        self.assertEqual(caught.exception.code,'SOURCE_REVISION_CONFLICT')
        evidence=self.app.store.list('evidence','f')[0]
        with self.assertRaises(BusinessError):self.app.validate_refs('f',[self.ref(evidence)])
        context=build_context(self.app,self.service,{'event_id':eid})
        self.assertFalse(context['evidence'][0]['source_current'])
        self.assertFalse(context['evidence'][0]['usable_for_completion'])

    def test_bad_lineage_cannot_invalidate_original(self):
        self.source();first=self.sync();self.source('batch-2',2,'incomplete','unrelated')
        with self.assertRaises(BusinessError) as caught:self.sync('batch-2')
        self.assertEqual(caught.exception.code,'SOURCE_LINEAGE_CONFLICT')
        self.assertTrue(self.app.store.get('evidence',first['evidence_ids'][0],'f')['valid'])

    def test_completion_then_correction_review_report_progress_reopens_original(self):
        eid,old=self.completed_original();self.source('batch-2',2,'incomplete','batch-1');result=self.sync('batch-2')
        self.assertFalse(self.app.store.get('evidence',old['id'],'f')['valid'])
        self.assertEqual(self.app.event('f',eid)['status'],'done')
        correction=self.app.store.get('evidence',result['correction_ids'][0],'f')
        self.assertEqual(correction['event_id'],eid)
        observed=[]
        def handler(job,tools):
            self.assertEqual(job['kind'],'review')
            family,binding=self.app.auth(tools.token,{'service'})
            self.assertTrue(binding['no_contact']);self.assertEqual(job['event_id'],eid)
            denied=tools.call('wechat.send_message',{'request_id':self.key(),
                'expected_family_version':self.app.store.family('f')['version'],
                'event_id':eid,'body':'Review is internal','action_kind':'remind_makeup','part_id':'review'})
            self.assertFalse(denied['ok'],denied)
            self.assertEqual(denied['error']['code'],'NO_CONTACT_RUN')
            context=tools.call('context.read',{'event_id':eid})['result']
            self.assertIn(self.ref(correction),[row['reference'] for row in context['evidence']])
            event=context['events'][0]
            report=tools.call('progress.submit',{'request_id':self.key(),'report_id':'corrected-original','event_ids':[eid],
                'facts':['New source explicitly withdraws prior completion'],'remaining_work':['Original lesson'],
                'evidence_refs':[self.ref(correction)]})
            self.assertTrue(report['ok'],report);saved=report['result']['data']
            outcome=tools.call('service.record_outcome',{'request_id':self.key(),'outcome':'action_taken',
                'reason':'Original source review report persisted without contact',
                'refs':[{'kind':'event','id':event['id'],'version':event['version']},
                        {'kind':'report','id':saved['id'],'version':saved['version']}]})
            self.assertTrue(outcome['ok'],outcome);observed.append(job['id'])
            return {'outcome_id':binding['run_id']}
        run=Worker(self.app,handler,lease_seconds=3).run_once();self.assertEqual(run['outcome'],'succeeded',run)
        self.progress_worker('in_progress')
        self.assertEqual(self.app.event('f',eid)['status'],'in_progress')
        self.assertEqual(self.app.event('f',eid)['remaining_work'],['synthetic-lesson-1'])
        self.assertEqual(len(self.app.store.list('event','f')),1);self.assertEqual(self.app.store.list('operation','f'),[])
        self.assertFalse(Worker(self.app,handler).run_once()['ran']);self.assertEqual(len(observed),1)
        version=self.app.store.family('f')['version'];self.assertFalse(self.sync('batch-2')['changed'])
        self.assertEqual(version,self.app.store.family('f')['version'])

    def test_ordinary_done_review_is_not_allowed(self):
        eid,_=self.completed_original()
        with self.app.store.transaction():JobQueue(self.app).enqueue('f','ordinary-review',{'kind':'review','event_id':eid})
        calls=[];result=Worker(self.app,lambda *_:calls.append(True)).run_once()
        self.assertEqual(result['outcome'],'obsolete_step');self.assertEqual(calls,[])

    def test_no_done_reference_means_no_correction_review(self):
        self.source();self.sync();self.historical_event()
        self.source('batch-2',2,'incomplete','batch-1');result=self.sync('batch-2')
        self.assertEqual(result['correction_ids'],[])
        self.assertEqual([j for j in self.app.store.list('job','f') if j['kind']=='review'],[])

    def test_review_cannot_report_for_another_event(self):
        eid,_=self.completed_original();self.source('batch-2',2,'incomplete','batch-1');result=self.sync('batch-2')
        correction=self.app.store.get('evidence',result['correction_ids'][0],'f')
        # A real unrelated same-family Event tests scope, not missing-record lookup.
        with self.app.store.transaction():
            unrelated=dict(self.app.event('f',eid))
            unrelated.pop('id',None);unrelated.pop('version',None)
            self.app.store.put('event','other-event','f',unrelated)
        claim=JobQueue(self.app).claim('test');token=JobQueue(self.app).session(claim)
        self.assertEqual(claim['job'].get('correction_evidence_id'),correction['id'])
        with self.assertRaises(BusinessError) as caught:
            self.app.submit_report(token,{'request_id':self.key(),'report_id':'bad','event_ids':['other-event'],
                'facts':['Wrong target'],'evidence_refs':[self.ref(correction)]})
        self.assertEqual(caught.exception.code,'PROGRESS_SCOPE_VIOLATION')

    def test_completion_correction_cannot_fake_withdrawal(self):
        eid,_=self.completed_original();self.source('batch-2',2,'completed','batch-1');result=self.sync('batch-2')
        correction=self.app.store.get('evidence',result['correction_ids'][0],'f')
        rid=self.report(eid,correction);token=self.app.open_session('f','planner_progress',rid,1)
        with self.assertRaises(BusinessError) as caught:
            self.app.commit_progress(token,{'request_id':self.key(),'expected_family_version':self.app.store.family('f')['version'],
                'report_id':rid,'revision':1,'event_updates':[{'event_id':eid,'expected_version':self.app.event('f',eid)['version'],
                    'changes':{'status':'in_progress'},'evidence_refs':[self.ref(correction)]}]})
        self.assertEqual(caught.exception.code,'COMPLETION_STILL_MET')
        self.assertEqual(self.app.event('f',eid)['status'],'done')
        self.assertIsNone(self.app.store.get('report',self.app.report_key('f',rid,1),'f')['resolution'])

    def test_low_binding_coverage_rejected(self):
        self.source();binding=self.app.store.list('course_binding','f')[0]
        with self.app.store.transaction():
            binding['coverage_through']='2026-10-04T07:00:00+08:00'
            self.app.store.put('course_binding',binding['id'],'f',binding,binding['version'])
        with self.assertRaises(BusinessError) as caught:self.sync()
        self.assertEqual(caught.exception.code,'COVERAGE_INCOMPLETE')

    def test_real_or_unconfirmed_import_not_accepted_by_simulation_adapter(self):
        self.source()
        db=self.app.store.connection;meta=json.loads(db.execute('SELECT metadata FROM imports').fetchone()[0])
        meta['synthetic']=False;db.execute('UPDATE imports SET metadata=?',(encode(meta),))
        with self.assertRaises(BusinessError) as caught:self.sync()
        self.assertEqual(caught.exception.code,'SOURCE_CONTRACT_MISSING')

    def test_non_authoritative_correction_cannot_unlock_done_review(self):
        eid,_=self.completed_original()
        with self.app.store.transaction():
            JobQueue(self.app).enqueue('f','forged-review',{'kind':'review','event_id':eid,
                'correction_evidence_id':'nonexistent','correction_evidence_version':1})
        calls=[];result=Worker(self.app,lambda *_:calls.append(True)).run_once()
        self.assertFalse(result['ran']);self.assertEqual(calls,[])
        self.assertEqual(self.app.store.get('job','forged-review','f')['status'],'superseded')

    def test_new_completion_wakes_existing_original_event_for_internal_review(self):
        eid=self.historical_event();self.source();synced=self.sync()
        reviews=[j for j in self.app.store.list('job','f') if j['kind']=='review']
        self.assertEqual(len(reviews),1);self.assertEqual(reviews[0]['event_id'],eid)
        self.assertEqual(reviews[0]['learning_evidence_id'],synced['evidence_ids'][0])
        self.assertFalse(reviews[0]['contact_allowed'])

    def test_consecutive_correction_updates_fact_without_waiting_for_original_review(self):
        self.completed_original();self.source('batch-2',2,'incomplete','batch-1');self.sync('batch-2')
        self.source('batch-3',3,'completed','batch-2');version=self.app.store.family('f')['version']
        result=self.sync('batch-3')
        self.assertTrue(result['changed']);self.assertGreater(self.app.store.family('f')['version'],version)
        self.assertEqual(result['source']['source_revision'],3)
        self.assertTrue(result['correction_ids'])
        from digital_tutor.learning_evidence import correction_review_allowed
        older=[j for j in self.app.store.list('job','f') if j.get('correction_evidence_id')
               and j['correction_evidence_id'] not in result['correction_ids']]
        self.assertTrue(older)
        self.assertTrue(all(not correction_review_allowed(self.app,'f',j,self.app.event('f',j['event_id'])) for j in older))

    def test_correction_review_is_deferred_not_discarded_by_existing_report(self):
        eid,old=self.completed_original();self.report(eid,old)
        self.source('batch-2',2,'incomplete','batch-1');self.sync('batch-2')
        claim=JobQueue(self.app).claim('report-revision')
        self.assertTrue(claim['job'].get('report_review_key'))
        review=next(j for j in self.app.store.list('job','f') if j.get('correction_evidence_id'))
        self.assertEqual(review['status'],'waiting');self.assertEqual(review['reason'],'REPORTS_PENDING')
        progress=next(j for j in self.app.store.list('job','f') if j['kind']=='progress' and j['status']=='waiting')
        self.assertEqual(progress['reason'],'REPORT_REVISION_REQUIRED')
        self.assertEqual(progress.get('attempt',0),0)
        self.assertTrue(any(j.get('report_review_key') for j in self.app.store.list('job','f')))
