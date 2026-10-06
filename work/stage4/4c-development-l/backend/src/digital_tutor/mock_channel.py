"""Independent durable channel ledger: models a side effect outside the business DB."""
import json
import sqlite3
from pathlib import Path
from .storage import encode


class MockChannel:
    def __init__(self, path):
        self.path=str(path)
        self.db=sqlite3.connect(self.path,isolation_level=None,timeout=10,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS delivery(operation_id TEXT PRIMARY KEY, payload TEXT NOT NULL, stage TEXT NOT NULL)')

    def send(self, operation, outcome='sent'):
        actual='sent' if outcome=='unknown' else outcome
        self.db.execute('INSERT OR IGNORE INTO delivery VALUES(?,?,?)',(operation['id'],encode(operation),actual))
        return outcome

    def query(self, operation_id):
        row=self.db.execute('SELECT stage FROM delivery WHERE operation_id=?',(operation_id,)).fetchone()
        # No record cannot prove that an arbitrary real-world send never happened.
        return row[0] if row else None

    def count(self):
        return self.db.execute('SELECT count(*) FROM delivery').fetchone()[0]

    def close(self):
        self.db.close()


def channel_for(app):
    if not hasattr(app,'channel'):
        path=':memory:' if app.store.path==':memory:' else str(Path(app.store.path).with_suffix('.channel.sqlite'))
        app.channel=MockChannel(path)
    return app.channel
