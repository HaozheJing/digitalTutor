import datetime as dt,json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import sql_tools as s,test_mode as tm,proactive as p,agent_plan as ap,service_workflow as wf,completion_planner as cp,service_ledger as ledger
import test_s02_required_planning as base

class ContactConsistencyTests(unittest.TestCase):
    setUp=base.RequiredS02PlanningTests.setUp
    tearDown=base.RequiredS02PlanningTests.tearDown
    create_source=base.RequiredS02PlanningTests.create_source
    set_context=base.RequiredS02PlanningTests.set_context
    begin=base.RequiredS02PlanningTests.begin
    choices=base.RequiredS02PlanningTests.choices
    workflow_begin=base.RequiredS02PlanningTests.workflow_begin

    def prior(self,at='2025-10-17T19:00:00+08:00'):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('prior-contact','prior','sent',at,'test'))
    def test_plan_receipt_ledger_and_dispatch_agree_on_actual_time(self):
        task,_=self.workflow_begin('2025-10-17 19:00');self.prior();ctx=cp.context()
        choices=self.choices(ctx,'S02');choices[0]['run_at']='2025-10-17T19:10:00+08:00'
        result=cp.commit('spacing-plan',ctx['context_token'],choices)
        item=next(d for d in result['decisions'] if d['code']=='S02')
        self.assertEqual(item['run_at'],'2025-10-17T20:00:00+08:00');self.assertIn('19:00',item['reason'])
        saved=s.read('SELECT due_at FROM service_tasks WHERE id=?',(item['task_id'],))[0]
        self.assertEqual(saved['due_at'],item['run_at'])
        visible=ledger.view()['plans'][0]['body']['decisions'][0]
        self.assertEqual(visible['run_at'],item['run_at']);self.assertEqual(visible['requested_run_at'],choices[0]['run_at'])
        self.assertTrue(cp.commit('spacing-plan',ctx['context_token'],choices)['duplicate'])
        original=json.loads(s.read('SELECT body FROM completion_plans')[0]['body'])
        self.assertEqual(original['decisions'][0]['run_at'],choices[0]['run_at'])

    def test_new_contact_after_plan_defers_before_model_without_attempt(self):
        task,ctx=self.workflow_begin('2025-10-17 19:00');choices=self.choices(ctx,'S02')
        tid=cp.commit('preflight-plan',ctx['context_token'],choices)['decisions'][0]['task_id']
        ap.finish(task['id'],'completed','已提交');self.prior()
        state=tm.state();state['manual_service_task_id']=tid
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        self.assertIsNone(ap.claim());row=s.read('SELECT * FROM service_tasks WHERE id=?',(tid,))[0]
        self.assertEqual(row['status'],'deferred');self.assertEqual(row['attempts'],0);self.assertEqual(row['due_at'],'2025-10-17T20:00:00+08:00')
        self.assertIsNone(row['native_request_id']);self.assertIsNone(ap.active())
        calls=s.read('SELECT name FROM tool_audit WHERE task_id=?',(tid,));self.assertEqual([x['name'] for x in calls],['contact_preflight'])
        self.assertFalse(s.read('SELECT * FROM service_links'));self.assertFalse(s.read('SELECT * FROM test_outbox'))

    def candidate(self,rule):
        return {'id':rule,'rule':rule,'time_window':{'earliest':'2025-10-17T19:00:00+08:00','latest':'2025-10-17T21:59:59+08:00'}}
    def test_interaction_only_exemption_and_guards(self):
        self.workflow_begin('2025-10-17 19:00');self.prior('2025-10-17T18:50:00+08:00')
        at=dt.datetime.fromisoformat(tm.state()['as_of']);i=self.candidate('invite-reading-interaction');other=self.candidate('celebrate-lesson-completion')
        with closing(sqlite3.connect(s.DB)) as c,c:
            self.assertIsNone(p.contact_gate(c,[i],at))
            delayed=p.contact_gate(c,[other],at);self.assertEqual(delayed['retry_at'],'2025-10-17T19:50:00+08:00')
            self.assertEqual(delayed['causes'][0]['reason_code'],'contact_min_gap');self.assertIn('18:50',delayed['reason'])
            self.assertEqual(p.contact_gate(c,[i,other],at)['status'],'deferred')
            c.execute("INSERT OR REPLACE INTO task_state(id,payload) VALUES('chat-gate',?)",(s.encoded({'active':True}),))
            self.assertEqual(p.contact_gate(c,[i],at)['causes'][0]['reason_code'],'dialogue_busy')
            c.execute("DELETE FROM task_state WHERE id='chat-gate'")
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-17','pause','今天暂停',s.now()))
            self.assertEqual(p.contact_gate(c,[i],at)['status'],'skipped')
            c.execute('DELETE FROM contact_constraints')
            c.execute('UPDATE proactive_settings SET body=? WHERE key=?',(s.encoded({'daily_limit':1,'min_gap_minutes':60}),'contact'))
            self.assertEqual(p.contact_gate(c,[i],at)['reason'],'当日非必需联系额度已用完')
            with patch('service_hours.is_open',return_value=False):self.assertIn('22:00',p.contact_gate(c,[i],at)['reason'])

    def test_s08_time_and_interaction_time_catalog(self):
        _,ctx=self.workflow_begin('2025-10-17 17:25')
        self.assertEqual(wf.planning_contract('close-week-reading-gap')['fixed_time'],'18:00')
        self.assertEqual(wf.DAILY['close-week-reading-gap'][1],'18:00');self.assertEqual(wf.DAILY['invite-reading-interaction'][1],'19:00')
        with closing(sqlite3.connect(s.DB)) as c:
            catalog=wf.catalog(c,tm.state())
            for skill,hour in [('close-week-reading-gap','18:00'),('invite-reading-interaction','19:00')]:
                entry=next(x for x in catalog if x['skill']==skill)
                self.assertIn('T'+hour,entry['timing']['at'])
                for item in entry['opportunities']:self.assertIn('T'+hour,item['time_window']['earliest'])

    def test_contact_after_window_allows_truthful_skip_not_impossible_s02(self):
        _,ctx=self.workflow_begin('2025-10-17 21:30');self.prior('2025-10-17T21:20:00+08:00');ctx=cp.context()
        candidate=next(x for x in ctx['candidates'] if x['skill']==cp.RULES['S02'])
        self.assertEqual(candidate['planning_requirement']['skip_basis'],'contact_window_expired')
        self.assertEqual(cp.commit('expired-window',ctx['context_token'],self.choices(ctx))['status'],'committed')
        self.assertFalse(s.read("SELECT * FROM service_tasks WHERE skill='celebrate-lesson-completion'"))

class InteractionContextTests(unittest.TestCase):
    import test_weekly_interaction as base
    setUp=base.WeeklyInteractionTests.setUp
    tearDown=base.WeeklyInteractionTests.tearDown
    create_source=base.WeeklyInteractionTests.create_source
    set_context=base.WeeklyInteractionTests.set_context
    begin=base.WeeklyInteractionTests.begin

    def test_actual_prompt_contains_recent_parent_context_and_new_rules(self):
        task,_=self.begin('2025-10-24')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)',('recent-parent','user','刚才说的最后一课还没读，孩子想先聊故事里的狐狸。',s.now(),'fixture','hash'))
        prompt=ap.prompt(task['id'])['prompt']
        for text in ['刚才说的最后一课还没读','先承接前文','单独的周互动不受60分钟','表扬别人时不要用“稳”','提建议时少用“就好”“就行”']:
            self.assertIn(text,prompt)
        self.assertFalse(s.read('SELECT * FROM test_outbox'))

if __name__=='__main__':unittest.main()
