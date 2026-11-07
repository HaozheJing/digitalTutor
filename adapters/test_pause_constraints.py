import unittest,sqlite3,json
from contextlib import closing
import test_service_workflow as base
import service_workflow as wf,sql_tools as s,test_mode as tm,agent_plan as ap

class PauseTests(unittest.TestCase):
    setUp=base.WorkflowTests.setUp
    tearDown=base.WorkflowTests.tearDown
    set_context=base.WorkflowTests.set_context
    create_source=base.WorkflowTests.create_source
    begin_workflow=base.WorkflowTests.begin_workflow
    stop_plan=base.WorkflowTests.stop_plan
    dialogue=base.WorkflowTests.dialogue
    new_task=base.WorkflowTests.new_task
    plan=base.WorkflowTests.plan
    start_action=base.WorkflowTests.start_action
    def empty(self,at="2025-11-07T06:30:00+08:00"):
        task,data=self.begin_workflow(at);self.stop_plan(task['id'])
        ctx=self.dialogue();self.assertEqual(ctx['tasks'],[])
        return task,ctx
    def save(self,ctx,scope=None,**extra):
        return wf.commit('pause-empty-ledger',ctx['context_token'],[],[{'source_message_id':'m-one','quote':'今天阅读提醒不用发了'}],{'scope':scope or {'kind':'skill','skill':'remind-reading'},'period':'today',**extra})
    def blocked(self,skill='remind-reading',task_id=None,at=None,state=None):
        with closing(sqlite3.connect(s.DB)) as c:return wf.constraint_blocks(c,state or tm.state(),skill,task_id,at)
    def resume_plan(self,task):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE events SET status='done'")
            c.execute("UPDATE service_tasks SET status='running' WHERE id=?",(task['id'],))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(task['id']),))
        return wf.context()
    def test_empty_ledger_persists_receipt_and_retry_and_reopen(self):
        _,ctx=self.empty();receipt=self.save(ctx)
        self.assertEqual(receipt['status'],'committed');self.assertEqual(receipt['decisions'],[])
        saved=receipt['contact_constraint'];self.assertEqual(saved['scope'],{'kind':'skill','skill':'remind-reading'})
        self.assertEqual(saved['starts_at'],'2025-11-07T06:30:00+08:00')
        self.assertEqual(saved['ends_at'],'2025-11-07T23:59:59+08:00')
        self.assertEqual(saved['applies_to'],'existing_and_future_tasks')
        self.assertTrue(self.save(ctx)['duplicate']);wf.initialize()
        self.assertEqual(len(s.read('SELECT * FROM service_workflow_constraints')),1)
        self.assertEqual(s.read('SELECT * FROM service_task_versions'),[])
        self.assertTrue(self.blocked());self.assertFalse(self.blocked('invite-reading-interaction'))
    def test_precise_kind_scope_isolation_and_beijing_expiry(self):
        _,ctx=self.empty();self.save(ctx)
        for name in ['invite-reading-interaction','plan-weekend-makeup','celebrate-lesson-completion','answer-faq']:
            self.assertFalse(self.blocked(name),name)
        self.assertTrue(self.blocked(at='2025-11-07T15:59:59+00:00'))
        self.assertFalse(self.blocked(at='2025-11-07T16:00:00+00:00'))
        self.assertFalse(self.blocked(at='2025-11-07T05:59:59+08:00'))
        for field in ['uid','run_id']:self.assertFalse(self.blocked(state={**tm.state(),field:'other'}))
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('DELETE FROM service_workflow_constraints')
            c.execute('INSERT INTO service_workflow_constraints VALUES(?,?,?,?,?,?,?)',('one',tm.state()['run_id'],tm.state()['uid'],json.dumps({'kind':'task','task_id':'owned-task'}),'2025-11-07T00:00:00+08:00','2025-11-07T23:59:59+08:00','[]'))
        self.assertFalse(self.blocked());self.assertFalse(self.blocked(task_id='another'))
        self.assertTrue(self.blocked(task_id='owned-task'))
    def test_registry_is_independent_of_today_catalog_and_rejects_unsafe_scopes(self):
        _,ctx=self.empty()
        self.assertIn('weekly-learning-review',{x['skill'] for x in ctx['pause_targets']})
        for scope in [{'kind':'all'},{'kind':'skill','skill':'invented'},{'kind':'skill','skill':'plan-service-day'},{'kind':'skill','skill':'remind-reading','task_id':'other'},{'kind':'task','task_id':'foreign'}]:
            with self.subTest(scope=scope),self.assertRaises(ValueError):self.save(ctx,scope)
        self.assertEqual(s.read('SELECT * FROM service_workflow_constraints'),[])
        with self.assertRaisesRegex(ValueError,'宿主确定'):self.save(ctx,ends_at='2026-01-01T00:00:00+08:00')
        with self.assertRaisesRegex(ValueError,'没有待提交'):wf.commit('empty-noop-001',ctx['context_token'],[],[{'source_message_id':'m-one','quote':'今天阅读提醒不用发了'}])
    def test_future_planning_skips_only_paused_category_and_can_create_others(self):
        task,ctx=self.empty("2025-11-07T12:00:00+08:00");self.save(ctx);data=self.resume_plan(task)
        reading=next(x for x in data['catalog'] if x['skill']=='remind-reading')
        self.assertEqual(reading['opportunities'][0]['status'],'not_eligible')
        self.assertTrue(reading['opportunities'][0]['pause_blocked'])
        with closing(sqlite3.connect(s.DB)) as c,c:
            with self.assertRaisesRegex(ValueError,'暂停范围'):
                wf.add(c,tm.state(),reading['opportunities'][0],reading,reading['timing']['at'],'late fixed task',{},'test')
        choices=[{'skill':x['skill'],'decision':'skip','reason':'test'} for x in data['catalog']]
        for name in ['invite-reading-interaction','plan-weekend-makeup']:
            con=next(x for x in data['catalog'] if x['skill']==name)
            self.assertEqual(con['opportunities'][0]['status'],'eligible')
            index=next(i for i,x in enumerate(choices) if x['skill']==name)
            choices[index].update(decision='create',opportunity_id=con['opportunities'][0]['id'],run_at=con['timing']['at'])
        forced=[*choices,{'skill':'remind-reading','decision':'create','reason':'test','opportunity_id':reading['opportunities'][0]['id'],'run_at':reading['timing']['at']}]
        with self.assertRaises(ValueError):wf.commit('paused-forced-create',data['context_token'],forced)
        self.assertEqual(s.read('SELECT * FROM service_task_versions'),[])
        wf.commit('other-services-create',data['context_token'],choices)
        self.assertEqual({x['skill'] for x in s.read("SELECT skill FROM service_tasks WHERE kind<>'plan'")},{'invite-reading-interaction','plan-weekend-makeup'})
    def test_existing_task_and_transport_revalidation_respect_independent_pause(self):
        tid=self.new_task();ctx=self.dialogue();before=s.read('SELECT id,status FROM service_tasks')
        self.save(ctx);self.assertEqual(s.read('SELECT id,status FROM service_tasks'),before)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE events SET status='done'")
            c.execute("UPDATE service_tasks SET status='running' WHERE id=?",(tid,))
        wf.prepare_execution(tid)
        with closing(sqlite3.connect(s.DB)) as c:
            with self.assertRaisesRegex(ValueError,'暂停范围'):wf.validate_execution(c,tid)
        self.assertEqual(s.read('SELECT * FROM service_links'),[])
    def test_stale_transaction_and_invalid_evidence_cannot_save_pause(self):
        _,ctx=self.empty()
        with self.assertRaisesRegex(ValueError,'原话不匹配'):
            wf.commit('forged-pause-01',ctx['context_token'],[],[{'source_message_id':'m-one','quote':'暂停全部'}],{'scope':{'kind':'skill','skill':'remind-reading'},'period':'today'})
        self.save(ctx)
        with self.assertRaisesRegex(ValueError,'已变化'):
            wf.commit('stale-pause-001',ctx['context_token'],[],[{'source_message_id':'m-one','quote':'今天阅读提醒不用发了'}],{'scope':{'kind':'skill','skill':'remind-reading'},'period':'today'})
        self.assertEqual(len(s.read('SELECT * FROM service_workflow_constraints')),1)

    def test_host_weekly_creation_after_fixed_time_still_honors_new_pause(self):
        task,ctx=self.empty('2025-11-08T10:00:00+08:00')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE messages SET body='今天周总结先不发，其他照常' WHERE source_id='m-one'")
        ctx=wf.context()
        wf.commit('pause-weekly-late',ctx['context_token'],[],[{'source_message_id':'m-one','quote':'今天周总结先不发，其他照常'}],{'scope':{'kind':'skill','skill':'weekly-learning-review'},'period':'today'})
        import s10_service
        with closing(sqlite3.connect(s.DB)) as c,c:
            self.assertIsNone(s10_service.ensure_fixed(c,{**tm.state(),'auto_highlights':True}))
        self.assertFalse(s.read('SELECT * FROM service_task_versions'))
