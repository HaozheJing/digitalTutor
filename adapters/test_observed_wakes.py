import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as base
import test_mode as tm,sql_tools as s,agent_plan as ap,service_workflow as wf,fixture_query as fq

class ObservedWakesTests(unittest.TestCase):
    setUp=base.FixtureIntegrationTests.setUp
    tearDown=base.FixtureIntegrationTests.tearDown
    create_source=base.FixtureIntegrationTests.create_source
    set_context=base.FixtureIntegrationTests.set_context
    def start(self,at,automatic=True):
        with patch.object(wf,'default_mode',return_value=True):tm.activate(at,automatic)
        ap.enable();wf.sync()
    def tasks(self,skill):return s.read('SELECT * FROM service_tasks WHERE run_id=? AND skill=?',(tm.state()['run_id'],skill))

    def test_same_due_actions_follow_existing_candidate_priority(self):
        self.start('2025-10-24T20:00:00+08:00')
        target=self.tasks('plan-weekend-makeup')[0]
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='done' WHERE id<>?",(target['id'],))
            item={'id':'lower-priority','day':'2025-10-24','rule':'invite-reading-interaction','scope':'week','priority':35}
            c.execute('INSERT INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',(item['id'],item['day'],item['rule'],item['scope'],s.encoded(item),'eligible',s.now()))
            import completion_planner as cp
            cp.insert(c,tm.state(),'older-interaction','interaction',item['rule'],target['due_at'],target['expires_at'],item['id'],'已等待的较低优先级互动')
            c.execute("UPDATE service_tasks SET created_at='2000-01-01' WHERE id='older-interaction'")
        self.assertEqual(ap.claim()['id'],target['id'])

    def test_failed_daily_plan_does_not_lose_fixed_s01(self):
        self.start('2025-10-20T06:30:00+08:00')
        self.assertFalse(self.tasks('remind-reading'))
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='blocked' WHERE kind='plan'")
        wf.sync();rows=self.tasks('remind-reading');self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['due_at'],'2025-10-20T06:30:00+08:00')
        wf.sync();self.assertEqual(len(self.tasks('remind-reading')),1)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='no_contact' WHERE id=?",(rows[0]['id'],))
        wf.sync();self.assertEqual(self.tasks('remind-reading')[0]['status'],'no_contact')

    def test_failed_daily_plan_s01_respects_pause_and_expiry(self):
        self.start('2025-10-20T06:30:00+08:00')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='blocked' WHERE kind='plan'")
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-20','m','今天别联系',s.now()))
        wf.sync();self.assertFalse(self.tasks('remind-reading'))
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('DELETE FROM contact_constraints')
        tm.advance('2025-10-20T23:59:01+08:00');wf.sync();self.assertFalse(self.tasks('remind-reading'))

    def test_afternoon_makeup_creates_s08_at_eighteen_despite_morning_ineligibility(self):
        data=json.loads(fq.FILE.read_text());lesson=next(x for x in data['learning_daily'] if x['lesson_no']==9)
        lesson['completed_at']='2025-10-24T12:00:00+08:00'
        for event in data['learning_events']:
            if event.get('lesson_id')==lesson['id'] and event['kind']=='completion':event['occurred_at']=lesson['completed_at']
        fq.FILE.write_text(json.dumps(data));self.start('2025-10-24T06:30:00+08:00')
        self.assertFalse(self.tasks('close-week-reading-gap'))
        with closing(sqlite3.connect(s.DB)) as c:
            entry=next(x for x in wf.catalog(c,tm.state()) if x['skill']=='close-week-reading-gap')
            self.assertEqual(entry['opportunities'][0]['status'],'not_eligible')
        tm.advance('2025-10-24T17:59:59+08:00');self.assertFalse(self.tasks('close-week-reading-gap'))
        tm.advance('2025-10-24T18:00:00+08:00');rows=self.tasks('close-week-reading-gap');self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['due_at'],'2025-10-24T18:00:00+08:00')
        wf.sync();wf.sync();self.assertEqual(len(self.tasks('close-week-reading-gap')),1)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='cancelled' WHERE id=?",(rows[0]['id'],))
        wf.sync();self.assertEqual(self.tasks('close-week-reading-gap')[0]['status'],'cancelled')

    def test_new_actual_highlight_wakes_incremental_planning_once_without_sending(self):
        self.start('2025-10-13T18:59:59+08:00')
        self.assertFalse(s.read("SELECT * FROM service_tasks WHERE id LIKE 'service-observed-%'"))
        tm.advance('2025-10-13T19:00:00+08:00');rows=s.read("SELECT * FROM service_tasks WHERE id LIKE 'service-observed-%'")
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['kind'],'plan');self.assertEqual(rows[0]['due_at'],'2025-10-13T19:00:00+08:00')
        wf.sync();wf.sync();self.assertEqual(len(s.read("SELECT * FROM service_tasks WHERE id LIKE 'service-observed-%'")),1)
        self.assertFalse(s.read('SELECT * FROM deliveries'))

    def test_manual_mode_does_not_auto_create_fallback_or_observed_wake(self):
        self.start('2025-10-24T20:00:00+08:00',False)
        self.assertFalse(self.tasks('close-week-reading-gap'));self.assertFalse(self.tasks('plan-weekend-makeup'))
        self.assertFalse(s.read("SELECT * FROM service_tasks WHERE id LIKE 'service-observed-%'"))

    def test_s05_weekly_key_survives_later_day_and_terminal_result(self):
        self.start('2025-11-04T19:00:00+08:00');rows=self.tasks('support-zero-completion');self.assertEqual(len(rows),1)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='no_contact' WHERE id=?",(rows[0]['id'],))
        tm.advance('2025-11-05T19:00:00+08:00');self.assertEqual(len(self.tasks('support-zero-completion')),1)

    def test_pause_and_expiry_do_not_create_late_s09(self):
        self.start('2025-10-24T19:59:59+08:00')
        self.assertFalse(self.tasks('plan-weekend-makeup'))
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-24','m','今天别联系',s.now()))
        tm.advance('2025-10-24T20:00:00+08:00');self.assertFalse(self.tasks('plan-weekend-makeup'))
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('DELETE FROM contact_constraints')
        tm.advance('2025-10-24T23:59:01+08:00');self.assertFalse(self.tasks('plan-weekend-makeup'))

    def test_observed_streak_card_is_covered_by_existing_s04_receipt(self):
        self.start('2025-10-15T17:55:00+08:00')
        state=tm.state();key=state['run_id']+':S04:test';candidate='prior-s04'
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',(candidate,'2025-10-15','celebrate-learning-streak','2025-10-13',json.dumps({'evidence':{'event_id':'actual-completion'}}),'eligible',s.now()))
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(key,candidate,'prior-delivery',state['as_of']))
        tm.advance('2025-10-15T19:00:00+08:00')
        with closing(sqlite3.connect(s.DB)) as c:
            card=next(x for x in tm.decisions(c,'2025-10-15') if x.get('delivery_owner')=='host_highlights' and x['rule']=='celebrate-learning-streak')
        self.assertEqual(card['status'],'merged_into_higher_priority')

    def test_first_completion_remains_actionable_without_visible_highlight(self):
        import completion_service
        self.start('2025-10-13T17:55:00+08:00')
        state=tm.state()
        with closing(sqlite3.connect(s.DB)) as c:
            item=next(x for x in completion_service.decisions(c,state,tm.snapshot(),tm.fixture_data(state,include_events=False)) if x['skill']=='celebrate-lesson-completion')
        self.assertEqual(item['status'],'eligible')
        self.assertEqual(item['time_window']['earliest'],state['as_of'])

    def test_late_first_card_does_not_duplicate_first_lesson_praise(self):
        self.start('2025-10-13T17:55:00+08:00');state=tm.state()
        with closing(sqlite3.connect(s.DB)) as c,c:
            body={'evidence':{'event_id':'first-event','lesson':{'lesson_no':1}}}
            c.execute('INSERT INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',('s02-first','2025-10-13','celebrate-lesson-completion','first',json.dumps(body),'delivered',s.now()))
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(state['run_id']+':s02-first','s02-first','first-praise',state['as_of']))
        tm.advance('2025-10-13T19:00:00+08:00')
        with closing(sqlite3.connect(s.DB)) as c:
            card=next(x for x in tm.decisions(c,'2025-10-13') if x['rule']=='highlight-course-start')
        self.assertEqual(card['status'],'merged_into_higher_priority')
        self.assertFalse(self.tasks('highlight-course-start'))

    def test_s04_only_covers_its_own_event_not_later_first_week_praise(self):
        import completion_service as cs
        self.start('2025-10-15T17:55:00+08:00');state=tm.state()
        with closing(sqlite3.connect(s.DB)) as c,c:
            items=cs.decisions(c,state,tm.snapshot(),tm.fixture_data(state,include_events=False))
            streak=next(x for x in items if x['skill']=='celebrate-learning-streak')
            c.execute('INSERT OR REPLACE INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',(streak['id'],streak['day'],streak['rule'],streak['scope'],s.encoded(streak),'delivered',s.now()))
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(streak['dedupe_key'],streak['id'],'streak-sent',state['as_of']))
            items=cs.decisions(c,state,tm.snapshot(),tm.fixture_data(state,include_events=False))
            self.assertEqual(next(x for x in items if x['skill']=='celebrate-lesson-completion')['status'],'merged_into_higher_priority')
        for day in ['2025-10-16','2025-10-17']:
            tm.advance(day+'T17:55:00+08:00');state=tm.state()
            with closing(sqlite3.connect(s.DB)) as c:items=cs.decisions(c,state,tm.snapshot(),tm.fixture_data(state,include_events=False))
            self.assertEqual(next(x for x in items if x['skill']=='celebrate-lesson-completion')['status'],'eligible')
            self.assertEqual(next(x for x in items if x['skill']=='celebrate-learning-streak')['status'],'already_delivered')

if __name__=='__main__':unittest.main()
