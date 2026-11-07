import json
import sqlite3
from contextlib import closing
from unittest.mock import patch
import unittest
import test_fixture_integration as fixtures
import test_mode as tm
import sql_tools as s
import agent_plan as ap
import observability as audit
import business as b
import wechat_test as w
import agent_view
import course_service as cs


class S01DeliveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.FixtureIntegrationTests.setUp(self)
        legacy=patch('s01_reading_guides.required',return_value=False)
        legacy.start();self.addCleanup(legacy.stop)  # Old frozen text-only S01 contract.
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def test_morning_boundary_and_frozen_old_contract(self):
        import datetime as dt,service_workflow as wf
        tm.activate('2025-10-13 06:30',True);ap.enable()
        for clock,expected in [('06:29:59','not_due'),('06:30:00','eligible')]:
            with patch.object(tm,'clock',return_value=dt.datetime.fromisoformat('2025-10-13T'+clock+'+08:00')):
                self.assertEqual(s.check('2025-10-13')['status'],expected)
        with patch.object(wf,'planning_contract',return_value={'fixed_time':'07:00'}):
            self.assertEqual(s.check('2025-10-13')['status'],'not_due')

    def begin(self):
        tm.activate('2025-10-13 07:00',True);ap.enable()
        plan=ap.claim()
        for skill in ['service-policy','tutor-main','plan-today','proactive-service','reply-style','remind-reading']:ap.load_skill(skill)
        for name,fn in [('daily_context',lambda:b.context('2025-10-13')),('action_candidates',lambda:b.candidates('2025-10-13')),('service_schedule_list',ap.schedules)]:audit.invoke(name,{},fn)
        audit.invoke('save_plan',{},lambda:ap.save('2025-10-13',[],'沿用固定检查'))
        ap.finish(plan['id'],'completed','计划已落实')
        task=ap.claim();self.assertEqual(task['kind'],'s01')
        for skill in ['service-policy','tutor-main','reply-style','remind-reading']:ap.load_skill(skill)
        audit.invoke('daily_context',{},lambda:b.context('2025-10-13'))
        audit.invoke('course_schedule',{'date':'2025-10-13','class_type':'L2','cohort':'61'},lambda:cs.course_schedule('2025-10-13','L2','61'))
        audit.invoke('learning_query',{'day':'2025-10-13'},lambda:tm.snapshot('2025-10-13'))
        audit.invoke('course_knowledge',{'class_type':'L2','lesson_no':1},lambda:{'status':'unavailable'})
        return task

    def test_basic_delivery_real_outbox_dedupe_and_receipt(self):
        task=self.begin()
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'provider-01'}) as sender:
            body='侨侨，今天有一节阅读课，家长可以转告孩子，找个方便的时间来读吧。'
            result=s.deliver('2025-10-13',body)
            self.assertEqual(result['status'],'accepted')
            self.assertEqual(body,result['body'])
            self.assertEqual(s.deliver('2025-10-13','another')['status'],'accepted')
            self.assertEqual(sender.call_count,1)
        self.assertEqual(ap.finish(task['id'],'completed','提醒已受理')['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent'")
        ap.sync()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'done')
        self.assertEqual(len(s.read('SELECT * FROM service_links')),1)

    def test_contact_pause_and_active_chat_prevent_send(self):
        self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-13','m','不要提醒',s.now()))
        with patch.object(w,'http_send') as sender:
            self.assertEqual(s.deliver('2025-10-13','hi')['status'],'skipped')
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute('DELETE FROM contact_constraints')
                c.execute('INSERT OR REPLACE INTO task_state VALUES(?,?,?)',('chat-gate','active',json.dumps({'active':True})))
            self.assertEqual(s.deliver('2025-10-13','hi')['status'],'deferred')
            sender.assert_not_called()

    def test_split_reminder_is_one_action_with_two_physical_texts(self):
        self.begin()
        messages=['今天的阅读课可以找个方便的时间来读。'*5,'这节课的故事读完以后再接着后面的安排。'*5]
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'split1'},{'errcode':0,'requestId':'split2'}]) as sender:
            self.assertEqual(s.deliver('2025-10-13','\n\n'.join(messages))['status'],'accepted')
            self.assertEqual([x.args[3] for x in sender.call_args_list],messages)
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)
        self.assertEqual(len(s.read('SELECT * FROM service_links')),1)

    def test_unknown_locked_completed_stale_day_do_not_send(self):
        self.begin();snap=tm.snapshot()
        for field,value,expected in [('status','unknown','unknown'),('status','completed','not_eligible')]:
            changed=json.loads(json.dumps(snap));changed['lessons_as_of'][0][field]=value
            with patch.object(tm,'snapshot',return_value=changed),patch.object(w,'http_send') as sender:
                self.assertEqual(s.check('2025-10-13')['status'],expected)
                self.assertEqual(s.deliver('2025-10-13','hi')['status'],'skipped');sender.assert_not_called()
        self.assertEqual(s.check('2025-10-12')['status'],'unknown')

    def test_uncertain_transport_is_not_retried_or_marked_complete(self):
        task=self.begin()
        with patch.object(w,'http_send',side_effect=TimeoutError) as sender:
            self.assertEqual(s.deliver('2025-10-13','hi')['status'],'unknown')
            s.deliver('2025-10-13','hi');self.assertEqual(sender.call_count,1)
        self.assertEqual(ap.finish(task['id'],'completed','attempted')['status'],'blocked')

    def test_unlock_fields_are_not_required_and_not_projected(self):
        self.begin();snap=tm.snapshot()
        for row in snap['lessons_as_of']:
            row.pop('unlocked',None);row.pop('unlocked_at',None)
        with patch.object(tm,'snapshot',return_value=snap):self.assertEqual(s.check('2025-10-13')['status'],'eligible')
        self.assertEqual(agent_view.project({'unlocked':False,'unlocked_at':'x','unlock_at':'y','status':'not_started'}),{'status':'not_started'})

    def test_missing_course_lookup_requires_repair_without_sending(self):
        self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("DELETE FROM tool_audit WHERE name='course_knowledge'")
        with patch.object(w,'http_send') as sender:
            result=s.deliver('2025-10-13','邀请阅读')
            self.assertEqual(result['status'],'needs_revision');sender.assert_not_called()

    def test_completed_ends_without_reading_course_or_sending(self):
        task=self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("DELETE FROM tool_audit WHERE name='course_knowledge'")
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("DELETE FROM tool_audit WHERE name='daily_context'")
        snap=tm.snapshot();snap['lessons_as_of'][0]['status']='completed'
        with patch.object(tm,'snapshot',return_value=snap),patch.object(w,'http_send') as sender:
            self.assertEqual(s.check('2025-10-13')['status'],'not_eligible');sender.assert_not_called()
            self.assertEqual(ap.finish(task['id'],'no_contact','已完课，事项结束')['status'],'no_contact')

    def test_model_context_has_no_environment_identity(self):
        self.begin()
        context=b.context('2025-10-13')
        text=json.dumps(context,ensure_ascii=False)
        for word in ['local_mock_fixture','test_run_id','test_instructions','bound_test_wechat','测试时钟','首月测试']:
            self.assertNotIn(word,text)
        self.assertEqual(context['identity']['guardian_role'],tm.snapshot()['profile']['guardian_role'])
        self.assertEqual(agent_view.project({'status':'mock_sent','simulation':True,'notice':'mock'}) ,{'status':'sent'})


if __name__=='__main__':unittest.main()
