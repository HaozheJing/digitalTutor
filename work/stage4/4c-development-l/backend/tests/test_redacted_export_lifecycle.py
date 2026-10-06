"""A closed redacted main DB must stand alone; source and old evidence stay intact."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]/'scripts'))
import accept_stage4_passive as export
from digital_tutor.application import Application,Clock
from digital_tutor.storage import encode


class ExportLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.app=Application(self.root/'source.sqlite',Clock('2026-10-04T10:00:00+08:00'))
        self.app.create_family('f','synthetic-f','mock:f')
        self.app.open_session('f','service')
        self.secret='PRIVATE_EXPORT_TOKEN_7d5a8cd5'
        self.capability='cap-'+'b'*32
        with self.app.store.transaction() as db:
            db.execute('UPDATE sessions SET binding=?',(encode({'api_key':self.secret}),))
            old=self.app.store.put('operation','op','f',{'api_key':self.secret,'token':self.capability,
                'note':self.capability,'body':'safe synthetic reply','padding':'x'*12000})
            self.app.store.put('operation','op','f',{'api_key':self.secret,'token':self.capability,
                'note':self.capability,'body':'current safe reply'},old['version'])
            db.execute('INSERT INTO receipts VALUES(?,?,?,?)',('f','receipt','opaque-digest',
                encode({'api_key':self.secret,'lease':self.capability,'body':'safe receipt'})))
            db.execute('CREATE TABLE freed_page_probe(value TEXT)')
            db.execute('INSERT INTO freed_page_probe VALUES(?)',(self.secret*1000,))
            db.execute('DELETE FROM freed_page_probe')

    def tearDown(self):self.app.close();self.tmp.cleanup()

    def source_bytes(self):
        return {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.root/'source.sqlite',self.root/'source.sqlite-wal') if p.exists()}

    def test_main_file_stands_alone_without_raw_old_pages_or_wal(self):
        before=self.source_bytes();session_count=self.app.store.connection.execute('SELECT count(*) FROM sessions').fetchone()[0]
        target=self.root/'redacted.sqlite';export.sanitized_database(self.app,target)
        self.assertEqual(self.source_bytes(),before)
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM sessions').fetchone()[0],session_count)
        for suffix in ('-wal','-shm','-journal'):self.assertFalse(Path(str(target)+suffix).exists())
        raw=target.read_bytes();self.assertNotIn(self.secret.encode(),raw);self.assertNotIn(self.capability.encode(),raw)
        other=self.root/'independent';other.mkdir();standalone=other/'copy.sqlite';shutil.copyfile(target,standalone)
        connection=sqlite3.connect(standalone.as_uri()+'?mode=ro&immutable=1',uri=True)
        try:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(connection.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
            values=[json.loads(r[0]) for r in connection.execute('SELECT data FROM records')]
            self.assertEqual(values[0]['body'],'current safe reply')
            self.assertNotIn('api_key',values[0])
            self.assertEqual(connection.execute('SELECT count(*) FROM history').fetchone()[0],2)
        finally:connection.close()

    def test_export_connection_is_explicitly_closed_before_return(self):
        original=sqlite3.connect;connections=[]
        class Tracked(sqlite3.Connection):
            closed=False
            def close(self):self.closed=True;return super().close()
        def connect(*args,**kwargs):
            connection=original(*args,**kwargs,factory=Tracked);connections.append(connection);return connection
        with patch.object(export.sqlite3,'connect',side_effect=connect):
            export.sanitized_database(self.app,self.root/'closed.sqlite')
        self.assertTrue(connections);self.assertTrue(all(c.closed for c in connections))

    def test_preexisting_export_and_sidecar_are_never_overwritten(self):
        target=self.root/'existing.sqlite';target.write_bytes(b'historical-evidence')
        with self.assertRaises(FileExistsError):export.sanitized_database(self.app,target)
        self.assertEqual(target.read_bytes(),b'historical-evidence')
        absent=self.root/'new.sqlite';sidecar=Path(str(absent)+'-wal');sidecar.write_bytes(b'old-wal')
        with self.assertRaises(FileExistsError):export.sanitized_database(self.app,absent)
        self.assertFalse(absent.exists());self.assertEqual(sidecar.read_bytes(),b'old-wal')

    def test_failure_during_redaction_publishes_nothing_and_preserves_source(self):
        before=self.source_bytes();target=self.root/'failed.sqlite'
        with patch.object(export,'safe',side_effect=ValueError('injected redaction failure')):
            with self.assertRaises(ValueError):export.sanitized_database(self.app,target)
        self.assertFalse(target.exists());self.assertEqual(self.source_bytes(),before)
        self.assertEqual(list(self.root.glob('.failed.sqlite-*')),[])

    def test_source_path_cannot_be_export_target(self):
        before=self.source_bytes()
        with self.assertRaises(FileExistsError):export.sanitized_database(self.app,self.root/'source.sqlite')
        self.assertEqual(self.source_bytes(),before)


if __name__=='__main__':unittest.main()
