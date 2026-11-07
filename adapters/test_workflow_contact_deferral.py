import json,sqlite3,unittest
from contextlib import closing
import test_service_workflow as base
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf

class ContactDeferralTests(unittest.TestCase):
    setUp=base.WorkflowTests.setUp
    tearDown=base.WorkflowTests.tearDown
    create_source=base.WorkflowTests.create_source
    set_context=base.WorkflowTests.set_context
    begin_workflow=base.WorkflowTests.begin_workflow
    plan=base.WorkflowTests.plan
    stop_plan=base.WorkflowTests.stop_plan

    def delayed_highlight(self):
        plan,ctx=self.begin_workflow('2025-11-07 20:30')
        con=next(x for x in ctx['catalog'] if x['kind']=='highlight' and x['opportunities'][0]['status']=='eligible')
        item=con['opportunities'][0];choices=self.plan(ctx)
        choices.append({'skill':con['skill'],'decision':'create','reason':'已有高光','opportunity_id':item['id'],'run_at':ctx['business_time']})
        tid=wf.commit('contact-deferral',ctx['context_token'],choices)['decisions'][-1]['task_id'];self.stop_plan(plan['id'])
        state=tm.state();state.update(auto_highlights=True,manual_service_task_id=tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        self.assertEqual(ap.claim()['id'],tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('prior-1','earlier service','sent','2025-11-07T20:00:00+08:00','test_wechat'))
        wf.prepare_execution(tid)
        self.assertEqual(wf.highlight()['status'],'deferred')
        return tid

    def assert_deferred(self,tid):
        task=s.read('SELECT * FROM service_tasks WHERE id=?',(tid,))[0]
        self.assertEqual(task['status'],'deferred');self.assertEqual(task['due_at'],'2025-11-07T21:00:00+08:00')
        self.assertEqual(json.loads(task['result'])['original_due_at'],'2025-11-07T20:30:00+08:00')
        self.assertEqual(task['attempts'],0);self.assertIsNone(task['finished_at'])
        self.assertIsNone(s.read('SELECT execution_token FROM service_task_versions WHERE task_id=?',(tid,))[0]['execution_token'])
        self.assertFalse(s.read('SELECT * FROM service_links'));self.assertFalse(s.read('SELECT * FROM test_outbox'))
        other=ap.claim()
        if other:
            self.assertNotEqual(other['id'],tid)
            self.stop_plan(other['id'])

    def test_gate_deferral_survives_model_finish_and_reclaims_same_task(self):
        tid=self.delayed_highlight();self.assertEqual(ap.settle(tid)['status'],'deferred');self.assert_deferred(tid)
        self.assertEqual(ap.settle(tid)['status'],'already_finished')
        tm.advance('2025-11-07 21:00')
        for _ in range(12):
            claimed=ap.claim();self.assertIsNotNone(claimed)
            if claimed['id']==tid:break
            self.stop_plan(claimed['id'])
        self.assertEqual(claimed['id'],tid);self.assertTrue(wf.prepare_execution(tid)['execution_token'])
        self.assertEqual(len(s.read('SELECT * FROM service_task_versions WHERE task_id=?',(tid,))),1)

    def test_restart_preserves_contact_retry_without_replay(self):
        tid=self.delayed_highlight();wf.recover();self.assert_deferred(tid)

    def test_other_candidate_pending_does_not_excuse_missing_finish(self):
        tid=self.delayed_highlight()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('UPDATE proactive_pending SET candidate_ids=?',(s.encoded(['different-candidate']),))
        self.assertEqual(ap.settle(tid)['status'],'blocked')
        self.assertEqual(json.loads(s.read('SELECT result FROM service_tasks WHERE id=?',(tid,))[0]['result'])['code'],'missing_finish_receipt')

if __name__=='__main__':unittest.main()
