import copy
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import agent_plan as ap
import observability as audit
import proactive
import s07_service
import s08_service as closing_week
import sql_tools as s
import test_fixture_integration as fixtures
import test_mode as tm


class S08IntegrationTests(unittest.TestCase):
    setUp = fixtures.FixtureIntegrationTests.setUp
    tearDown = fixtures.FixtureIntegrationTests.tearDown
    create_source = fixtures.FixtureIntegrationTests.create_source
    set_context = fixtures.FixtureIntegrationTests.set_context

    def snapshot(self):
        snap = copy.deepcopy(tm.snapshot())
        day = snap['as_of'][:10]
        for row in snap['lessons_as_of']:
            if row['scheduled_date'] >= snap['weekly_learning']['current_week']['start']:
                row['status'] = 'in_progress' if row['scheduled_date'] == day else 'completed'
                row['completed_at'] = None if row['scheduled_date'] == day else row['scheduled_date'] + 'T18:00:00+08:00'
        snap['lessons_as_of'][0]['status'] = 'absent'
        snap['lessons_as_of'][0]['completed_at'] = None
        return self.recount(snap)

    def recount(self, snap):
        calendar = json.loads(fixtures.fq.FILE.read_text(encoding='utf-8'))['course_calendar']
        snap['weekly_learning'] = s07_service.weekly_learning(snap, calendar)
        return snap

    def begin(self):
        tm.activate('2025-10-24 19:00', True)
        ap.enable()
        task = next(r for r in ap.tasks() if r['kind'] == 's08')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE service_tasks SET status='running' WHERE id=?", (task['id'],))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)", (json.dumps(task['id']),))
        return task

    def test_every_friday_wakes_without_prefiltering_and_only_friday(self):
        for day in ['2025-10-17', '2025-10-24', '2025-10-31', '2025-11-07']:
            tm.activate(day + ' 19:00', False)
            ap.enable()
            tasks = [r for r in ap.tasks() if r['kind'] == 's08']
            self.assertEqual(len(tasks), 1)
            with self.assertRaises(ap.ServiceValidationError):ap.prompt(tasks[0]['id'])
            import console_control
            console_control.execute(tasks[0]['id'])
            self.assertEqual(ap.claim()['id'],tasks[0]['id'])
            prompt = ap.prompt(tasks[0]['id'])['prompt']
            self.assertIn('learning_query', prompt)
            self.assertIn('close-week-reading-gap', prompt)
            self.assertIn('facts.learning_context', prompt)
            ap.finish(tasks[0]['id'],'blocked','仅验证宿主装配，不执行外发')
            ap.sync()
            self.assertEqual(len([r for r in ap.tasks() if r['kind'] == 's08']), 1)
            with closing(sqlite3.connect(s.DB)) as c, c:
                c.execute("UPDATE service_tasks SET status='cancelled' WHERE kind='plan'")
            self.assertIsNone(ap.claim())  # Automatic execution remains off.
        with self.assertRaisesRegex(ValueError, 'outside the first-month'):
            tm.activate('2025-11-14 19:00', False)
        tm.activate('2025-11-06 19:00', False)
        ap.enable()
        self.assertFalse(any(r['kind'] == 's08' for r in ap.tasks()))

    def test_only_today_missing_and_incomplete_evidence_stays_unknown(self):
        tm.activate('2025-10-24 19:00', False)
        snap = self.snapshot()
        self.assertEqual(closing_week.eligibility(snap['weekly_learning'], '2025-10-24')[0], 'eligible')
        for missing in [set(), {'2025-10-23'}, {'2025-10-23', '2025-10-24'}]:
            altered = copy.deepcopy(snap)
            for row in altered['lessons_as_of']:
                if row['scheduled_date'] >= '2025-10-20':
                    row['status'] = 'in_progress' if row['scheduled_date'] in missing else 'completed'
            self.assertEqual(closing_week.eligibility(self.recount(altered)['weekly_learning'], '2025-10-24')[0], 'not_eligible')
        for change in ['remove', 'duplicate', 'unknown']:
            altered = copy.deepcopy(snap)
            row = next(r for r in altered['lessons_as_of'] if r['scheduled_date'] == '2025-10-23')
            if change == 'remove':
                altered['lessons_as_of'].remove(row)
            elif change == 'duplicate':
                altered['lessons_as_of'].append(copy.deepcopy(row))
            else:
                row['status'] = 'unknown'
            self.assertEqual(closing_week.eligibility(self.recount(altered)['weekly_learning'], '2025-10-24')[0], 'unknown')
        # Other weeks' unfinished lessons do not change the current week's gap.
        self.assertTrue(any(r['status'] != 'completed' and r['scheduled_date'] < '2025-10-20' for r in snap['lessons_as_of']))

    def test_send_rechecks_completion_time_window_length_and_dedupes(self):
        tm.activate('2025-10-24 19:00', False)
        ap.enable()
        snap = self.snapshot()
        with patch.object(tm, 'snapshot', return_value=snap):
            candidate = next(r for r in proactive.refresh('2025-10-24') if r['rule'] == closing_week.RULE)
            today = next(r for r in snap['weekly_learning']['current_week']['lessons'] if r['scheduled_date'] == '2025-10-24')
            today['status'] = 'completed'
            self.assertEqual(proactive.deliver([candidate['id']], '今天可以轻松收尾。')['status'], 'skipped')
            today['status'] = 'in_progress'
            self.assertEqual(proactive.deliver([candidate['id']], '好' * 151)['status'], 'needs_revision')
            before = tm.clock().replace(hour=17, minute=59)
            self.assertEqual(proactive.check(candidate['id'], before)['status'], 'not_due')
            result = proactive.deliver([candidate['id']], '今天可以轻松收尾。')
            self.assertEqual(result['status'], 'accepted')
            self.assertTrue(proactive.deliver([candidate['id']], '再次提醒。')['duplicate'])
            self.assertEqual(len(s.read('SELECT * FROM deliveries')), 1)

    def test_contact_pause_and_shared_lesson_limit_still_apply(self):
        tm.activate('2025-10-24 19:00', False)
        ap.enable()
        snap = self.snapshot()
        with patch.object(tm, 'snapshot', return_value=snap):
            candidate = next(r for r in proactive.refresh('2025-10-24') if r['rule'] == closing_week.RULE)
            lesson = candidate['evidence']['overdue_lessons'][0]
            with closing(sqlite3.connect(s.DB)) as c, c:
                for i, day in enumerate(['2025-10-22', '2025-10-23']):
                    item = {'rule': 'remind-historical-makeup', 'evidence': {'overdue_lessons': [lesson]}}
                    c.execute('INSERT INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',
                              ('past' + str(i), day, item['rule'], day, json.dumps(item), 'delivered', s.now()))
                    c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)', ('past' + str(i), 'past' + str(i), 'sent' + str(i), day))
            result = proactive.deliver([candidate['id']], '今天可以轻松收尾。')
            self.assertEqual(result['status'], 'skipped')
            self.assertIn('两次', result['reason'])
            self.assertFalse(s.read('SELECT * FROM deliveries'))
            with closing(sqlite3.connect(s.DB)) as c, c:
                c.execute('DELETE FROM proactive_receipts')
                c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)', ('2025-10-24', 'source', '今天别提醒', s.now()))
            self.assertEqual(proactive.deliver([candidate['id']], '今天可以轻松收尾。')['status'], 'skipped')

    def test_skip_needs_query_but_not_precheck_or_course_text(self):
        task = self.begin()
        for name in [closing_week.RULE, 'reply-style']:
            ap.load_skill(name)
        with self.assertRaises(ap.ServiceValidationError):
            ap.finish(task['id'], 'no_contact', '本周多节未完')
        audit.invoke('learning_query', {'day': '2025-10-24'}, tm.snapshot)
        self.assertEqual(ap.finish(task['id'], 'no_contact', '本周多节未完')['status'], 'no_contact')

    def test_guard_accepts_task_identifier_without_precheck_and_rejects_other_action(self):
        self.begin()
        for name in ['tutor-main', 'service-policy', 'reply-style', closing_week.RULE]:
            ap.load_skill(name)
        snap = self.snapshot()
        with patch.object(tm, 'snapshot', return_value=snap):
            candidate = next(r for r in proactive.refresh('2025-10-24') if r['rule'] == closing_week.RULE)
            audit.invoke('family_profile', {}, s.family_profile)
            audit.invoke('action_candidates', {'day':'2025-10-24'}, lambda:proactive.refresh('2025-10-24'))
            audit.invoke('daily_context', {'day': '2025-10-24'}, lambda: {})
            audit.invoke('learning_query', {'day': '2025-10-24'}, lambda: snap)
            with self.assertRaises(ap.ServiceValidationError):
                ap.guard_delivery([candidate['id']])
            import course_service
            audit.invoke('course_schedule', {'date': '2025-10-24', 'class_type': 'L2', 'cohort': '61'},
                         lambda: course_service.course_schedule('2025-10-24', 'L2', '61'))
            audit.invoke('course_knowledge', {'class_type': 'L2', 'lesson_no': 10}, lambda: {'status': 'unavailable'})
            with self.assertRaises(ap.ServiceValidationError):
                ap.guard_delivery([candidate['id']])
            audit.invoke('course_knowledge', {'class_type': 'L2', 'lesson_no': 10}, lambda: {'status': 'ok', 'body': '真实课程正文'})
            ap.guard_delivery([candidate['id']])
            with patch.object(proactive, 'refresh', return_value=[{'id': 'other', 'skill': 'celebrate-important-day'}]):
                with self.assertRaises(ap.ServiceValidationError):
                    ap.guard_delivery(['other'])

    def test_receipts_wait_then_finish_without_sending_again(self):
        task = self.begin()
        for name in [closing_week.RULE, 'reply-style']:
            ap.load_skill(name)
        audit.invoke('learning_query', {}, tm.snapshot)
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)', ('s08-receipt', '消息正文', 'accepted', s.now(), 'test'))
            c.execute('INSERT INTO service_links VALUES(?,?)', (task['id'], 's08-receipt'))
        self.assertEqual(ap.finish(task['id'], 'completed', '平台已受理')['status'], 'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE deliveries SET status='sent' WHERE action_key='s08-receipt'")
        ap.reconcile_s01_receipts()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?', (task['id'],))[0]['status'], 'done')
        self.assertEqual(len(s.read('SELECT * FROM deliveries')), 1)

    def test_fixed_weekly_check_cannot_be_scheduled_again_by_planning(self):
        tm.activate('2025-10-24 10:00', True)
        ap.enable()
        for name in ['tutor-main', 'service-policy', 'plan-today', 'proactive-service']:
            ap.load_skill(name)
        snap = self.snapshot()
        with patch.object(tm, 'snapshot', return_value=snap), patch.object(ap, 'require_queries'):
            candidate = next(r for r in proactive.refresh('2025-10-24') if r['rule'] == closing_week.RULE)
            with self.assertRaisesRegex(ap.ServiceValidationError, '周期入口'):
                ap.schedule(candidate['id'], '2025-10-24T19:00:00+08:00', '收尾建议', '复查学情')
        self.assertFalse(any(r['kind'] == 'action' for r in ap.tasks()))

    def test_model_exit_uses_delivery_receipt_without_finish_call(self):
        for receipt in ['accepted','sent',None]:
            task=self.begin()
            if receipt:
                with closing(sqlite3.connect(s.DB)) as c, c:
                    c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('exit-receipt','一条消息',receipt,s.now(),'test'))
                    c.execute('INSERT INTO service_links VALUES(?,?)',(task['id'],'exit-receipt'))
            expected='awaiting_delivery' if receipt=='accepted' else 'done' if receipt=='sent' else 'blocked'
            self.assertEqual(ap.settle(task['id'])['status'],expected)
            self.assertIsNone(ap.active())
            if receipt=='accepted':
                with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent' WHERE action_key='exit-receipt'")
                ap.reconcile_s01_receipts()
                self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],'done')

    def test_legacy_missing_finish_is_reconciled_but_other_blocks_stay_blocked(self):
        for reason,expected in [('模型运行结束但未交付 service_task_finish 回执，请查看调用记录后重新评估。','done'),('人工核对发现其他业务问题','blocked')]:
            task=self.begin()
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute("UPDATE service_tasks SET status='blocked',result=? WHERE id=?",(json.dumps({'reason':reason}),task['id']))
                c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('legacy-receipt','一条消息','sent',s.now(),'test'))
                c.execute('INSERT INTO service_links VALUES(?,?)',(task['id'],'legacy-receipt'))
                c.execute("DELETE FROM metadata WHERE key='active_service_task'")
            ap.reconcile_s01_receipts()
            self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(task['id'],))[0]['status'],expected)
            self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_acceptance_scope_claims_only_s08_and_survives_clock_switch(self):
        tm.activate('2025-10-17 19:00', True)
        ap.enable()
        state=tm.state();state['task_scope']=['s08']
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(json.dumps(state),))
        task=ap.claim()
        self.assertEqual(task['kind'],'s08')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE service_tasks SET status='no_contact' WHERE id=?",(task['id'],))
            c.execute("DELETE FROM metadata WHERE key='active_service_task'")
        self.assertIsNone(ap.claim())
        tm.activate('2025-10-24 19:00', False)
        self.assertEqual(tm.state()['task_scope'],['s08'])


if __name__ == '__main__':
    unittest.main()
