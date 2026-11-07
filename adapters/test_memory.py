"""Meaningful guardrails: exact evidence, idempotence and temporal supersession."""
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import sql_tools


class MemoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='memory-test-', dir=Path(__file__).resolve().parent)
        self.original_db = sql_tools.DB
        sql_tools.DB = Path(self.temp.name) / 'test.sqlite'
        with closing(sqlite3.connect(sql_tools.DB)) as c, c:
            c.executescript('''
            CREATE TABLE messages(source_id TEXT PRIMARY KEY,speaker TEXT,body TEXT,created_at TEXT,source TEXT,sha256 TEXT,visible INTEGER);
            CREATE TABLE memories(id TEXT PRIMARY KEY,memory_key TEXT,value TEXT,source_message_id TEXT,quote TEXT,source_sha256 TEXT,observed_at TEXT,recorded_at TEXT,status TEXT,source TEXT);
            CREATE UNIQUE INDEX one_active_memory ON memories(memory_key) WHERE status='active';
            ''')
            for ident, body, date in [('m1', '老师你好，我是豆豆', '2026-10-19'), ('m2', '我是小豆', '2026-10-20')]:
                c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', (ident, 'user', body, date, 'test_fixture', hashlib.sha256(body.encode()).hexdigest(), 1))

    def tearDown(self):
        sql_tools.DB = self.original_db
        self.temp.cleanup()

    def test_rejects_invented_evidence_and_inferred_child_role(self):
        for key, value, source, quote in [('speaker.self_reported_name', '小禾', 'm1', '我是小禾'), ('child.name', '豆豆', 'm1', '我是豆豆'), ('speaker.self_reported_name', '豆豆', 'absent', '我是豆豆')]:
            with self.assertRaises(ValueError):sql_tools.memory_remember(key, value, source, quote)
        self.assertEqual(sql_tools.memory_recall(), [])

    def test_bad_date_is_rejected_instead_of_becoming_unknown_data(self):
        for day in ['2026--10-20', '20261020', '2026-02-30']:
            with self.assertRaises(ValueError):sql_tools.check(day)

    def test_duplicate_is_idempotent_and_retains_source(self):
        args = ('speaker.self_reported_name', '豆豆', 'm1', '我是豆豆')
        first = sql_tools.memory_remember(*args)
        second = sql_tools.memory_remember(*args)
        self.assertEqual(first['memory']['id'], second['memory']['id'])
        self.assertEqual(second['status'], 'already_remembered')
        self.assertEqual(len(sql_tools.memory_recall()), 1)
        self.assertEqual(first['memory']['source_sha256'], hashlib.sha256('老师你好，我是豆豆'.encode()).hexdigest())

    def test_explicit_wo_jiao_is_remembered_but_name_prefix_is_not(self):
        explicit='老师，我叫林琳，后面和我说话时可以叫我林琳。'
        ambiguous='我叫林琳妈妈来接孩子。'
        with closing(sqlite3.connect(sql_tools.DB)) as c,c:
            for ident,body in [('m3',explicit),('m4',ambiguous)]:
                c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',(ident,'user',body,'2026-10-21','test_fixture',hashlib.sha256(body.encode()).hexdigest(),1))
        with self.assertRaises(ValueError):
            sql_tools.memory_remember('speaker.self_reported_name','林琳','m4',ambiguous)
        saved=sql_tools.memory_remember('speaker.self_reported_name','林琳','m3',explicit)
        self.assertEqual(saved['status'],'remembered')
        self.assertEqual(sql_tools.memory_recall()[0]['source_message_id'],'m3')

    def test_newer_self_report_supersedes_but_old_cannot_overwrite(self):
        sql_tools.memory_remember('speaker.self_reported_name', '豆豆', 'm1', '我是豆豆')
        sql_tools.memory_remember('speaker.self_reported_name', '小豆', 'm2', '我是小豆')
        self.assertEqual(sql_tools.memory_recall()[0]['value'], '小豆')
        with self.assertRaises(ValueError):sql_tools.memory_remember('speaker.self_reported_name', '豆豆', 'm1', '我是豆豆')
        self.assertEqual(sql_tools.read("SELECT count(*) AS n FROM memories WHERE status='superseded'")[0]['n'], 1)


if __name__ == '__main__':unittest.main()
