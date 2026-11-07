import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as fixtures
import test_mode as tm
import sql_tools as s
import proactive as p
import agent_plan as ap
import workbench_observer as observer
import observability as audit
import business as b
import wechat_test as w


class ObserverTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def start(self,at):
        tm.activate(at,True);ap.enable()

    def s02(self):
        return next(a for a in observer.view()['actions'] if a['code']=='S02')

    def test_god_view_future_is_separate_and_read_only(self):
        self.start('2025-10-17 06:30')
        with closing(sqlite3.connect(s.DB)) as c:before=list(c.iterdump())
        view=observer.view()
        self.assertTrue(any(not e['visible'] and e['title']=='完课' for e in view['timeline']))
        self.assertEqual(self.s02()['expectation'],'forecast')
        self.assertEqual(tm.snapshot()['lessons_as_of'][-1]['completed_at'],None)
        with closing(sqlite3.connect(s.DB)) as c:
            self.assertFalse(any(x['rule']=='celebrate-lesson-completion' for x in p.evaluate(c,'2025-10-17')))
        with closing(sqlite3.connect(s.DB)) as c:self.assertEqual(before,list(c.iterdump()))
        self.assertNotIn('timeline',tm.context('2025-10-17'))

    def test_missing_candidate_detected_independently(self):
        self.start('2025-10-17 20:30')
        with patch.object(tm,'decisions',return_value=[]):
            result=self.s02()
        self.assertEqual(result['expectation'],'expected')
        # Remove the event task to reproduce the original empty-candidate incident.
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("DELETE FROM service_tasks WHERE kind='signal'")
        with patch.object(tm,'decisions',return_value=[]):self.assertEqual(self.s02()['result'],'missing_candidate')

    def test_event_at_1755_is_once_and_text_only(self):
        self.start('2025-10-17 17:24')
        self.assertFalse(any(t['kind']=='signal' for t in ap.tasks()))
        tm.advance('2025-10-17 17:25')
        signals=[t for t in ap.tasks() if t['kind']=='signal']
        self.assertEqual(len(signals),1)
        task=signals[0];candidate=task['candidate_id']
        self.assertEqual(task['due_at'],'2025-10-17T17:55:00+08:00')
        self.assertEqual(p.check(candidate)['status'],'not_due')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='cancelled' WHERE kind!='signal'")
        tm.advance('2025-10-17 17:54');self.assertIsNone(ap.claim())
        tm.advance('2025-10-17 17:55');self.assertEqual(ap.claim()['id'],task['id'])
        for skill in ('service-policy','tutor-main','proactive-service','reply-style','celebrate-lesson-completion'):ap.load_skill(skill)
        audit.invoke('daily_context',{},lambda:b.context('2025-10-17'))
        audit.invoke('action_candidates',{},lambda:b.candidates('2025-10-17'))
        audit.invoke('check_proactive',{'candidate_id':candidate},lambda:p.check(candidate))
        with self.assertRaisesRegex(ap.ServiceValidationError,'family_profile'):ap.guard_delivery([candidate])
        audit.invoke('family_profile',{},s.family_profile)
        audit.invoke('learning_query',{'day':'2025-10-17'},lambda:tm.snapshot('2025-10-17'))
        ap.guard_delivery([candidate])
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'isolated-test'}) as sender:
            result=p.deliver([candidate],'侨侨今天完成了第5课，给坚持阅读的行动点个赞。')
            ap.link_delivery(result);w.flush()
            ap.finish(task['id'],'completed','文字表扬已受理，等待平台最终回执')
            self.assertEqual(sender.call_count,1)
            self.assertEqual(self.s02()['result'],'accepted')
            ap.sync();self.assertIsNone(ap.claim())
            self.assertEqual(len([t for t in ap.tasks() if t['kind']=='signal']),1)
            self.assertTrue(p.deliver([candidate],'重复消息')['duplicate'])
            self.assertEqual(sender.call_count,1)

    def test_automatic_off_stops_signal_and_day_change_expires_it(self):
        import test_scheduler
        self.start('2025-10-17 20:30')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='cancelled' WHERE kind!='signal'")
        test_scheduler.set_automatic(False)
        self.assertIsNone(ap.claim())
        tm.advance('2025-10-18 06:29')
        self.assertEqual(next(t for t in ap.tasks('2025-10-17') if t['kind']=='signal')['status'],'expired')

    def test_first_day_is_merged_and_second_week_has_no_s02(self):
        for at,expected in [('2025-10-13 20:30','merged'),('2025-10-20 20:30','not_applicable')]:
            self.start(at)
            self.assertEqual(self.s02()['expectation'],expected)
            self.assertFalse(any(t['kind']=='signal' for t in ap.tasks()))

    def test_birthday_forecast_and_capability_gap_are_explicit(self):
        self.start('2025-10-28 06:30');value=observer.view()
        self.assertTrue(value['birthday_today'])
        birthday=next(a for a in value['actions'] if a['code']=='生日')
        self.assertEqual(birthday['result'],'not_due')
        self.assertEqual(birthday['due_at'],'2025-10-28T09:30:00+08:00')
        self.assertEqual(next(a for a in value['actions'] if a['code']=='S05')['capability'],'connected')

    def test_candidate_without_event_task_is_visible_as_gap(self):
        self.start('2025-10-17 20:30')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("DELETE FROM service_tasks WHERE kind='signal'")
        self.assertEqual(self.s02()['result'],'missing_task')


if __name__=='__main__':unittest.main()
