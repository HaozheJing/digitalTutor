import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as fixtures
import test_mode as tm
import sql_tools as s
import agent_plan as ap
import observability as audit
import business as b
import proactive as p
import wechat_test as w


class AgentPlanTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def begin(self,at='2025-10-25 16:00'):
        tm.activate(at,True);ap.enable()
        task=ap.claim()
        self.assertEqual(task['kind'],'plan')
        for name in ['service-policy','tutor-main','plan-today','proactive-service']:
            ap.load_skill(name)
        self.query()
        return task

    def query(self):
        day=tm.state()['as_of'][:10]
        for name,fn in [('daily_context',lambda:b.context(day)),
                        ('action_candidates',lambda:b.candidates(day)),
                        ('service_schedule_list',ap.schedules)]:
            audit.invoke(name,{},fn)
        audit.invoke('family_profile',{},s.family_profile)
        audit.invoke('learning_query',{'day':day},lambda:tm.snapshot(day))
        if ap.active() and ap.active()['kind'] in ('s01','s08'):
            import course_service
            audit.invoke('course_schedule',{'date':day,'class_type':'L2','cohort':'61'},lambda:course_service.course_schedule(day,'L2','61'))
            audit.invoke('learning_query',{'day':day},lambda:tm.snapshot(day))

    def save(self,items=None):
        return audit.invoke('save_plan',{},lambda:ap.save(tm.state()['as_of'][:10],items or [],'根据当前事实，保留晚间检查并落实所需安排。'))

    def candidate(self):
        return next(r for r in p.refresh(tm.state()['as_of'][:10]) if r['skill']=='plan-weekend-makeup')

    def finish_plan(self,task,items=None):
        self.save(items);ap.finish(task['id'],'completed','已核对学情并保存计划')

    def finish_check(self,kind,reason='已复查，当前不联系'):
        task=ap.claim()
        self.assertIsNotNone(task)
        self.assertEqual(task['kind'],kind)
        if ap.s10_task(task):
            ap.load_skill('weekly-learning-review');ap.load_skill('reply-style')
        elif ap.weekend_task(task):
            ap.load_skill('plan-weekend-makeup');ap.load_skill('reply-style')
        elif ap.interaction_task(task):
            ap.load_skill('invite-reading-interaction');ap.load_skill('reply-style')
        else:ap.load_skill(task['skill']);ap.load_skill('reply-style')
        self.query()
        if kind=='s01':
            audit.invoke('check_s01',{},lambda:s.check(tm.state()['as_of'][:10]))
        ap.finish(task['id'],'no_contact',reason)
        return task

    def test_receipts_require_skills_queries_and_real_task(self):
        tm.activate('2025-10-25 16:00');ap.enable();ap.claim()
        with self.assertRaises(ValueError):ap.save('2025-10-25',[],'empty')
        for name in ['service-policy','tutor-main','plan-today','proactive-service']:ap.load_skill(name)
        with self.assertRaises(ValueError):ap.save('2025-10-25',[],'empty')
        self.query()
        with self.assertRaises(ValueError):ap.save('2025-10-25',[{'candidate_id':'invented','schedule_id':'schedule-fake'}],'bad')
        self.assertEqual(self.save()['source'],'model')

    def test_schedule_advance_preserves_receipt_and_checks_current_facts(self):
        task=self.begin('2025-10-24 16:00');candidate=self.candidate()
        with self.assertRaises(ValueError):ap.schedule(candidate['id'],'2025-10-24T15:00:00+08:00','reason','check')
        scheduled=ap.schedule(candidate['id'],'2025-10-24T20:00:00+08:00','周五晚间方便安排补读','先重新核对仍欠哪些课')
        self.assertEqual(ap.schedule(candidate['id'],'2025-10-24T21:00:00+08:00','same','same')['id'],scheduled['id'])
        with self.assertRaises(ValueError):self.save([])
        self.finish_plan(task,[{'candidate_id':candidate['id'],'schedule_id':scheduled['id']}])
        run=tm.state()['run_id']
        tm.advance('2025-10-24 19:00')
        self.assertEqual(tm.state()['run_id'],run)
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(scheduled['id'],))[0]['status'],'planned')
        self.assertEqual(len(s.read('SELECT * FROM daily_plans')),1)
        with self.assertRaises(ValueError):tm.advance('2025-10-24 18:00')
        self.assertEqual(ap.claim()['kind'],'plan')  # Re-evaluation precedes execution.

    def test_daily_recurrence_crosses_days_without_replaying_history(self):
        task=self.begin('2025-10-13 06:30');self.finish_plan(task)
        run=tm.state()['run_id']
        tm.advance('2025-10-14 06:29')
        self.assertEqual(tm.state()['run_id'],run)
        self.assertIsNone(ap.claim())
        self.assertEqual(ap.view('2025-10-13')['model_plans'][0]['source'],'model')
        self.assertEqual(ap.recurrence()['next_due_at'],'2025-10-14T06:30:00+08:00')
        tm.advance('2025-10-14 06:30')
        next_task=ap.claim()
        self.assertEqual(next_task['kind'],'plan')
        self.assertNotEqual(next_task['id'],task['id'])
        self.assertEqual(next_task['day'],'2025-10-14')
        self.query();self.finish_plan(next_task)
        ap.sync();self.finish_check('s01');self.assertIsNone(ap.claim())
        self.assertEqual(len([r for r in ap.tasks() if r['kind']=='plan']),1)
        tm.advance('2025-10-16 06:29')
        self.assertEqual(ap.tasks('2025-10-15'),[])
        self.assertTrue(all(r['status']=='expired' for r in ap.tasks('2025-10-14') if r['kind'] not in ('plan','s01')))
        self.assertEqual(next(r['status'] for r in ap.tasks('2025-10-14') if r['kind']=='s01'),'no_contact')
        self.assertIsNone(ap.claim())

    def test_legacy_fixed_receipt_is_not_replayed(self):
        tm.activate('2025-10-13 06:30');ap.enable()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET id=?,status='done' WHERE kind='plan'",
                      ('service-'+tm.state()['run_id']+'-plan',))
        ap.sync()
        self.assertEqual(len([r for r in ap.tasks() if r['kind']=='plan']),1)
        self.assertIsNone(ap.claim())

    def test_recurrence_waits_for_course_start(self):
        import fixture_query as fq
        tm.activate('2025-10-13 06:30')
        fixture=json.loads(fq.FILE.read_text(encoding='utf8'))
        fixture['enrollments'][0]['course_start_date']='2025-10-14'
        file=self.root/'later-enrollment.json'
        file.write_text(json.dumps(fixture),encoding='utf8')
        with patch.object(fq,'FILE',file):
            self.assertEqual(ap.enable()['status'],'outside_service_period')
            self.assertEqual(ap.tasks(),[])
            self.assertIsNone(ap.claim())
            tm.advance('2025-10-14 06:30')
            self.assertEqual(ap.claim()['kind'],'plan')

    def test_no_success_without_finish_or_send_receipt_and_recovery_no_retry(self):
        task=self.begin();self.finish_plan(task)
        self.finish_check('s01')
        self.finish_check('weekly')
        calendar=ap.claim();self.assertEqual(calendar['kind'],'calendar');self.query()
        with self.assertRaises(ValueError):ap.finish(calendar['id'],'completed','assume done')
        ap.recover()
        row=s.read('SELECT * FROM service_tasks WHERE id=?',(calendar['id'],))[0]
        self.assertEqual(row['status'],'blocked')
        self.assertIsNone(ap.claim())  # Afternoon: evening is not due.

    def test_scope_and_future_equivalent_schedule_block_delivery(self):
        task=self.begin('2025-10-24 20:00');candidate=self.candidate()
        item=ap.schedule(candidate['id'],'2025-10-24T20:30:00+08:00','reason','recheck')
        self.finish_plan(task,[{'candidate_id':candidate['id'],'schedule_id':item['id']}])
        self.finish_check('s01','今天已有完课记录')
        cal=ap.claim();self.assertEqual(cal['kind'],'calendar');self.query()
        ap.load_skill('reply-style');ap.load_skill(candidate['skill'])
        audit.invoke('check_proactive',{'candidate_id':candidate['id']},lambda:p.check(candidate['id']))
        with self.assertRaises(ValueError):ap.guard_delivery([candidate['id']])
        ap.finish(cal['id'],'no_contact','无适用日历信号')
        for kind in ['s08','interaction','evening']:self.finish_check(kind)
        weekly=ap.claim();self.assertEqual(weekly['kind'],'weekly');self.query()
        audit.invoke('check_proactive',{'candidate_id':candidate['id']},lambda:p.check(candidate['id']))
        with self.assertRaisesRegex(ap.ServiceValidationError,'尚未到点'):
            ap.guard_delivery([candidate['id']])
        ap.finish(weekly['id'],'no_contact','沿用20点30分已有安排，不提前重复触达')
        self.assertIsNone(ap.claim())
        tm.advance('2025-10-24 20:30')
        repl=ap.claim();self.assertEqual(repl['kind'],'plan');self.query()
        self.finish_plan(repl,[{'candidate_id':candidate['id'],'schedule_id':item['id']}])
        due=ap.claim();self.assertEqual(due['id'],item['id']);self.query()
        audit.invoke('check_proactive',{'candidate_id':candidate['id']},lambda:p.check(candidate['id']))
        ap.guard_delivery([candidate['id']])

    def test_s01_missing_fulltext_is_not_invented_or_delivered(self):
        self.begin('2025-10-21 20:30')
        self.assertNotIn('course_material_status',s.check('2025-10-21'))
        with patch.object(w,'http_send') as sender:
            with self.assertRaises(ValueError):s.deliver('2025-10-21','invented course text')
            sender.assert_not_called()

    def test_real_action_delivery_linked_and_unknown_not_marked_done(self):
        task=self.begin('2025-10-13 20:30');self.finish_plan(task)
        for kind in ['s01','calendar']:
            job=ap.claim();self.assertEqual(job['kind'],kind);self.query()
            ap.load_skill(job['skill'])
            if kind=='s01':audit.invoke('check_s01',{},lambda:s.check('2025-10-13'))
            ap.finish(job['id'],'no_contact','无适用信号或已完成')
        job=ap.claim();self.query()
        candidate=p.refresh('2025-10-13')[0]
        ap.load_skill(candidate['skill']);ap.load_skill('reply-style')
        audit.invoke('check_proactive',{'candidate_id':candidate['id']},lambda:p.check(candidate['id']))
        ap.guard_delivery([candidate['id']])
        with patch.object(w,'http_send',side_effect=TimeoutError) as sender:
            result=p.deliver([candidate['id']],'侨侨，首课已经完成。')
            self.assertEqual(result['status'],'blocked')  # Existing image belongs to host, not model.
            import host_highlights
            queued=host_highlights.queue();self.assertEqual(queued['status'],'queued')
            ap.link_delivery(queued['items'][0]);w.flush()
            finished=ap.finish(job['id'],'completed','已提交交付')
            self.assertEqual(finished['status'],'blocked')
            w.flush();self.assertEqual(sender.call_count,1)
        self.assertEqual(ap.view()['items'][-1]['deliveries'][0]['status'],'unknown')

    def test_model_mode_disables_deterministic_sender(self):
        import test_scheduler
        self.begin('2025-10-13 20:30')
        with patch.object(w,'http_send') as sender:
            self.assertEqual(test_scheduler.tick()['status'],'agent_owned')
            sender.assert_not_called()

    def test_explicit_check_runs_with_automatic_off_but_scheduled_actions_wait(self):
        import test_scheduler
        task=self.begin('2025-10-25 20:30');self.finish_plan(task)
        test_scheduler.set_automatic(False)
        self.assertIsNone(ap.claim())
        requested=ap.request('evening')
        self.assertEqual(ap.claim()['id'],requested['task_id'])

    def test_passive_replanning_requires_current_event_queries(self):
        task=self.begin();self.finish_plan(task)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',('p1','passive','{}','dispatched',s.now(),1,s.now(),None,None))
        with self.assertRaises(ValueError):self.save()
        self.query();self.assertEqual(self.save()['source'],'model')
        self.assertTrue(s.read("SELECT 1 FROM tool_audit WHERE task_id='event:p1' AND name='save_plan' AND status='ok'"))

    def test_exhausted_deferrals_become_explicitly_blocked(self):
        task=self.begin();self.finish_plan(task)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='deferred',attempts=3 WHERE kind='calendar'")
        ap.sync()
        self.assertEqual(next(r for r in ap.tasks() if r['kind']=='calendar')['status'],'blocked')

    def test_missing_fresh_query_has_actionable_error_then_can_recover(self):
        import base_mcp
        task=self.begin('2025-10-25 19:00');self.finish_plan(task)
        job=ap.claim();self.assertEqual(job['kind'],'s01');ap.load_skill('remind-reading')
        audit.invoke('check_s01',{},lambda:s.check('2025-10-25'))
        with self.assertRaises(ap.ServiceValidationError) as caught:
            audit.invoke('service_task_finish',{},lambda:ap.finish(job['id'],'no_contact','今日无课'))
        self.assertIn('course_schedule',base_mcp.tool_error(caught.exception))
        record=next(r for r in audit.records(tm.state()['run_id']) if r['name']=='service_task_finish')
        self.assertIn('course_schedule',record['output']['reason'])
        self.assertNotIn('secret=abc',base_mcp.tool_error(ValueError('secret=abc')))
        audit.invoke('course_schedule',{},lambda:__import__('course_service').course_schedule('2025-10-25','L2','61'))
        self.assertEqual(ap.finish(job['id'],'no_contact','复查今日无课')['status'],'no_contact')

    def test_fixed_tasks_follow_friday_s09_and_saturday_s10_clock(self):
        for day,is_weekend in [('2025-10-24',False),('2025-10-25',True),('2025-10-26',True)]:
            with self.subTest(day=day):
                tm.activate(day+' 06:29',True);ap.enable()
                expected={'plan':'06:30','s01':'06:30','calendar':'09:30','evening':'19:00'}
                if day=='2025-10-24':expected.update(s08='18:00',interaction='19:00',weekly='20:00')
                if day=='2025-10-25':expected['weekly']='09:00'
                self.assertEqual({r['kind']:r['due_at'][11:16] for r in ap.tasks()},expected)
                self.assertIsNone(ap.claim())
                ap.sync()
                self.assertEqual(len(ap.tasks()),len(expected))  # Repeated sync cannot duplicate entry tasks.
                tm.activate(day+' 20:30',True);ap.enable()
                task=ap.claim();self.assertEqual(task['kind'],'plan')
                for skill in ['service-policy','tutor-main','plan-today','proactive-service']:ap.load_skill(skill)
                self.query();self.finish_plan(task)
                kinds=['s01']+(['weekly'] if day=='2025-10-25' else [])+['calendar']+(['s08','interaction'] if day=='2025-10-24' else [])+['evening']+(['weekly'] if day=='2025-10-24' else [])
                for kind in kinds:self.finish_check(kind)
                self.assertIsNone(ap.claim())

    def test_s01_at_six_thirty_reads_current_incomplete_without_waiting_for_due_at(self):
        tm.activate('2025-10-13 06:29',True);ap.enable()
        self.assertEqual(s.check('2025-10-13')['status'],'not_due')
        tm.advance('2025-10-13 06:30')
        current=s.check('2025-10-13')
        self.assertEqual(current['status'],'eligible')
        self.assertTrue(current['evidence'])
        self.assertTrue(all(r['complete_by_date']=='2025-10-13' and 'due_at' not in r for r in current['evidence']))
        self.assertTrue(all(not r['overdue'] and r['completed_at'] is None for r in current['evidence']))
        self.assertFalse(current['delivery_blocked'])
        self.assertNotIn('content_mode',current)
        tm.advance('2025-10-13 20:30')
        self.assertEqual(s.check('2025-10-13')['status'],'not_eligible')

    def test_weekly_candidates_open_at_configured_time_without_future_completion(self):
        for day,skill,before,due in [('2025-10-24','plan-weekend-makeup','19:59','20:00'),('2025-10-25','weekly-learning-review','08:59','09:00')]:
            with self.subTest(day=day):
                tm.activate(day+' '+before,True);ap.enable()
                candidate=next(r for r in p.refresh(day) if r['skill']==skill)
                self.assertEqual(candidate['time_window']['earliest'],day+'T'+due+':00+08:00')
                self.assertEqual(p.check(candidate['id'])['status'],'not_due')
                tm.advance(day+' '+due)
                self.assertEqual(p.check(candidate['id'])['status'],'eligible')
                current=next(r for r in p.refresh(day) if r['skill']==skill)
                self.assertEqual(current['id'],candidate['id'])
                self.assertEqual(current['evidence']['weekly_learning' if skill=='plan-weekend-makeup' else 'weekly_review']['as_of'],tm.state()['as_of'])
                self.assertTrue(all(r['scheduled_date']<=day and r['status']!='completed'
                                    for r in current['evidence']['overdue_lessons']))

    def test_blocked_can_close_even_when_required_read_is_unavailable(self):
        tm.activate('2025-10-25 16:00');ap.enable()
        job=ap.claim()
        result=ap.finish(job['id'],'blocked','资料工具不可用，无法完成查询或规划，未对客发送')
        self.assertEqual(result['status'],'blocked')
        self.assertIsNone(ap.active())


if __name__=='__main__':unittest.main()
