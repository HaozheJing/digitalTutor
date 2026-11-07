"""Deterministic trigger/host tests; no claim of native-model E2E coverage."""
import sys
from pathlib import Path
if Path('/home/ubuntu/digitalTutor-fastloop-dev-20261107/adapters').is_dir():sys.path.insert(0,'/home/ubuntu/digitalTutor-fastloop-dev-20261107/adapters')
import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_service_workflow as base
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf,fixture_query as fq
import completion_events as ce,weekly_interaction as wi,proactive as p

class ActiveTriggerTests(unittest.TestCase):
    setUp=base.WorkflowTests.setUp
    tearDown=base.WorkflowTests.tearDown
    create_source=base.WorkflowTests.create_source
    set_context=base.WorkflowTests.set_context
    def configure(self,at,automatic=True):
        with patch.object(wf,'default_mode',return_value=True):tm.activate(at,automatic)
        ap.enable();wf.initialize()
    def clear_week(self):
        data=json.loads(fq.FILE.read_text())
        data['highlight_assets']=[]
        for l in data['learning_daily']:
            if '2025-11-03'<=l['date']<='2025-11-07':
                for key in ('completed_at','attended_at','checked_in_at','quiz_submitted_at'):l[key]=None
        data['learning_events']=[e for e in data['learning_events'] if not '2025-11-03'<=e['occurred_at'][:10]<='2025-11-07']
        fq.FILE.write_text(json.dumps(data))
    def finish_empty_morning(self):
        plan=ap.claim();self.assertEqual(plan['kind'],'plan');ctx=wf.context()
        wf.commit('test-morning',ctx['context_token'],[{'skill':e['skill'],'decision':'skip','reason':'deterministic scenario: no current applicable service'} for e in ctx['catalog']])
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='done' WHERE id=?",(plan['id'],))
            c.execute("DELETE FROM metadata WHERE key='active_service_task'")
    def interaction_tasks(self):return s.read('SELECT * FROM service_tasks WHERE skill=?',(wi.RULE,))
    def learn(self):
        return ce.simulate({'run_id':tm.state()['run_id'],'lesson_id':'L2-020','event_id':'synthetic-friday-completion','completed_at':'2025-11-07T17:00:00+08:00'})
    def test_before_fixed_wake_no_claim_or_send(self):
        self.configure('2025-11-07T06:29:00+08:00')
        self.assertIsNone(ap.claim());self.assertFalse(s.read('SELECT * FROM deliveries'))
        tm.advance('2025-11-07T06:30:00+08:00');self.assertEqual(ap.claim()['kind'],'plan')
    def test_weekly_new_learning_after_empty_morning_creates_due_check(self):
        self.clear_week();self.configure('2025-11-07T06:30:00+08:00');self.finish_empty_morning()
        self.assertFalse(self.interaction_tasks());tm.advance('2025-11-07T17:00:00+08:00');self.assertEqual(self.learn()['status'],'recorded')
        tm.advance('2025-11-07T18:59:00+08:00');self.assertFalse(self.interaction_tasks())
        tm.advance('2025-11-07T19:00:00+08:00')
        tasks=self.interaction_tasks();self.assertEqual(len(tasks),1)
        self.assertEqual(tasks[0]['due_at'],'2025-11-07T19:00:00+08:00')
        self.assertEqual(json.loads(s.read('SELECT source FROM service_task_versions WHERE task_id=?',(tasks[0]['id'],))[0]['source'])['kind'],'host_due_recheck')
        self.assertFalse(s.read('SELECT * FROM deliveries'))
    def test_weekly_repeated_sync_and_recovery_keep_one_matter(self):
        self.configure('2025-11-07T19:00:00+08:00')
        for _ in range(4):ap.sync();wf.recover()
        tasks=self.interaction_tasks();self.assertEqual(len(tasks),1)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='cancelled' WHERE id=?",(tasks[0]['id'],))
        for _ in range(3):ap.sync()
        self.assertEqual(len(self.interaction_tasks()),1);self.assertEqual(self.interaction_tasks()[0]['status'],'cancelled')
    def test_weekly_no_learning_is_negative_and_missing_is_unknown(self):
        self.clear_week();self.configure('2025-11-07T19:00:00+08:00');self.assertFalse(self.interaction_tasks())
        snap=tm.snapshot()
        with closing(sqlite3.connect(s.DB)) as c:
            self.assertEqual(wi.decision(c,tm.state(),snap)['status'],'not_eligible')
            snap['weekly_learning']['current_week']['coverage_complete']=False
            self.assertEqual(wi.decision(c,tm.state(),snap)['status'],'unknown')
    def test_weekly_day_pause_prevents_due_task(self):
        self.configure('2025-11-07T06:30:00+08:00');self.finish_empty_morning()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-11-07','pause-evidence','pause',s.now()))
        tm.advance('2025-11-07T19:00:00+08:00');self.assertFalse(self.interaction_tasks());self.assertFalse(s.read('SELECT * FROM deliveries'))
    def test_weekly_scope_pause_prevents_due_task(self):
        self.configure('2025-11-07T06:30:00+08:00');self.finish_empty_morning()
        st=tm.state()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO service_workflow_constraints VALUES(?,?,?,?,?,?,?)',('test-pause',st['run_id'],st['uid'],json.dumps({'kind':'skill','skill':wi.RULE}),st['as_of'],'2025-11-07T23:59:59+08:00','[]'))
        tm.advance('2025-11-07T19:00:00+08:00');self.assertFalse(self.interaction_tasks())
    def test_weekly_outside_window_never_catches_up_next_day(self):
        self.configure('2025-11-07T06:30:00+08:00');self.finish_empty_morning()
        tm.advance('2025-11-07T22:00:00+08:00');self.assertFalse(self.interaction_tasks())
        tm.advance('2025-11-08T06:30:00+08:00');self.assertFalse(self.interaction_tasks())
    def test_weekly_already_sent_not_recreated(self):
        self.configure('2025-11-07T06:30:00+08:00');self.finish_empty_morning()
        with closing(sqlite3.connect(s.DB)) as c,c:
            item=wi.decision(c,tm.state(),tm.snapshot());c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(item['dedupe_key'],item['id'],'original-delivery',s.now()))
        tm.advance('2025-11-07T19:00:00+08:00');self.assertFalse(self.interaction_tasks())
    def test_manual_mode_never_promoted_to_automatic_weekly(self):
        self.configure('2025-11-07T19:00:00+08:00',False)
        self.assertFalse(self.interaction_tasks())
    def test_duplicate_completion_one_inbox_and_plan_after_restart(self):
        self.clear_week();self.configure('2025-11-07T17:00:00+08:00')
        self.assertEqual(self.learn()['status'],'recorded');ap.sync()
        self.assertEqual(self.learn()['status'],'duplicate')
        for _ in range(3):ap.sync();wf.recover()
        self.assertEqual(len(s.read("SELECT * FROM completion_events WHERE event_id='synthetic-friday-completion'")),1)
        self.assertEqual(len([r for r in s.read("SELECT * FROM service_tasks WHERE kind='completion_plan'") if 'synthetic-friday-completion' in r['candidate_id']]),1)
    def test_completed_daily_invitation_send_gate_is_negative(self):
        self.configure('2025-10-14T20:00:00+08:00')
        import s01_delivery
        self.assertEqual(s01_delivery.check('2025-10-14')['status'],'not_eligible');self.assertFalse(s.read('SELECT * FROM deliveries'))
    def test_s01_tool_contract_explains_host_binding_without_relaxing_guard(self):
        import delivery_capability as delivery
        string={'type':'string'}
        defs={'reply_to_message':('',{'properties':{'body':string,'generation_token':string}}),'deliver_proactive':('',{'properties':{'candidate_ids':{'type':'array'}}})}
        _,description,_=delivery.definition(lambda props,required:{'properties':props,'required':required},defs)
        self.assertIn('S01阅读邀请仅传body',description);self.assertIn('不传candidate_ids',description)
        with patch.object(ap,'active',return_value={'kind':'s01','day':'2025-11-07'}):
            with self.assertRaisesRegex(ValueError,'移除candidate_ids和asset_id'):delivery.route({'body':'邀请','candidate_ids':['s01:2025-11-07']})
            name,args=delivery.route({'body':'邀请','execution_token':'original-execution'})
            self.assertEqual(name,'deliver_message');self.assertNotIn('candidate_ids',args)

if __name__=='__main__':unittest.main()
