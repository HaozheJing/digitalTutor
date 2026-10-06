"""SQLite repository. All state applications use short IMMEDIATE transactions."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

from .domain import BusinessError, require
from .migrations import upgrade


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def new_id(prefix):
    return prefix + '-' + uuid4().hex


SCHEMA = '''
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_migrations VALUES(1);
CREATE TABLE IF NOT EXISTS families(
 id TEXT PRIMARY KEY, uid TEXT NOT NULL UNIQUE, recipient TEXT NOT NULL UNIQUE,
 version INTEGER NOT NULL CHECK(version>=1), mock INTEGER NOT NULL CHECK(mock IN (0,1)));
CREATE TABLE IF NOT EXISTS records(
 kind TEXT NOT NULL, id TEXT NOT NULL, family_id TEXT NOT NULL REFERENCES families(id),
 version INTEGER NOT NULL CHECK(version>=1), data TEXT NOT NULL CHECK(json_valid(data)),
 PRIMARY KEY(kind,id));
CREATE INDEX IF NOT EXISTS records_family ON records(family_id,kind);
CREATE TABLE IF NOT EXISTS history(
 kind TEXT NOT NULL, id TEXT NOT NULL, family_id TEXT NOT NULL,
 version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind,id,version));
CREATE TABLE IF NOT EXISTS receipts(
 family_id TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(family_id,request_id));
CREATE TABLE IF NOT EXISTS report_links(
 report_key TEXT NOT NULL, event_id TEXT NOT NULL, family_id TEXT NOT NULL,
 resolved INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(report_key,event_id));
CREATE TABLE IF NOT EXISTS message_index(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
 family_id TEXT NOT NULL REFERENCES families(id), external_id TEXT NOT NULL,
 direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
 UNIQUE(family_id,external_id,direction));
CREATE TABLE IF NOT EXISTS goals(
 family_id TEXT NOT NULL, goal_key TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
 PRIMARY KEY(family_id,goal_key));
CREATE TABLE IF NOT EXISTS plans(
 family_id TEXT NOT NULL, business_date TEXT NOT NULL, plan_id TEXT NOT NULL UNIQUE,
 PRIMARY KEY(family_id,business_date));
CREATE TABLE IF NOT EXISTS operation_keys(
 family_id TEXT NOT NULL, purpose_key TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
 PRIMARY KEY(family_id,purpose_key));
CREATE TABLE IF NOT EXISTS sessions(
 token_hash TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id),
 role TEXT NOT NULL, binding TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS imports(
 batch_id TEXT PRIMARY KEY, metadata TEXT NOT NULL CHECK(json_valid(metadata)));
CREATE TABLE IF NOT EXISTS learning_days(
 batch_id TEXT NOT NULL REFERENCES imports(batch_id), uid TEXT NOT NULL, day INTEGER NOT NULL,
 attendance_status TEXT NOT NULL, completion_status TEXT NOT NULL,
 homework_status TEXT NOT NULL, companion_status TEXT NOT NULL, checkin_status TEXT NOT NULL,
 source_date_label TEXT, source_learning_date TEXT, evidence_locator TEXT NOT NULL,
 PRIMARY KEY(batch_id,uid,day));
CREATE TABLE IF NOT EXISTS assignments(
 batch_id TEXT NOT NULL REFERENCES imports(batch_id), uid TEXT NOT NULL, day INTEGER NOT NULL,
 source_row INTEGER NOT NULL, chapter TEXT, status TEXT NOT NULL, submission_count INTEGER,
 submitted_at_raw TEXT, question_title TEXT, question_text TEXT, ai_guidance TEXT,
 answer_text TEXT, answer_rounds INTEGER, voice_answers INTEGER, quality_flags TEXT NOT NULL,
 PRIMARY KEY(batch_id,source_row));
'''


class Store:
    def __init__(self, path):
        self.path = ':memory:' if str(path) == ':memory:' else str(Path(path).resolve())
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=10, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=FULL')
        version = self.connection.execute('PRAGMA user_version').fetchone()[0]
        require(version in (0, 1, 2), 'DATABASE_VERSION', '数据库版本不受支持')
        if version == 0:
            self.connection.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nPRAGMA user_version=1; COMMIT;')
            version = 1
        upgrade(self.connection, version)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute('BEGIN IMMEDIATE')
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def family(self, family_id):
        row = self.connection.execute('SELECT * FROM families WHERE id=?', (family_id,)).fetchone()
        require(row is not None, 'FAMILY_NOT_FOUND', '家庭绑定不存在')
        return dict(row)

    def get(self, kind, record_id, family_id):
        row = self.connection.execute('SELECT * FROM records WHERE kind=? AND id=? AND family_id=?', (kind, record_id, family_id)).fetchone()
        require(row is not None, 'RECORD_NOT_FOUND', '当前家庭中不存在该记录')
        return {'id': row['id'], 'version': row['version'], **json.loads(row['data'])}

    def list(self, kind, family_id):
        rows = self.connection.execute('SELECT * FROM records WHERE kind=? AND family_id=? ORDER BY rowid', (kind, family_id))
        return [{'id': r['id'], 'version': r['version'], **json.loads(r['data'])} for r in rows]

    def put(self, kind, record_id, family_id, data, expected=None):
        row = self.connection.execute('SELECT family_id,version FROM records WHERE kind=? AND id=?', (kind, record_id)).fetchone()
        if row:
            require(row['family_id'] == family_id, 'FAMILY_SCOPE_MISMATCH', '不能覆盖其他家庭记录')
            require(expected == row['version'], 'STALE_EVENT', '记录已变化')
        else:
            require(expected is None, 'RECORD_NOT_FOUND', '记录不存在')
        version = row['version'] + 1 if row else 1
        clean = {k: v for k, v in data.items() if k not in ('id', 'version', 'update_pending')}
        payload = encode(clean)
        self.connection.execute('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET version=excluded.version,data=excluded.data', (kind, record_id, family_id, version, payload))
        self.connection.execute('INSERT INTO history VALUES(?,?,?,?,?)', (kind, record_id, family_id, version, payload))
        return self.get(kind, record_id, family_id)

    def bump(self, family_id):
        self.connection.execute('UPDATE families SET version=version+1 WHERE id=?', (family_id,))
        return self.family(family_id)['version']

    def close(self):
        self.connection.close()
