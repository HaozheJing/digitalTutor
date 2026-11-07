"""Isolated checks for unified Friday invitations and policy-free execution."""
import copy
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
import agent_plan as ap
import business as b
import observability as audit
import proactive as p
import service_catalog as catalog
import service_content as content
import sql_tools as s
import test_fixture_integration as fixtures
import test_mode as tm
import weekly_interaction as wi


class WeeklyInteractionTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def begin(self, day='2025-10-24'):
        tm.activate(day+' 19:00',True);ap.enable()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='cancelled' WHERE kind!='interaction'")
        task=ap.claim();self.assertEqual(task['kind'],'interaction')
        for name in [wi.RULE,'reply-style']:ap.load_skill(name)
        audit.invoke('family_profile',{},s.family_profile)
        audit.invoke('learning_query',{'day':day},lambda:tm.snapshot(day))
        audit.invoke('daily_context',{'day':day},lambda:b.context(day))
        audit.invoke('action_candidates',{'day':day},lambda:b.candidates(day))
        candidate=next(x for x in p.refresh(day) if x['skill']==wi.RULE)
        audit.invoke('check_proactive',{'candidate_id':b.token(candidate['id'])},lambda:b.project(p.check(candidate['id'])))
        return task,candidate

    def test_fridays_have_one_fixed_task_even_before_learning(self):
        for day in ['2025-10-17','2025-10-24','2025-10-31','2025-11-07']:
            tm.activate(day+' 19:00',False);ap.enable();ap.sync()
            tasks=[r for r in ap.tasks() if r['kind']=='interaction']
            self.assertEqual(len(tasks),1)
            with self.assertRaises(ap.ServiceValidationError):ap.prompt(tasks[0]['id'])
            import console_control
            console_control.execute(tasks[0]['id'])
            self.assertEqual(ap.claim()['id'],tasks[0]['id'])
            self.assertIn(wi.RULE,ap.prompt(tasks[0]['id'])['prompt'])
            self.assertTrue(ap.receipt_driven(tasks[0]))
            ap.finish(tasks[0]['id'],'blocked','仅验证宿主装配，不执行外发')
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='cancelled' WHERE kind='plan'")
            self.assertIsNone(ap.claim())
        tm.activate('2025-11-06 19:00',False);ap.enable()
        self.assertFalse(any(t['kind']=='interaction' for t in ap.tasks()))

    def test_policy_and_examples_are_not_loaded(self):
        self.begin();import mcp_server as mcp
        for code in catalog.WEEKLY_INTERACTION_IDS:
            self.assertEqual(catalog.action_dependencies(code)['skills'],['reply-style'])
            self.assertEqual(catalog.action_dependencies(code)['references'],[])
        for name in ['service-policy','tutor-main','proactive-service','plan-today']:
            self.assertEqual(mcp.audited_call('service_skill',{'name':name})['status'],'not_applicable')
            self.assertEqual(mcp.audited_call('service_reference',{'name':name,'path':'references/interaction.md'})['status'],'not_applicable')
        self.assertEqual(ap.load_skill(wi.RULE)['references'],['references/planning-contract.md'])
        self.assertIsNone(ap.load_skill(wi.RULE)['required_shared_skill'])
        self.assertEqual(s.read("SELECT * FROM service_skill_reads WHERE name='service-policy'"),[])
        self.assertIn('service-policy',catalog.action_dependencies('S08')['skills'])

    def test_empty_or_future_learning_is_not_an_invitation(self):
        self.begin();snap=copy.deepcopy(tm.snapshot());week=snap['weekly_learning']['current_week']
        for lesson in week['lessons']:lesson['status']='not_started'
        with closing(sqlite3.connect(s.DB)) as c:
            self.assertEqual(wi.decision(c,tm.state(),snap)['status'],'not_eligible')
            week['coverage_complete']=False
            self.assertEqual(wi.decision(c,tm.state(),snap)['status'],'unknown')

    def test_preparation_uses_a_completed_lesson_full_text(self):
        self.begin();snap=tm.snapshot()
        learned=next(x for x in snap['weekly_learning']['current_week']['lessons'] if x['status']=='completed')
        self.assertIsNotNone(wi.preparation([],snap))
        trace=[{'name':'course_knowledge','arguments':{'class_type':'L2','lesson_no':learned['lesson_no']},
                'result':{'status':'ok','body':'verified lesson text'}}]
        self.assertIsNone(wi.preparation(trace,snap))
        trace[0]['arguments']['lesson_no']=20
        self.assertIsNotNone(wi.preparation(trace,snap))

    def test_send_gate_requires_material_and_rejects_other_actions(self):
        _,candidate=self.begin()
        with self.assertRaisesRegex(ValueError,'正文'):ap.guard_delivery([candidate['id']])
        with patch.object(wi,'preparation',return_value=None):ap.guard_delivery([candidate['id']])
        with patch.object(p,'refresh',return_value=[{'id':'other','skill':'close-week-reading-gap'}]):
            with self.assertRaisesRegex(ValueError,'只执行周互动'):ap.guard_delivery(['other'])

    def test_pause_and_deduplication_remain_enforced(self):
        _,candidate=self.begin();day=tm.state()['as_of'][:10]
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',(day,'m','pause',s.now()))
        self.assertEqual(p.deliver([candidate['id']],'想和你聊聊这周的故事～')['status'],'skipped')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('DELETE FROM contact_constraints')
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(candidate['dedupe_key'],candidate['id'],'sent-before',s.now()))
        self.assertEqual(next(x for x in p.refresh(day) if x['skill']==wi.RULE)['status'],'already_delivered')

    def test_delivery_finishes_invitation_without_waiting_for_reply(self):
        task,_=self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('interaction-case','invite','accepted',s.now(),'test'))
            c.execute('INSERT INTO service_links VALUES(?,?)',(task['id'],'interaction-case'))
        self.assertEqual(ap.finish(task['id'],'completed','invited')['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent'")
        ap.reconcile_s01_receipts()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'done')


if __name__=='__main__':unittest.main()
