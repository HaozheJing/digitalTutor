"""Independent durable effect ledger and actual transport invocation observation."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from .storage import encode, digest, new_id


class MockChannel:
    def __init__(self, path):
        self.path=str(path)
        self.db=sqlite3.connect(self.path,isolation_level=None,timeout=10,check_same_thread=False)
        self.lock=RLock()
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS delivery(operation_id TEXT PRIMARY KEY, payload TEXT NOT NULL, stage TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS transport_invocation(id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, entered_at TEXT NOT NULL, payload_digest TEXT NOT NULL, requested_outcome TEXT NOT NULL, status TEXT NOT NULL, response_stage TEXT, observed_stage TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS transport_query(id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, entered_at TEXT NOT NULL, status TEXT NOT NULL, observed_stage TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS transport_observation(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        from .transport_proof import initialize
        initialize(self)
        coverage={'version':1,'started_at':self.now(),
                  'legacy_delivery_rows':self.count(),
                  'scope':'Actual boundary calls since instrumentation; legacy calls are unknown and never backfilled.'}
        self.db.execute('INSERT OR IGNORE INTO transport_observation VALUES(?,?)',('coverage',encode(coverage)))

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def send(self, operation, outcome='sent'):
        with self.lock:
            invocation=new_id('transport')
            # Independent durable entry survives a later transaction rollback.
            # Pending means entered/uncertain, never proof of no external effect.
            self.db.execute('INSERT INTO transport_invocation VALUES(?,?,?,?,?,?,NULL,NULL)',
                (invocation,operation['id'],self.now(),digest(operation),outcome,'pending'))
        # The crash observer may inspect the business store. Do not hold the
        # channel lock while entering it: host reads acquire store then channel.
        if getattr(self,'boundary_hook',None):
            self.boundary_hook(operation['id'])
        with self.lock:
            actual='sent' if outcome=='unknown' else outcome
            self.db.execute('BEGIN IMMEDIATE')
            try:
                from .transport_proof import send_result
                special=send_result(self,operation)
                if special is None:
                    self.db.execute('INSERT OR IGNORE INTO delivery VALUES(?,?,?)',(operation['id'],encode(operation),actual))
                    observed=self.db.execute('SELECT stage FROM delivery WHERE operation_id=?',(operation['id'],)).fetchone()[0]
                else:
                    outcome=special
                    fact=self.db.execute('SELECT stage FROM delivery WHERE operation_id=?',(operation['id'],)).fetchone()
                    observed=fact[0] if fact else special
                self.db.execute('UPDATE transport_invocation SET status=?,response_stage=?,observed_stage=? WHERE id=?',
                    ('completed',outcome,observed,invocation))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
            return outcome

    def query(self, operation_id):
        with self.lock:
            query=new_id('query')
            self.db.execute('INSERT INTO transport_query VALUES(?,?,?,?,NULL)',
                (query,operation_id,self.now(),'pending'))
            row=self.db.execute('SELECT stage FROM delivery WHERE operation_id=?',(operation_id,)).fetchone()
            observed=row[0] if row else None
            if row is None and self.db.execute('SELECT 1 FROM terminal_fence WHERE operation_id=?',(operation_id,)).fetchone():
                observed='failed'
            self.db.execute('UPDATE transport_query SET status=?,observed_stage=? WHERE id=?',('completed',observed,query))
            # Absence remains unknown; observations confer no retry authority.
            return observed

    def count(self):
        with self.lock:
            return self.db.execute('SELECT count(*) FROM delivery').fetchone()[0]

    def audit_snapshot(self):
        with self.lock:
            def rows(table):
                cursor=self.db.execute('SELECT * FROM '+table+' ORDER BY rowid')
                names=[column[0] for column in cursor.description]
                return [dict(zip(names,row)) for row in cursor]
            return {'coverage':json.loads(self.db.execute('SELECT value FROM transport_observation WHERE key=?',('coverage',)).fetchone()[0]),
                    'send_invocations':rows('transport_invocation'),'queries':rows('transport_query'),
                    'terminal_fences':rows('terminal_fence'),'isolated_faults':rows('isolated_transport_fault'),
                    'counts_are_lifetime_total':False,'observation_is_retry_permission':False}

    def close(self):
        self.db.close()


def channel_for(app):
    if not hasattr(app,'channel'):
        path=':memory:' if app.store.path==':memory:' else str(Path(app.store.path).with_suffix('.channel.sqlite'))
        app.channel=MockChannel(path)
        from .runtime_hooks import crash_boundary
        app.channel.boundary_hook=lambda operation_id:crash_boundary(app,'transport_entered',operation_id=operation_id)
    config=getattr(app,'test_hooks',{}).get('transport_fault')
    if config:
        from .domain import require
        require(app.strict_runtime,'FAULT_SCOPE','Private faults require strict isolated runtime')
    app.channel.isolated_fault=config
    return app.channel
