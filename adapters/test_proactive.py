import datetime as dt
import json
import sqlite3
import unittest
from contextlib import closing
import test_live as fixture
import business
import proactive as p
import sql_tools as s

class ProactiveTests(unittest.TestCase):
    create_source=fixture.LiveTests.create_source
    set_context=fixture.LiveTests.set_context
    tearDown=fixture.LiveTests.tearDown
    def setUp(self):
        fixture.LiveTests.setUp(self);business.initialize()
        self.day='2026-10-20'
    def fact(self,kind,body,day=None,scope='course-001'):
        day=day or self.day
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT OR REPLACE INTO proactive_facts VALUES(?,?,?,?,?,?,?)',(kind,scope,day,json.dumps(body),self.day+'T00:00:00+08:00','2026-12-31','sql_mock'))
    def learning(self,start,end,completed=True):
        start=dt.date.fromisoformat(start);end=dt.date.fromisoformat(end)
        for i in range((end-start).days+1):
            day=(start+dt.timedelta(days=i)).isoformat()
            self.fact('learning_day',{'expected':1,'completed':int(completed),'coverage_complete':True,'lesson_titles':['阅读课'],'completed_at':day+'T18:00:00+08:00' if completed else None,'missing_lesson_ids':[] if completed else ['lesson-'+day]},day,day)
    def decision(self,name):
        return next(x for x in p.refresh(self.day) if x['rule']==name)
    def at(self,clock='20:30'):return dt.datetime.fromisoformat(self.day+'T'+clock+':00+08:00')
    def seed(self,kind):
        if kind=='birthday':
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("INSERT INTO family VALUES('live-family','小禾','2018-10-20','sql_mock')")
        elif kind=='calendar':self.fact('calendar',{'title':'阅读节','kind':'holiday','applicable':True},scope='reading-day')
        elif kind in ['completed','first_day']:
            self.learning(self.day,self.day)
            self.fact('enrollment',{'start_date':self.day if kind=='first_day' else '2026-10-19','active':True,'first_lesson':{'id':'lesson-first','completed':True,'completed_at':self.day+'T18:00:00+08:00'}})
        elif kind in ['streak3','streak21']:
            count=3 if kind=='streak3' else 21
            begin=(dt.date.fromisoformat(self.day)-dt.timedelta(days=count-1)).isoformat()
            self.learning(begin,self.day);self.fact('enrollment',{'start_date':begin,'active':True},begin)
        elif kind=='seven':
            self.learning('2026-10-16',self.day);self.fact('seven_day_window',{'start':'2026-10-14','end':self.day})
            self.learning('2026-10-14','2026-10-15',False)
        elif kind=='week2':
            self.fact('enrollment',{'start_date':'2026-10-07','active':True},'2026-10-07');self.learning('2026-10-14',self.day)
        elif kind=='book':
            self.fact('book',{'title':'狐狸爸爸','scope_confirmed':True,'coverage_complete':True,'shelf_lit':True,'completed_on':self.day,'lessons':[{'id':'lesson1','completed':True}]},scope='fox-v1')
        elif kind=='perfect':
            self.fact('quiz',{'history_complete':True,'attempts':[{'day':self.day,'total':1,'correct':1,'support':'有提示','question_answers':[{'question':'主角是谁','answer':'狐狸爸爸','correct':True}]}]})
        elif kind in ['rank','tier']:
            self.fact('ranking',{'final':True,'ranking_type':'quiz_accuracy','rank':1,'published_on':self.day,'same_metric':True,'higher_tier_is_better':True,'previous_tier':1,'current_tier':2},scope='week-43')
        elif kind=='expression':
            self.fact('expression',{'teacher_selected':True,'transcript_verified':True,'quote':'我也想帮小动物','lesson_context':'孩子在狐狸爸爸课程讨论后的原话'})
        elif kind in ['weekly','weekend','last_gap']:
            self.day={'weekly':'2026-10-24','weekend':'2026-10-23','last_gap':'2026-10-23'}[kind]
            self.learning('2026-10-19','2026-10-25')
            if kind!='weekly':self.learning(self.day,self.day,False)
        elif kind=='monthly':
            self.day='2026-10-31';self.learning('2026-10-01',self.day)
        elif kind=='rotation':
            self.day='2026-10-23';self.fact('rotation',{'week_start':'2026-10-19','form':'阅读感受交流','lesson_material':'狐狸爸爸帮助动物的故事'})
        elif kind=='decision':
            self.fact('decision',{'confirmed':True,'review_on':self.day,'learning_evidence':[{'book':'狐狸爸爸','completed_lessons':1}]})
    def test_all_nineteen_rules_have_positive_evidence_path(self):
        for name,_,kind,*_ in p.RULES:
            with self.subTest(rule=name):
                with closing(sqlite3.connect(s.DB)) as c,c:
                    for table in ['family','proactive_facts','proactive_candidates','plan_candidates']:c.execute('DELETE FROM '+table)
                self.day='2026-10-20';self.seed(kind)
                self.assertIn(self.decision(name)['status'],['eligible','merged_into_higher_priority'])
    def test_missing_data_does_not_trigger(self):
        self.assertTrue(all(x['status'] in ['unknown','not_eligible'] for x in p.refresh(self.day)))

    def test_weekly_interaction_needs_material_but_no_configured_angle(self):
        self.day='2026-10-23'
        self.fact('rotation',{'week_start':'2026-10-19','lesson_material':'verified course material'})
        first=self.decision('invite-reading-interaction')
        self.assertEqual(first['status'],'eligible')
        self.fact('rotation',{'week_start':'2026-10-19','lesson_material':'verified course material','form':'legacy value'})
        second=self.decision('invite-reading-interaction')
        self.assertEqual(second['status'],'eligible')
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(first['dedupe_key'],second['dedupe_key'])
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',
                      (first['dedupe_key'],first['id'],'existing-delivery',s.now()))
        self.assertEqual(self.decision('invite-reading-interaction')['status'],'already_delivered')

    def test_weekly_interaction_does_not_accept_missing_material_or_wrong_week(self):
        self.day='2026-10-23'
        for body in [{'week_start':'2026-10-19'},
                     {'week_start':'2026-10-12','lesson_material':'old material'}]:
            self.fact('rotation',body)
            self.assertNotEqual(self.decision('invite-reading-interaction')['status'],'eligible')
    def test_unknown_learning_day_breaks_streak(self):
        self.seed('streak3')
        self.fact('learning_day',{'expected':1,'completed':1,'coverage_complete':False},'2026-10-19','2026-10-19')
        self.assertEqual(self.decision('celebrate-learning-streak')['status'],'unknown')
    def test_changed_signal_blocks_delivery_and_removes_candidate(self):
        self.seed('calendar');item=self.decision('care-calendar-day')
        self.fact('calendar',{'title':'阅读节','kind':'holiday','applicable':False},scope='reading-day')
        self.assertEqual(p.deliver([item['id']],'阅读节快乐',self.at())['status'],'skipped')
        self.assertFalse(any(x['status']=='eligible' for x in p.refresh(self.day)))
    def test_duplicate_delivery_is_idempotent(self):
        self.seed('birthday');item=self.decision('celebrate-important-day')
        self.assertEqual(p.deliver([item['id']],'生日快乐',self.at())['status'],'accepted')
        self.assertTrue(p.deliver([item['id']],'另一条',self.at())['duplicate'])
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)
    def test_pause_and_expiry_block_delivery(self):
        self.seed('birthday');item=self.decision('celebrate-important-day')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',(self.day,'message','今天别催',s.now()))
        self.assertEqual(p.deliver([item['id']],'生日快乐',self.at())['status'],'skipped')
        self.assertEqual(p.deliver([item['id']],'生日快乐',self.at('21:30'))['status'],'expired')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])
    def test_chat_deferral_is_persistent(self):
        self.seed('birthday');item=self.decision('celebrate-important-day')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',('new-parent','passive','{}','pending',s.now(),0,None,None,None))
        self.assertEqual(p.deliver([item['id']],'生日快乐',self.at())['status'],'deferred')
        self.assertEqual(len(s.read("SELECT * FROM proactive_pending WHERE status='pending'")),1)
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])
    def test_frequency_exempts_s01_and_passive_reply(self):
        self.seed('birthday');item=self.decision('celebrate-important-day')
        with closing(sqlite3.connect(s.DB)) as c,c:
            for key in ['live-family:S01:'+self.day,'active:another','reply:parent']:
                c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,'通知','mock_sent',self.day+'T10:00:00+08:00','mock_channel'))
        self.assertEqual(p.deliver([item['id']],'生日快乐',self.at())['status'],'accepted')
    def test_two_optional_messages_block_more_but_s02_is_exempt(self):
        self.seed('birthday');item=self.decision('celebrate-important-day')
        with closing(sqlite3.connect(s.DB)) as c,c:
            for key in ['active:one','active:two']:
                c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,'通知','mock_sent',self.day+'T10:00:00+08:00','mock_channel'))
        self.assertEqual(p.deliver([item['id']],'生日快乐',self.at())['status'],'skipped')
        self.seed('completed');item=self.decision('celebrate-lesson-completion')
        self.assertEqual(p.deliver([item['id']],'完成了今天课程',self.at())['status'],'accepted')
    def test_completion_requires_first_week_and_exact_event_time(self):
        self.seed('completed')
        item=self.decision('celebrate-lesson-completion')
        self.assertEqual(item['time_window']['earliest'],self.day+'T18:30:00+08:00')
        self.assertEqual(p.check(item['id'],self.at('18:29'))['status'],'not_due')
        self.fact('enrollment',{'start_date':'2026-10-01','active':True})
        self.assertEqual(self.decision('celebrate-lesson-completion')['status'],'not_eligible')
        self.fact('enrollment',{'start_date':'2026-10-19','active':True})
        self.fact('learning_day',{'expected':1,'completed':1,'coverage_complete':True},scope=self.day)
        self.assertEqual(self.decision('celebrate-lesson-completion')['status'],'unknown')
    def test_first_lesson_does_not_require_all_lessons_complete(self):
        self.seed('first_day')
        self.fact('learning_day',{'expected':2,'completed':1,'coverage_complete':True},scope=self.day)
        item=self.decision('highlight-course-start')
        self.assertEqual(item['status'],'eligible')
        self.assertEqual(item['time_window']['earliest'],self.day+'T18:00:00+08:00')
        self.fact('enrollment',{'start_date':self.day,'active':True})
        self.assertEqual(self.decision('highlight-course-start')['status'],'unknown')
    def test_already_delivered_first_highlight_still_covers_plain_praise(self):
        self.seed('first_day')
        item=self.decision('highlight-course-start')
        self.assertEqual(p.deliver([item['id']],'首课完成啦',self.at('18:00'))['status'],'accepted')
        self.assertEqual(self.decision('celebrate-lesson-completion')['status'],'merged_into_higher_priority')
    def test_first_lesson_conflicting_completion_is_unknown(self):
        self.seed('first_day')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO lessons VALUES(?,?,?,?,?,?,?,?,?)',
                ('lesson-first',self.day,'首课','正文',0,s.now(),'sql_mock','binding','completion'))
        self.assertEqual(self.decision('highlight-course-start')['status'],'unknown')
    def test_weekend_missing_lesson_ids_blocks_unverifiable_reminder(self):
        self.seed('weekend')
        self.fact('learning_day',{'expected':1,'completed':0,'coverage_complete':True},scope=self.day)
        item=self.decision('plan-weekend-makeup')
        self.assertEqual(item['time_window']['earliest'],self.day+'T20:00:00+08:00')
        self.assertEqual(p.deliver([item['id']],'补读建议',self.at())['status'],'blocked')
    def test_same_lesson_weekly_cap_is_shared_across_reminder_actions(self):
        self.seed('weekend');item=self.decision('plan-weekend-makeup')
        lesson='lesson-'+self.day
        with closing(sqlite3.connect(s.DB)) as c,c:
            for i in range(2):
                old={'rule':'close-week-reading-gap','evidence':{'missing':[{'missing_lesson_ids':[lesson]}]}}
                c.execute('INSERT INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',
                    ('old-'+str(i),self.day,'close-week-reading-gap','week',json.dumps(old),'delivered',s.now()))
                c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',
                    ('old-dedupe-'+str(i),'old-'+str(i),'delivery-'+str(i),self.day+'T10:00:00+08:00'))
        result=p.deliver([item['id']],'再次提醒同一节',self.at())
        self.assertEqual(result['status'],'skipped')
        self.assertIn('本周已提醒两次',result['reason'])
    def test_first_perfect_requires_complete_history_and_matching_answers(self):
        self.seed('perfect')
        for payload in [
            {'history_complete':False,'attempts':[{'day':self.day,'total':1,'correct':1,'question_answers':[{'question':'题','answer':'答','correct':True}]}]},
            {'history_complete':True,'attempts':[{'day':self.day,'total':2,'correct':2,'question_answers':[{'question':'题','answer':'答','correct':True}]}]}
        ]:
            self.fact('quiz',payload)
            self.assertNotEqual(self.decision('highlight-first-perfect-answer')['status'],'eligible')

    def test_weekly_lesson_cap_counts_all_six_reminder_actions(self):
        self.seed('weekend');item=self.decision('plan-weekend-makeup')
        lesson='lesson-'+self.day
        rules=['remind-historical-makeup','support-zero-completion','prevent-lesson-skipping',
               'rebuild-reading-rhythm','close-week-reading-gap','plan-weekend-makeup']
        for rule in rules:
            with self.subTest(rule=rule):
                with closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute("DELETE FROM proactive_receipts WHERE dedupe_key LIKE 'history-%'")
                    c.execute("DELETE FROM proactive_candidates WHERE id LIKE 'history-%'")
                    for i,prior_rule in enumerate([rule,'close-week-reading-gap']):
                        old={'rule':prior_rule,'evidence':{'missing':[{'missing_lesson_ids':[lesson]}]}}
                        c.execute('INSERT INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',
                            ('history-'+str(i),self.day,prior_rule,'week',json.dumps(old),'delivered',s.now()))
                        c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',
                            ('history-'+str(i),'history-'+str(i),'receipt-'+str(i),self.day+'T08:00:00+08:00'))
                result=p.deliver([item['id']],'不应第三次提醒同一课',self.at())
                self.assertEqual(result['status'],'skipped')
                self.assertIn('本周已提醒两次',result['reason'])
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_new_reminder_history_requires_stable_lesson_ids(self):
        for missing in [None,'lesson-id',[None],[{'missing_lesson_ids':'lesson-id'}],[{'title':'阅读课'}]]:
            with self.subTest(missing=missing):
                self.assertIsNone(p.reminder_lessons({'rule':'remind-historical-makeup','evidence':{'missing':missing}}))
    def test_weekly_report_needs_full_week_not_sample(self):
        self.day='2026-10-24';self.learning('2026-10-23',self.day)
        self.assertEqual(self.decision('weekly-learning-review')['status'],'unknown')
    def test_seven_day_window_must_be_explicit(self):
        self.learning('2026-10-14',self.day)
        self.assertEqual(self.decision('highlight-seven-day-completion')['status'],'unknown')
    def test_plan_references_match_business_projection(self):
        self.seed('birthday')
        item=self.decision('celebrate-important-day')
        result=business.candidates(self.day)
        self.assertEqual(next(x for x in result['actions'] if x.get('rule')=='celebrate-important-day')['candidate_id'],business.token(item['id']))
    def test_streak_reward_does_not_repeat_after_threshold(self):
        self.seed('streak3');item=self.decision('celebrate-learning-streak')
        p.deliver([item['id']],'坚持了三天',self.at())
        self.day='2026-10-21';self.learning(self.day,self.day)
        self.assertEqual(self.decision('celebrate-learning-streak')['status'],'already_delivered')
    def test_conflicting_today_feeds_do_not_create_achievement(self):
        self.seed('completed')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO lessons VALUES(?,?,?,?,?,?,?,?,?)',('lesson',self.day,'阅读课','正文',0,s.now(),'sql_mock','binding','completion'))
        self.assertEqual(self.decision('celebrate-lesson-completion')['status'],'unknown')
    def test_concurrent_delivery_only_writes_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.seed('birthday');item=self.decision('celebrate-important-day')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:p.deliver([item['id']],'生日快乐',self.at()),range(2)))
        self.assertEqual(sum(bool(x.get('duplicate')) for x in results),1)
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)
    def test_each_rule_rechecks_disappeared_evidence_before_sending(self):
        for name,_,kind,*_ in p.RULES:
            with self.subTest(rule=name):
                with closing(sqlite3.connect(s.DB)) as c,c:
                    for table in ['family','proactive_facts','proactive_candidates','plan_candidates']:c.execute('DELETE FROM '+table)
                self.day='2026-10-20';self.seed(kind);item=self.decision(name)
                with closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute('DELETE FROM family');c.execute('DELETE FROM proactive_facts')
                self.assertEqual(p.deliver([item['id']],'不应发送',self.at())['status'],'skipped')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])
    def test_resumed_delivery_closes_pending_work(self):
        self.seed('birthday');item=self.decision('celebrate-important-day')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)',('parent','passive','{}','pending',s.now(),0,None,None,None))
        p.deliver([item['id']],'生日快乐',self.at())
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE events SET status='done'")
        self.assertEqual(p.deliver([item['id']],'生日快乐',self.at('19:33'))['status'],'accepted')
        self.assertEqual(s.read('SELECT status FROM proactive_pending')[0]['status'],'done')
    def test_s01_waits_for_contact_interval_without_immediate_retry_loop(self):
        self.day=p.now().date().isoformat()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO lessons VALUES(?,?,?,?,?,?,?,?,?)',('lesson',self.day,'阅读课','正文',0,s.now(),'sql_mock','binding','completion'))
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('active:recent','祝福','mock_sent',p.now().isoformat(),'mock_channel'))
        self.assertEqual(s.deliver(self.day,'今日阅读')['status'],'deferred')
        pending=json.loads(s.read("SELECT payload FROM task_state WHERE id=?",('S01:'+self.day,))[0]['payload'])
        self.assertGreater(pending['not_before'],p.now().timestamp()+3500)

if __name__=='__main__':unittest.main()
