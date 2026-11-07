import datetime
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import sql_tools as s
import live_runtime as live


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='live-test-', dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.old = s.DB
        s.DB = self.root / 'live.sqlite'
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.executescript((Path(__file__).parent / 'schema.sql').read_text())
            c.execute('INSERT INTO profiles VALUES(?,?,?,?,?)', ('mock', '小禾', 'unknown', 'sql_mock', s.now()))
        # Tests without a business clock use a stable daytime clock. Real/frozen
        # runtime states still exercise the production service-hours clock path.
        import service_hours as hours,test_mode as tm
        original_clock=hours.now
        def test_clock(c=None):
            state=tm.state()
            return original_clock(c) if state and state.get('as_of') else datetime.datetime(2026,10,20,10,tzinfo=hours.TZ)
        clock_patch=patch.object(hours,'now',side_effect=test_clock);clock_patch.start();self.addCleanup(clock_patch.stop)
        self.contexts = self.root / 'contexts'
        self.source = self.contexts / 'a'
        self.source.mkdir(parents=True)
        self.pointer = self.root / 'context.json'
        self.set_context('a')
        self.settings = {'pointer': str(self.pointer), 'contexts_root': str(self.contexts), 'recipient': 'allowed', 'activated_at': '2026-10-20T00:00:00+00:00'}
        self.create_source()

    def tearDown(self):
        s.DB = self.old
        self.temp.cleanup()

    def set_context(self, ident):
        self.source = self.contexts / ident
        self.source.mkdir(exist_ok=True)
        self.pointer.write_text(json.dumps({'id': ident, 'path': str(self.source)}))

    def create_source(self):
        with closing(sqlite3.connect(self.source / 'replyer.sqlite')) as c:
            c.execute('CREATE TABLE turns(id TEXT,recipient TEXT,body TEXT,reply TEXT,at TEXT,status TEXT,fixture TEXT)')

    def insert(self, ident='m1', at='2026-10-20T01:00:00+00:00', recipient='allowed'):
        with closing(sqlite3.connect(self.source / 'replyer.sqlite')) as c, c:
            c.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?)', (ident, recipient, '老师你好，我是豆豆', '', at, 'queued', 'free_chat'))

    def test_increment_cursor_late_reply_restart_and_contact_boundary(self):
        self.insert(at='2026-10-19T01:00:00+00:00')
        self.insert('private-other', recipient='other')
        before = hashlib.sha256((self.source / 'replyer.sqlite').read_bytes()).hexdigest()
        self.assertEqual(live.ingest(self.settings)['added_messages'], 1)
        self.assertEqual(hashlib.sha256((self.source / 'replyer.sqlite').read_bytes()).hexdigest(), before)
        self.assertEqual(s.read('SELECT kind FROM events')[0]['kind'], 'memory_plan')
        self.assertEqual(live.ingest(self.settings)['added_messages'], 0)
        with closing(sqlite3.connect(self.source / 'replyer.sqlite')) as c, c:
            c.execute("UPDATE turns SET reply='你好',status='sent' WHERE id='m1'")
        self.assertEqual(live.ingest(self.settings)['added_messages'], 1)
        live.recover()
        self.assertEqual(live.ingest(self.settings)['scanned_rows'], 0)
        self.insert('m2')
        live.ingest(self.settings)
        self.assertEqual(len(s.read("SELECT * FROM events WHERE kind='passive'")), 1)

    def test_pointer_rotation_and_untrusted_path(self):
        self.insert();live.ingest(self.settings)
        self.set_context('b');self.create_source();self.insert();live.ingest(self.settings)
        self.assertEqual(len(s.read('SELECT * FROM messages')), 2)
        self.pointer.write_text(json.dumps({'id': 'bad', 'path': str(self.root)}))
        with self.assertRaises(ValueError):live.ingest(self.settings)

    def test_profile_fields_do_not_come_from_chat_and_verified_wins(self):
        self.insert();live.ingest(self.settings)
        self.assertEqual(s.family_profile()['child_nickname'], '小禾')
        self.assertEqual(s.family_profile()['guardian_role'], 'unknown')
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute('INSERT INTO profiles VALUES(?,?,?,?,?)', ('business', '小满', 'father', 'business_api', s.now()))
        self.assertEqual(s.family_profile()['guardian_role'], 'father')
        self.assertTrue(s.family_profile()['fields']['child_nickname']['verified'])

    def test_completion_requires_reply_then_memory_then_plan_and_deduplicates(self):
        self.insert();live.ingest(self.settings)
        event = live.claim()['id']
        with self.assertRaises(ValueError):s.finish_event(event, '2026-10-20')
        first = s.mock_reply(event, '你好')
        second = s.mock_reply(event, '不同回复')
        self.assertEqual(first['body'], second['body'])
        s.memory_review(event, '已审查')
        with self.assertRaises(ValueError):s.finish_event(event, '2026-10-20')
        s.save_plan('2026-10-20', [], '无非周期候选')
        self.assertEqual(s.finish_event(event, '2026-10-20')['status'], 'done')
        live.recover();self.assertIsNone(live.claim())

    def test_chat_priority_blocks_internal_jobs(self):
        self.insert(at='2026-10-19T01:00:00+00:00');live.ingest(self.settings)
        s.gate('start');self.assertIsNone(live.claim())
        self.insert('fresh');live.ingest(self.settings)
        self.assertEqual(live.claim()['kind'], 'passive')

    def test_pending_inbound_blocks_proactive_send_before_agent_gate_starts(self):
        self.insert();live.ingest(self.settings)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO lessons VALUES(?,?,?,?,?,?,?,?,?)', ('lesson','2026-10-20','title','body',0,s.now(),'sql_mock','sql_mock','sql_mock'))
        self.assertEqual(s.deliver('2026-10-20','reminder')['status'],'deferred')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])
        self.assertEqual(len(live.pending_s01()),1)

    def test_restart_preserves_quiet_window_and_releases_dead_active_gate(self):
        s.gate('start');live.recover()
        gate=s.gate('read')
        self.assertFalse(gate['active'])
        import time
        self.assertGreater(gate['until'],time.time()+115)
        s.gate('end');before=s.gate('read')['until'];live.recover()
        self.assertEqual(s.gate('read')['until'],before)


if __name__ == '__main__':unittest.main()
