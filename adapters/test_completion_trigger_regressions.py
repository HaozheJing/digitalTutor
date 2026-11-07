import json,sqlite3,unittest
from contextlib import closing
import test_s11_service as legacy
import fixture_query as fq,sql_tools as s,test_mode as tm,completion_service as cs


class ReturnAfterFirstDayTests(unittest.TestCase):
    setUp=legacy.S11Tests.setUp
    tearDown=legacy.S11Tests.tearDown
    create_source=legacy.S11Tests.create_source
    set_context=legacy.S11Tests.set_context
    fixture=legacy.S11Tests.fixture
    now=legacy.S11Tests.now
    items=legacy.S11Tests.items
    enable=legacy.S11Tests.enable

    def history(self):
        data=self.fixture(count=6,return_lesson=7,stamp='2025-10-21T19:10:00+08:00')
        first=next(x for x in data['learning_daily'] if x['lesson_no']==1)
        first.update(attended_at='2025-10-13T18:50:00+08:00',completed_at='2025-10-13T19:10:00+08:00')
        fq.FILE.write_text(json.dumps(data))
        return data

    def test_first_course_day_has_known_zero_prior_lessons(self):
        self.history();self.now('2025-10-13 19:40')
        first=next(x for x in self.items() if x['evidence']['lesson']['lesson_no']==1)
        self.assertEqual(first['status'],'not_eligible')
        self.assertTrue(first['evidence']['return_to_learning']['coverage_complete'])
        self.assertEqual(first['evidence']['return_to_learning']['before_missing_count'],0)

    def test_five_missed_lessons_after_first_day_completion_create_one_welcome(self):
        self.history();self.now('2025-10-21 19:40');self.enable()
        item=next(x for x in self.items() if x['evidence']['lesson']['lesson_no']==7)
        self.assertEqual(item['status'],'eligible')
        self.assertEqual(item['evidence']['return_to_learning']['before_missing_count'],5)
        self.assertEqual(len(s.read("SELECT id FROM service_tasks WHERE skill='welcome-back-to-reading'")),1)
        self.enable()
        self.assertEqual(len(s.read("SELECT id FROM service_tasks WHERE skill='welcome-back-to-reading'")),1)
        with closing(sqlite3.connect(s.DB)) as c:
            candidates=cs.decisions(c,tm.state(),tm.snapshot(),tm.fixture_data(include_events=False))
        for rule in ('remind-historical-makeup','prevent-lesson-skipping'):
            current=next(x for x in candidates if x['skill']==rule and x['evidence']['event_id']==item['evidence']['event_id'])
            self.assertEqual(current['status'],'merged_into_higher_priority')

    def test_missing_intervening_calendar_still_blocks_return(self):
        data=self.history();data['course_calendar']=[x for x in data['course_calendar'] if x['date']!='2025-10-14']
        fq.FILE.write_text(json.dumps(data));self.now('2025-10-21 19:40');self.enable()
        item=next(x for x in self.items() if x['evidence']['lesson']['lesson_no']==7)
        self.assertEqual(item['status'],'unknown')
        self.assertFalse(item['evidence']['return_to_learning']['coverage_complete'])
        self.assertEqual(s.read("SELECT id FROM service_tasks WHERE skill='welcome-back-to-reading'"),[])


if __name__=='__main__':unittest.main()
