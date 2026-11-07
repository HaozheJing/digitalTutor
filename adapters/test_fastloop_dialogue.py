import unittest,tempfile,sqlite3,json
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import dialogue_context as d
class Dialogue(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.db=Path(self.tmp.name)/'facts.sqlite'
  with closing(sqlite3.connect(self.db)) as c,c:
   c.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE messages(source_id TEXT,speaker TEXT,body TEXT,created_at TEXT,visible INTEGER); CREATE TABLE deliveries(action_key TEXT,body TEXT,status TEXT,created_at TEXT);')
   c.execute('INSERT INTO metadata VALUES(?,?)',('test_mode',json.dumps({'run_id':'r','uid':'f'})))
   for i in range(3):c.execute('INSERT INTO messages VALUES(?,?,?,?,1)',(str(i),'user','message '+str(i),'2025-10-13T08:00:00+08:00'))
  self.addCleanup(patch.stopall);patch.object(d.s,'DB',self.db).start();patch.object(d.m,'settings',return_value={'enabled':False,'family_uid':'f','contact_id':'c'}).start()
 def test_disabled_memory_window_and_paging(self):
  before=self.db.read_bytes();a=d.window('2025-10-13',max_rows=1);page=a['conversation_window']
  b=d.window('2025-10-13',offset=page['next_offset'],snapshot=page['snapshot'],max_rows=2)
  self.assertEqual(len(b['recent_conversation']),2);self.assertEqual(before,self.db.read_bytes())
 def test_forgotten_legacy_stays_hidden(self):
  with closing(sqlite3.connect(self.db)) as c,c:
   c.execute('CREATE TABLE preference_forgotten(scope TEXT,source_id TEXT)');c.execute('INSERT INTO preference_forgotten VALUES(?,?)',(d.s.encoded(['test:r','f','c']),'0'))
  self.assertNotIn('0',[x['source_id'] for x in d.window('2025-10-13')['recent_conversation']])
 def test_other_family_rejected(self):
  with patch.object(d.m,'settings',return_value={'enabled':False,'family_uid':'other','contact_id':'c'}):
   with self.assertRaisesRegex(ValueError,'family_mismatch'):d.window('2025-10-13')
 def test_rebound_contact_rejected(self):
  with closing(sqlite3.connect(self.db)) as c,c:
   c.execute('CREATE TABLE preference_bindings(namespace TEXT,identity TEXT)');c.execute('INSERT INTO preference_bindings VALUES(?,?)',('test:r',d.s.encoded(['f','other'])))
  with self.assertRaisesRegex(ValueError,'binding_changed'):d.window('2025-10-13')
 def test_stale_snapshot_rejected(self):
  with self.assertRaisesRegex(ValueError,'snapshot_changed'):d.window('2025-10-13',snapshot='stale')
if __name__=='__main__':unittest.main()
