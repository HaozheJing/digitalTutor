"""Validate preparatory fixtures without model calls or current-round business actions."""
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from digital_tutor.application import Application, Clock
from accept_stage4_behavior import seed_historical_responsibilities
from accept_stage4_passive import seed_sources, sanitized_database
from stage4_behavior_fixtures import inputs_for,oracle_for
from stage4_behavior_checks import evaluate


class BehaviorFixtureTests(unittest.TestCase):
    def test_historical_fixture_does_not_prewrite_current_model_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(Path(tmp)/'fixture.sqlite',Clock('2026-10-04T10:00:00+08:00'),
                            mode='passive',strict_runtime=True)
            try:
                families=seed_sources(app,inputs_for('old_progress'))
                seed_historical_responsibilities(app,families['a'])
                events={r['id']:r for r in app.store.list('event',families['a'])}
                plans={r['id']:r for r in app.store.list('plan',families['a'])}
                self.assertEqual(len(events),2)
                self.assertEqual({p['business_date'] for p in plans.values()},{'2026-10-03'})
                for kind in ('message','report','control','pending','memory','operation','run_outcome'):
                    self.assertEqual(app.store.list(kind,families['a']),[])
                checks,_,_,reports=evaluate(app,list(families.values()),SimpleNamespace(calls=[]),
                    'old_progress',oracle_for('old_progress'),events,plans,[])
                self.assertFalse(checks['original_report_exists'])
                self.assertFalse(checks['actual_model_runs'])
                self.assertTrue(checks['original_events_unchanged'])
                self.assertEqual(reports,[])
            finally:app.close()

    def test_participation_fixture_has_no_completion_or_work_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(Path(tmp)/'participation.sqlite',Clock('2026-10-04T10:00:00+08:00'))
            try:
                seed_sources(app,inputs_for('participation'))
                rows=app.store.connection.execute('SELECT attendance_status,completion_status,homework_status FROM learning_days').fetchall()
                self.assertTrue(rows)
                self.assertTrue(all(tuple(row)==('attended','unknown','unknown') for row in rows))
                self.assertEqual(app.store.connection.execute('SELECT count(*) FROM assignments').fetchone()[0],0)
            finally:app.close()

    def test_evidence_database_copy_removes_capabilities_without_touching_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(Path(tmp)/'source.sqlite')
            try:
                app.create_family('f','synthetic','mock:f')
                token=app.open_session('f','service')
                target=Path(tmp)/'sanitized.sqlite'
                sanitized_database(app,target)
                with sqlite3.connect(target) as copy:
                    self.assertEqual(copy.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
                self.assertEqual(app.auth(token,{'service'})[0],'f')
                self.assertEqual(app.store.connection.execute('SELECT count(*) FROM sessions').fetchone()[0],1)
            finally:app.close()
