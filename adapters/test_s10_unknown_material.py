"""A new S10 service must not send its PDF with unknown weekly learning facts."""
import json
import sqlite3
import unittest
from contextlib import closing
import fixture_query as fq
import sql_tools as s
import test_mode as tm
import service_workflow as wf
import s10_service
import weekly_materials as wm
import wechat_test as w
import test_weekly_materials as shared


class S10UnknownMaterialTests(unittest.TestCase):
    setUp = shared.WeeklyMaterialsTests.setUp
    tearDown = shared.WeeklyMaterialsTests.tearDown
    create_source = shared.WeeklyMaterialsTests.create_source
    set_context = shared.WeeklyMaterialsTests.set_context
    setup_simulation = shared.WeeklyMaterialsTests.setup_simulation

    def start_workflow(self, zero=False):
        if zero:
            data = json.loads(fq.FILE.read_text())
            data['learning_events'] = []
            for lesson in data['learning_daily']:
                for field in ('completed_at', 'attended_at', 'checked_in_at', 'quiz_submitted_at'):
                    lesson[field] = None
            fq.FILE.write_text(json.dumps(data))
        self.setup_simulation()
        wf.initialize()
        state = tm.state()
        state['service_workflow_mode'] = wf.MODE
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'", (json.dumps(state),))
        self.assertTrue(wf.planning_contract('weekly-learning-review')['required_weekly_material'])

    def remove_calendar_evidence(self):
        data = json.loads(fq.FILE.read_text())
        data['course_calendar'] = [r for r in data['course_calendar'] if r['date'] != '2025-10-14']
        fq.FILE.write_text(json.dumps(data))
        self.assertEqual(s10_service.review(tm.snapshot())['band'], 'unknown')

    def test_unknown_learning_does_not_queue_pdf(self):
        self.start_workflow()
        self.remove_calendar_evidence()
        result = wm.queue()
        self.assertEqual(result['status'], 'deferred')
        self.assertIn('学情', result['reason'])
        self.assertEqual(s.read('SELECT * FROM weekly_material_deliveries'), [])

    def test_facts_lost_after_queue_block_provider_send(self):
        self.start_workflow()
        self.assertEqual(wm.queue()['status'], 'queued')
        self.remove_calendar_evidence()
        results = w.flush()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'blocked')
        self.assertEqual(s.read('SELECT * FROM test_outbox'), [])
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'], 'blocked')
        self.assertEqual(w.flush(), [])

    def test_known_zero_still_receives_weekly_pdf(self):
        self.start_workflow(zero=True)
        self.assertEqual(s10_service.review(tm.snapshot())['band'], 'zero')
        self.assertEqual(wm.queue()['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
