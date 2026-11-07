import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import agent_plan as ap
import fixture_query as fq
import sql_tools as s
import test_fixture_integration as fixtures
import test_mode as tm
import workbench_observer as observer


class DailyHighlightTests(unittest.TestCase):
    setUp = fixtures.FixtureIntegrationTests.setUp
    tearDown = fixtures.FixtureIntegrationTests.tearDown
    create_source = fixtures.FixtureIntegrationTests.create_source
    set_context = fixtures.FixtureIntegrationTests.set_context

    def item(self, ident, day=None):
        value = (observer.daily_regular_overview(day=day) if ident.startswith('completion:') else observer.daily_highlight_overview(day=day))
        return next(x for x in value['selected']['items'] if x['id'] == ident)

    def receipt(self, status, run_id=None, ident='highlight-00001-06'):
        run_id = run_id or tm.state()['run_id']
        asset = next(h for h in tm.fixture_data()['highlight_assets'] if h['id'] == ident)
        candidate_id = 'proactive:fixture:' + run_id + ':' + ident
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',
                      ('highlight-test-' + run_id, '隔离测试消息', status, s.now(), 'isolated-test'))
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',
                      (run_id + ':' + asset['dedupe_key'], candidate_id, 'highlight-test-' + run_id, s.now()))

    def test_full_calendar_and_selection_are_read_only(self):
        tm.activate('2025-10-13 06:30')
        state = tm.state(); fixture_before = fq.FILE.read_bytes()
        with closing(sqlite3.connect(s.DB)) as c:
            before = list(c.iterdump())
        with patch('proactive.refresh', side_effect=AssertionError('refresh writes')), \
             patch.object(ap, 'sync', side_effect=AssertionError('sync writes')):
            value = observer.daily_highlight_overview(day='2025-11-07')
        self.assertEqual(len(value['days']), 35)
        self.assertEqual(value['selected']['date'], '2025-11-07')
        self.assertTrue(value['selected']['is_future'])
        self.assertTrue(value['operator_only']); self.assertTrue(value['read_only'])
        self.assertEqual(set(value['summary']), {'total', *observer.HIGHLIGHT_STATES})
        self.assertEqual(value['summary'], value['days'][0]['counts'])
        self.assertEqual(tm.state(), state)
        self.assertEqual(fq.FILE.read_bytes(), fixture_before)
        with closing(sqlite3.connect(s.DB)) as c:
            self.assertEqual(list(c.iterdump()), before)

    def test_material_available_means_due_not_sent_or_triggered(self):
        tm.activate('2025-10-13 20:30')
        row = self.item('highlight-00001-06')
        self.assertEqual(row['state'], 'due')
        self.assertEqual(row['deliveries'], [])
        self.assertEqual(row['tasks'], [])
        self.assertEqual(row['candidates'], [])

    def test_future_rows_never_borrow_current_receipt(self):
        tm.activate('2025-10-13 20:30')
        self.receipt('sent')
        self.assertEqual(self.item('highlight-00001-06')['state'], 'sent')
        row = self.item('highlight-00001-02', '2025-11-07')
        self.assertEqual(row['state'], 'forecast')
        self.assertEqual(row['deliveries'], [])
        self.assertIn('脚本预览', row['reason'])

    def test_previous_run_receipt_is_not_current_run_evidence(self):
        tm.activate('2025-10-13 20:30')
        self.receipt('sent', run_id='a-different-run')
        self.assertEqual(self.item('highlight-00001-06')['state'], 'due')

    def test_future_ignores_even_a_matching_preexisting_receipt(self):
        tm.activate('2025-10-13 20:30')
        self.receipt('sent', ident='highlight-00001-02')
        row = self.item('highlight-00001-02', '2025-11-07')
        self.assertEqual(row['state'], 'forecast')
        self.assertEqual(row['deliveries'], [])

    def test_accepted_and_unconfirmed_are_never_sent(self):
        tm.activate('2025-10-13 20:30')
        self.receipt('accepted')
        self.assertEqual(self.item('highlight-00001-06')['state'], 'accepted')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE deliveries SET status='unknown'")
        self.assertEqual(self.item('highlight-00001-06')['state'], 'blocked')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE deliveries SET status='sent'")
        self.assertEqual(self.item('highlight-00001-06')['state'], 'sent')

    def test_historical_actual_receipt_remains_on_its_own_day(self):
        tm.activate('2025-10-13 20:30')
        self.receipt('sent')
        tm.advance('2025-10-14 20:30')
        old = self.item('highlight-00001-06', '2025-10-13')
        self.assertEqual(old['state'], 'sent')
        self.assertEqual(old['temporal_scope'], 'past')
        today = observer.daily_highlight_overview()['selected']
        self.assertEqual(today['counts']['sent'], 0)

    def test_past_without_runtime_evidence_stays_projection(self):
        tm.activate('2025-10-20 20:30')
        row = self.item('highlight-00001-06', '2025-10-13')
        self.assertEqual(row['state'], 'forecast')
        self.assertEqual(row['deliveries'], [])

    def test_s04_covers_preset_and_same_event_s02(self):
        raw = tm.fixture_data()
        template = dict(raw['highlight_assets'][0])
        raw['highlight_assets'].extend([
            {**template, 'id': 'highlight-00001-09', 'medal_name': '三日完课组合',
             'candidate_at': '2025-10-15T19:00:00+08:00', 'dedupe_key': 'fixture-highlight-09',
             'expires_at': '2025-10-15T23:59:00+08:00'},
            {**template, 'id': 'highlight-00001-04', 'medal_name': '首次全对', 'candidate_at': None,
             'trigger_status': 'covered_by_combination', 'covered_by': 'highlight-00001-09'},
        ])
        fq.FILE.write_text(json.dumps(raw), encoding='utf8')
        tm.activate('2025-10-15 20:30'); ap.enable()
        value = observer.daily_highlight_overview()['selected']
        rows = {r['id']: r for r in value['items']}
        self.assertEqual(rows['highlight-00001-09']['state'], 'triggered')  # Interface asset remains independent from model action.
        self.assertFalse(any(key.startswith('completion:') for key in rows))
        rows.update({r['id']:r for r in observer.daily_regular_overview()['selected']['items']})
        self.assertEqual(rows['completion:S02:L2-003']['state'], 'covered')
        self.assertEqual(rows['completion:S04:L2-003']['state'], 'covered')  # Existing interface streak asset owns the delivery.
        self.assertFalse(any(t['skill']=='celebrate-learning-streak' for t in ap.tasks()))
        self.assertEqual(rows['completion:S04:L2-003']['due_at'], '2025-10-15T17:55:00+08:00')
        self.assertEqual(rows['highlight-00001-04']['covered_by'], 'highlight-00001-09')

    def test_task_block_and_delivery_evidence_are_distinct(self):
        tm.activate('2025-10-17 20:30'); ap.enable()
        task = next(t for t in ap.tasks() if t['kind'] == 'signal' and t['skill'] == 'celebrate-lesson-completion')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE service_tasks SET status='blocked',result=? WHERE id=?",
                      (json.dumps({'reason': '缺少课程正文'}), task['id']))
        row = self.item('completion:S02:L2-005')
        self.assertEqual(row['state'], 'blocked')
        self.assertIn('缺少课程正文', row['reason'])
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',
                      ('highlight-task-receipt', '隔离文字表扬', 'sent', s.now(), 'isolated-test'))
            c.execute('INSERT INTO service_links VALUES(?,?)', (task['id'], 'highlight-task-receipt'))
        self.assertEqual(self.item('completion:S02:L2-005')['state'], 'sent')

    def test_legitimate_no_contact_is_not_an_exception(self):
        tm.activate('2025-10-17 20:30'); ap.enable()
        task = next(t for t in ap.tasks() if t['kind'] == 'signal' and t['skill'] == 'celebrate-lesson-completion')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE service_tasks SET status='no_contact',result=? WHERE id=?",
                      (json.dumps({'reason': '本次不重复联系'}), task['id']))
        row = self.item('completion:S02:L2-005')
        self.assertEqual(row['state'], 'skipped')
        self.assertIn('本次不重复联系', row['reason'])

    def test_selected_fixture_changes_expected_completion_nodes(self):
        tm.activate('2025-10-17 06:30')
        raw = tm.fixture_data()
        for lesson in raw['learning_daily']:
            if lesson['date'] == '2025-10-17':
                lesson['completed_at'] = None
        name = 'acceptance-s07-highlight-overview-test.json'
        (fq.FILE.parent / name).write_text(json.dumps(raw), encoding='utf8')
        state = tm.state(); state['learning_scenario'] = name
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'", (s.encoded(state),))
        self.assertFalse(any(x['id'] == 'completion:S02:L2-005'
                             for x in observer.daily_highlight_overview()['selected']['items']))

    def test_contact_pause_is_visible_without_creating_a_task(self):
        tm.activate('2025-10-13 20:30')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',
                      ('2025-10-13', 'isolated', '不要联系', s.now()))
        row = self.item('highlight-00001-06')
        self.assertEqual(row['state'], 'blocked')
        self.assertEqual(row['tasks'], [])

    def test_invalid_date_range_is_rejected_without_clock_change(self):
        before = tm.state()
        for args in ({'day': '2025-12-01'}, {'start': '2025-10-15', 'end': '2025-10-13'},
                     {'day': '2025-10-99'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                observer.daily_highlight_overview(**args)
        self.assertEqual(tm.state(), before)


if __name__ == '__main__':
    unittest.main()
