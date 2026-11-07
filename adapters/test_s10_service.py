"""S10 loads its own methods and reply style; transport guards still apply."""
import datetime as dt
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


class S10Tests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def begin(self,day='2025-10-25'):
        tm.activate(day+' 09:00',True);ap.enable()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='cancelled' WHERE kind!='weekly'")
        task=ap.claim();self.assertEqual(task['kind'],'weekly');self.assertTrue(ap.s10_task(task))
        for name in ['weekly-learning-review','reply-style']:ap.load_skill(name)
        audit.invoke('family_profile',{},s.family_profile)
        audit.invoke('learning_query',{'day':day},lambda:tm.snapshot(day))
        audit.invoke('daily_context',{'day':day},lambda:b.context(day))
        audit.invoke('action_candidates',{'day':day},lambda:b.candidates(day))
        candidate=next(x for x in p.refresh(day) if x['skill']=='weekly-learning-review')
        audit.invoke('check_proactive',{'candidate_id':b.token(candidate['id'])},lambda:b.project(p.check(candidate['id'])))
        return task,candidate

    def test_s10_dependencies_exclude_policy_and_other_actions_keep_it(self):
        deps=catalog.action_dependencies('S10')
        self.assertEqual(deps['skills'],['reply-style'])
        self.assertEqual(deps['references'],[])
        self.assertNotIn('student-service-tier',deps['skills'])
        self.assertIn('service-policy',catalog.action_dependencies('Q01')['skills'])

    def test_s10_envelope_uses_completion_band_and_pdf(self):
        import task_context
        task,_=self.begin();context=task_context.prepare(task['id'])
        self.assertIn('weekly_review',context['prompt']);self.assertIn('weekly_material',context['prompt'])
        self.assertNotIn('<loaded_skill name="student-service-tier"',context['prompt'])

    def test_saturday_executes_directly_without_planning(self):
        task,_=self.begin();prompt=ap.prompt(task['id'])['prompt']
        self.assertIn('weekly-learning-review',prompt)
        self.assertIn('周六',prompt)
        self.assertIn('不调用save_plan',prompt)
        self.assertIn('不创建新的service_schedule',prompt)
        self.assertTrue(ap.receipt_driven(task))

    def test_s10_skill_and_all_policy_reference_reads_return_no_policy_body(self):
        self.begin();import mcp_server as mcp
        policy=content.skill_file('service-policy').read_text(encoding='utf-8')
        for name in ['service-policy','tutor-main','proactive-service','plan-today']:
            value=mcp.audited_call('service_skill',{'name':name})
            self.assertEqual(value['status'],'not_applicable')
            self.assertNotIn(policy,json.dumps(value,ensure_ascii=False))
        for path in ['references/action-templates.md','references/supervision.md','references/qa.md']:
            value=mcp.audited_call('service_reference',{'name':'service-policy','path':path})
            self.assertEqual(value['status'],'not_applicable')
            self.assertNotIn('shared_rules',value)
        self.assertEqual(s.read("SELECT * FROM service_skill_reads WHERE name='service-policy'"),[])
        self.assertIsNone(ap.load_skill('reply-style')['required_shared_skill'])

    def test_delivery_gate_no_longer_needs_shared_policy(self):
        _,candidate=self.begin()
        ap.require_skills('weekly-learning-review','reply-style')
        ap.guard_delivery([candidate['id']])
        self.assertEqual(s.read("SELECT * FROM service_skill_reads WHERE name='service-policy'"),[])

    def test_s10_cannot_send_a_different_action(self):
        self.begin()
        with patch.object(p,'refresh',return_value=[{'id':'other','skill':'plan-weekend-makeup'}]):
            with self.assertRaisesRegex(ValueError,'本次S10只执行|S09只由周五'):ap.guard_delivery(['other'])

    def test_pause_and_daily_contact_quota_are_still_enforced(self):
        _,candidate=self.begin();day=tm.state()['as_of'][:10]
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',(day,'m','今天不联系',s.now()))
        self.assertEqual(p.deliver([candidate['id']],'一周阅读回顾～')['status'],'skipped')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('DELETE FROM contact_constraints')
            for n in range(2):c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('earlier-'+str(n),'此前联系','sent',tm.state()['as_of'],'test'))
        result=p.deliver([candidate['id']],'一周阅读回顾～')
        self.assertEqual(result['status'],'skipped');self.assertIn('额度',result['reason'])

    def test_minimum_contact_interval_remains_in_force(self):
        _,candidate=self.begin()
        stamp=(tm.clock()-dt.timedelta(minutes=30)).isoformat()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('earlier','此前联系','sent',stamp,'test'))
        self.assertEqual(p.deliver([candidate['id']],'一周阅读回顾～')['status'],'deferred')

    def test_accepted_waits_and_sent_completes_without_resending(self):
        task,_=self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('s10-case','一周阅读回顾～','accepted',s.now(),'test'))
            c.execute('INSERT INTO service_links VALUES(?,?)',(task['id'],'s10-case'))
        self.assertEqual(ap.finish(task['id'],'deferred','等待回执')['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent'")
        ap.reconcile_s01_receipts()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'done')
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_sunday_no_longer_creates_weekly_task(self):
        tm.activate('2025-10-26 09:00',True);ap.enable()
        self.assertEqual(s.read("SELECT id FROM service_tasks WHERE kind='weekly'"),[])

    def test_policy_remains_excluded_during_native_wrapup_then_scope_is_cleared(self):
        task,_=self.begin();ap.dispatched(task['id'],'native-s10-case')
        ap.finish(task['id'],'no_contact','本周已有等价回顾')
        self.assertIsNone(ap.active())
        self.assertEqual(ap.load_skill('service-policy')['status'],'not_applicable')
        ap.settle(task['id'])
        self.assertFalse(ap.s10_task())
        self.assertIn('shared_rules',ap.load_skill('service-policy'))


if __name__=='__main__':unittest.main()
