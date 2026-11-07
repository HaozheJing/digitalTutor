import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import execution_contract as contract, tool_result_contract as evidence
import test_service_workflow as base
import service_workflow as wf,sql_tools as s,agent_plan as ap,test_mode as tm


class ProjectionTests(unittest.TestCase):
    def test_provider_book_ids_are_not_used_as_knowledge_selectors(self):
        value={'facts':{'learning':{'profile':{'class_type':'L2'},'books':[{'id':'provider-book'}],
               'lessons':[{'lesson_no':19,'status':'completed'},{'lesson_no':999,'status':'unknown'}]}}}
        result=contract.context_projection(value)
        lessons=result['facts']['learning']['lessons']
        self.assertEqual(lessons[0]['curriculum_ref'],{'lesson_id':'L2-W04-D4','level':'L2','week':4,'day':4})
        self.assertIsNone(lessons[1]['curriculum_ref'])
        self.assertFalse(result['knowledge_reference_policy']['provider_book_id_is_curriculum_book_id'])
        self.assertNotIn('curriculum_ref',value['facts']['learning']['lessons'][0])

    def test_transport_states_do_not_invent_completion_or_retry(self):
        for rows,expected in [([],None),([{'status':'superseded'}],None),
            ([{'status':'sent'}],'done'),([{'status':'queued'},{'status':'sent'}],'awaiting_delivery'),
            ([{'status':'partial','source':'test_wechat_pending'}],'awaiting_delivery'),
            ([{'status':'partial','source':'receipt'}],'blocked'),([{'status':'unknown'}],'blocked')]:
            self.assertEqual(contract.delivery_task_status(rows),expected)

    def test_bound_receipt_distinguishes_transport_and_task(self):
        for state in ['queued','accepted','sent','partial','unknown']:
            value=evidence.annotate('workflow_fulfill',{}, {'status':'bound','delivery':state,'task_status':'awaiting_delivery'})
            labels=value['result_evidence']
            self.assertEqual(labels['operation']['state'],'bound')
            self.assertEqual(labels['delivery']['state'],state)
            self.assertEqual(labels['completion']['state'],'not_established')
            self.assertFalse(labels['delivery']['retry_without_verification'])

    def test_handoff_does_not_claim_resolution_or_resume(self):
        for state in ['pending','accepted','unknown','sent','dismissed']:
            value=contract.handoff_projection({'status':state,'delivery_status':state})
            self.assertEqual(value['handling_complete'],state in ('sent','dismissed'))
            self.assertEqual(value['business_issue_resolved'],'not_established')
            self.assertFalse(value['auto_resume_original'])

    def test_internal_review_is_not_delivery(self):
        result=evidence.annotate('service_case_review',{}, {'status':'reviewed','contacts_user':False})
        self.assertEqual(result['result_evidence']['operation']['scope'],'internal_case_review')
        self.assertEqual(result['result_evidence']['completion']['state'],'not_established')


class WorkflowContractTests(unittest.TestCase):
    setUp=base.WorkflowTests.setUp
    tearDown=base.WorkflowTests.tearDown
    create_source=base.WorkflowTests.create_source
    set_context=base.WorkflowTests.set_context
    begin_workflow=base.WorkflowTests.begin_workflow
    plan=base.WorkflowTests.plan
    stop_plan=base.WorkflowTests.stop_plan
    new_task=base.WorkflowTests.new_task
    start_action=base.WorkflowTests.start_action
    dialogue=base.WorkflowTests.dialogue

    def test_fulfill_retries_read_live_receipt_without_second_flush(self):
        tid=self.new_task();ctx=self.dialogue();task=ctx['tasks'][0]
        args=('stable-fulfillment',ctx['context_token'],tid,task['revision'],'这条回复完整履行原事项。','原话与当前事项一致')
        with patch.object(ap,'require_skills'),patch('wechat_test.flush') as flush:
            first=wf.fulfill(*args)
            self.assertEqual(first['delivery'],'queued')
            for state in ['accepted','unknown','sent']:
                with closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute('UPDATE deliveries SET status=?',(state,));wf.reconcile(c)
                result=wf.fulfill(*args)
                self.assertTrue(result['duplicate']);self.assertEqual(result['delivery'],state)
            self.assertEqual(flush.call_count,1)
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)
        self.assertEqual(result['task_status'],'done')

    def test_unknown_receipt_survives_restart_and_late_sent_completes_same_task(self):
        tid=self.new_task();self.start_action(tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            wf.bind_delivery(c,'original',tid)
            c.execute("INSERT INTO deliveries VALUES('original','内容','unknown',?,'receipt')",(s.now(),))
        wf.recover()
        with closing(sqlite3.connect(s.DB)) as c:
            view=wf.task_rows(c,tm.state())[0]['execution_state']
        self.assertEqual(view['waiting_for'],'delivery_verification');self.assertFalse(view['retry_delivery'])
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE deliveries SET status='sent'");wf.reconcile(c)
            view=wf.task_rows(c,tm.state())[0]['execution_state']
        self.assertTrue(view['completion_confirmed']);self.assertEqual(view['business_issue_resolved'],'not_established')
        self.assertEqual(len(s.read('SELECT * FROM service_task_versions')),1)

    def test_projection_is_readonly_and_respects_bound_run(self):
        tid=self.new_task()
        with closing(sqlite3.connect(s.DB)) as c,c:
            before=c.total_changes
            task=wf.task_rows(c,tm.state())[0]
            self.assertEqual(c.total_changes,before)
            self.assertEqual(task['execution_state']['wakeup']['kind'],'existing_task_due')
            self.assertFalse(wf.task_rows(c,{**tm.state(),'run_id':'other'}))
            c.execute("UPDATE service_tasks SET run_id='historical' WHERE id=?",(tid,))
            c.execute("INSERT INTO deliveries VALUES('old','内容','sent',?,'receipt')",(s.now(),))
            c.execute("INSERT INTO service_links VALUES(?,'old')",(tid,))
            wf.reconcile(c)
            self.assertEqual(c.execute('SELECT status FROM service_tasks WHERE id=?',(tid,)).fetchone()[0],'planned')

    def test_fulfill_rejects_internal_authority_before_outbox_mutation(self):
        tid=self.new_task();ctx=self.dialogue();task=ctx['tasks'][0]
        with self.assertRaisesRegex(ValueError,'internal_authority_in_body'):
            wf.fulfill('body-leak-test',ctx['context_token'],tid,task['revision'],'generation_token=synthetic-value','evidence')
        self.assertFalse(s.read('SELECT * FROM deliveries'))

    def test_pending_transport_finish_uses_same_projection_as_reconcile(self):
        tid=self.new_task();self.start_action(tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            wf.bind_delivery(c,'pending-parts',tid)
            c.execute("INSERT INTO deliveries VALUES('pending-parts','内容','partial',?,'test_wechat_pending')",(s.now(),))
        with patch.object(ap,'require_skills'),patch.object(ap,'require_queries'):
            result=ap.finish(tid,'completed','已提交，请以回执为准')
        self.assertEqual(result['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:wf.reconcile(c)
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(tid,))[0]['status'],'awaiting_delivery')

    def test_window_reschedule_is_atomic_and_preserves_task_identity(self):
        tid=self.new_task()
        # Explicit synthetic event-triggered task uses an existing Skill and
        # the existing window; no additional production trigger is enabled.
        import service_content
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET skill='prevent-lesson-skipping',kind='signal' WHERE id=?",(tid,))
            c.execute('UPDATE service_task_versions SET skill_version=? WHERE task_id=?',
                      (service_content.bundle_digest('prevent-lesson-skipping'),tid))
        ctx=self.dialogue()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE messages SET body='这次补读建议改到今天八点，其他安排不变' WHERE source_id='m-one'")
        ctx=wf.context();task=ctx['tasks'][0]
        args={'request_id':'reschedule-window','context_token':ctx['context_token'],
              'decisions':[{'task_id':tid,'revision':task['revision'],'decision':'reschedule',
                            'run_at':'2025-10-13T08:00:00+08:00','reason':'家长明确改时'}],
              'evidence':[{'source_message_id':'m-one','quote':'这次补读建议改到今天八点，其他安排不变'}]}
        self.assertEqual(wf.commit(**args)['status'],'committed')
        self.assertTrue(wf.commit(**args)['duplicate'])
        self.assertEqual(len(s.read('SELECT * FROM service_task_versions')),1)
        self.assertEqual(s.read('SELECT due_at FROM service_tasks WHERE id=?',(tid,))[0]['due_at'],'2025-10-13T08:00:00+08:00')

if __name__=='__main__':unittest.main()
