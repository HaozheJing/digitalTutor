import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
from pathlib import Path
import test_fixture_integration as fixtures
import test_mode as tm
import sql_tools as s
import agent_plan as ap
import console_control as control
import workbench


class ConsoleTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def start(self):
        tm.activate('2025-11-05 19:00',False);ap.enable()

    def dump(self):
        with closing(sqlite3.connect(s.DB)) as c:return list(c.iterdump())

    def test_preview_is_time_bounded_and_never_writes(self):
        self.start();before=self.dump()
        for at in ['2025-10-13T06:29:00+08:00','2025-10-24T19:00:00+08:00','2025-11-09T09:00:00+08:00']:
            v=workbench.preview(at)
            self.assertTrue(v['read_only'])
            self.assertTrue(all(l['scheduled_date']<=at[:10] for l in v['lessons']))
            self.assertTrue(all(not l['completed_at'] or l['completed_at']<=at for l in v['lessons']))
            if at.startswith('2025-10-24'):
                self.assertIn('interaction',[x['kind'] for x in v['checks']])
            if at.startswith('2025-11-09'):
                self.assertIn('weekly',[x['kind'] for x in v['checks']])
        self.assertEqual(self.dump(),before)

    def test_clear_context_archives_conversation_and_retains_plan(self):
        self.start();before=tm.state();tasks=ap.tasks()
        # Use the real schema and a visible source message, without any transport.
        with closing(sqlite3.connect(s.DB)) as c,c:
            cols=[r[1] for r in c.execute('PRAGMA table_info(messages)')]
            vals={x:'' for x in cols};vals.update(source_id='console-old',speaker='user',body='老师你好',visible=1,created_at=s.now())
            c.execute('INSERT INTO messages ('+','.join(cols)+') VALUES ('+','.join('?' for _ in cols)+')',list(vals.values()))
        result=control.clear_context()
        self.assertEqual(tm.state()['as_of'],before['as_of'])
        self.assertEqual(tm.state()['run_id'],before['run_id'])
        self.assertFalse(tm.state()['auto_highlights'])
        self.assertEqual(ap.tasks(),tasks)
        self.assertEqual(tm.context('2025-11-05')['recent_conversation'],[])
        self.assertEqual(s.conversation_read('console-old'),[])
        archive=Path(s.DB).parent/'test-archives'/(result['archive_id']+'.sqlite')
        with closing(sqlite3.connect(archive)) as c:self.assertEqual(c.execute('SELECT visible FROM messages WHERE source_id=?',('console-old',)).fetchone()[0],1)

    def test_manual_task_is_single_and_priority_without_auto(self):
        self.start();task=next(t for t in ap.tasks() if t['kind']=='s07')
        control.execute(task['id'])
        self.assertFalse(tm.state()['auto_highlights'])
        with self.assertRaises(ValueError):control.execute(task['id'])
        self.assertEqual(ap.claim()['id'],task['id'])
        self.assertNotIn('manual_service_task_id',tm.state())
        with self.assertRaises(ValueError):control.clear_context()

    def test_execute_rejects_future_and_other_runs_and_finished(self):
        self.start();tm.activate('2025-11-05 06:29',False);ap.enable()
        task=next(t for t in ap.tasks() if t['kind']=='s07')
        before=self.dump()
        for ident in ['service-invalid',task['id']]:
            with self.assertRaises(ValueError):control.execute(ident)
        self.assertEqual(self.dump(),before)
        tm.advance('2025-11-05 19:00')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='done' WHERE id=?",(task['id'],))
        with self.assertRaises(ValueError):control.execute(task['id'])

    def test_new_run_can_rewind_and_does_not_change_fixture(self):
        self.start();import fixture_query as fq
        content=fq.FILE.read_bytes();old=tm.state()['run_id']
        tm.activate('2025-10-13 07:00',False);ap.sync()
        self.assertNotEqual(tm.state()['run_id'],old)
        self.assertEqual(tm.snapshot()['as_of'],'2025-10-13T07:00:00+08:00')
        self.assertFalse(tm.state()['auto_highlights'])
        self.assertEqual(fq.FILE.read_bytes(),content)


if __name__=='__main__':unittest.main()
