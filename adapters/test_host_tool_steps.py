import unittest,json,tempfile,sqlite3
from pathlib import Path
from unittest.mock import patch
import sql_tools as s,host_tool_steps as host,material_cache,voice_understanding,festival_service

class HostStepsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  root=Path(self.tmp.name);db=root/'db'
  for p in [patch.object(s,'R',root),patch.object(s,'DB',db),patch.object(host,'enabled',return_value=True),patch('test_mode.state',return_value={'run_id':'run','uid':'family','as_of':'2025-10-13T10:00:00+08:00'}),patch('observability.invoke',side_effect=lambda n,a,fn,**kw:fn())]:p.start();self.addCleanup(p.stop)
  with sqlite3.connect(db) as c:
   c.executescript("CREATE TABLE events(id TEXT,kind TEXT,status TEXT,payload TEXT);CREATE TABLE voice_inputs(id TEXT,source_id TEXT,run_id TEXT,uid TEXT,result TEXT);")
   c.execute('INSERT INTO events VALUES(?,?,?,?)',('event','passive','dispatched',json.dumps({'source_message_ids':['source','source','foreign']})))
   c.executemany('INSERT INTO voice_inputs VALUES(?,?,?,?,?)',[('audio','source','run','family',None),('other','foreign','other-run','other-family',None)])
 def test_host_only_processes_current_batch_family_once(self):
  with patch.object(voice_understanding,'understand',return_value={'status':'unavailable','needs_audio_resend':True,'current_speaker':{'address_mode':'neutral'}}) as read:
   result=host.event_materials('event')
  read.assert_called_once_with('audio');self.assertTrue(result[0]['needs_audio_resend'])
  self.assertEqual(material_cache.read(result[0]['material_ref'])['source_args'],{'asset_id':'audio'})
 def test_long_voice_is_paged_and_not_silently_discarded(self):
  with patch.object(voice_understanding,'understand',return_value={'status':'ok','transcript':'语音内容'*2200}):result=host.event_materials('event')[0]
  self.assertTrue(result['read_required']);page=material_cache.read(result['material_ref']);self.assertIsNotNone(page['next_offset'])
  text=page['json_fragment'];offset=page['next_offset']
  while offset is not None:
   page=material_cache.read(result['material_ref'],offset);text+=page['json_fragment'];offset=page['next_offset']
  self.assertEqual(json.loads(text)['transcript'],'语音内容'*2200)
 def test_non_dispatched_event_has_no_media_effect(self):
  with sqlite3.connect(s.DB) as c:c.execute("UPDATE events SET status='done'")
  with patch.object(voice_understanding,'understand') as read:
   with self.assertRaises(ValueError):host.event_materials('event')
   read.assert_not_called()
 def test_inspection_is_mandatory_but_model_still_decides(self):
  value={'status':'ready','asset_id':'image','requires_inspection':True}
  with patch.object(festival_service,'read_asset',return_value={'sha256':'hash','inspection':None}),patch.object(festival_service,'inspect_festival_image',return_value={'inspection':'文字含错字，应重做'}) as inspect:
   result=host.inspected_image(value);inspect.assert_called_once_with('image')
   self.assertTrue(result['inspection_completed']);self.assertIn('错字',result['inspection_result']['inspection']);self.assertNotIn('sent',result.values())
 def test_failed_inspection_never_claims_ready_to_send(self):
  with patch.object(festival_service,'read_asset',return_value={'inspection':None}),patch.object(festival_service,'inspect_festival_image',return_value={'status':'unavailable'}):
   value=host.inspected_image({'status':'ready','asset_id':'image'})
   self.assertFalse(value['inspection_completed']);self.assertIn('不能发送',value['next'])
 def test_generation_retry_reuses_persisted_inspection(self):
  with patch.object(festival_service,'read_asset',return_value={'sha256':'hash','inspection':'实际成图'}),patch.object(festival_service,'inspect_festival_image') as inspect:
   value=host.inspected_image({'status':'ready','asset_id':'image'});self.assertTrue(value['inspection_result']['reused']);inspect.assert_not_called()
 def test_missing_or_unknown_receipt_does_not_complete(self):
  with patch('business.finish',side_effect=ValueError('unknown')) as finish:
   self.assertEqual(host.finish_event('event')['status'],'incomplete');finish.assert_called_once()
  with sqlite3.connect(s.DB) as c:self.assertEqual(c.execute('SELECT status FROM events').fetchone()[0],'dispatched')
 def test_already_done_and_nightly_are_not_replayed(self):
  with patch('business.finish') as finish:
   with sqlite3.connect(s.DB) as c:c.execute("UPDATE events SET status='done'")
   self.assertEqual(host.finish_event('event')['status'],'done');finish.assert_not_called()
   with sqlite3.connect(s.DB) as c:c.execute("UPDATE events SET kind='memory_consolidation',status='dispatched'")
   self.assertEqual(host.finish_event('event')['status'],'not_owned');finish.assert_not_called()
 def test_send_preflight_is_fresh_and_bound_not_cached(self):
  row={'kind':'s07','candidate_id':'candidate','day':'2025-10-13'}
  with patch('task_context.candidates',return_value={'actions':[]}) as candidates,patch('business.token',side_effect=lambda x:x),patch('business.project',side_effect=lambda x:x),patch('proactive.check',side_effect=[{'status':'eligible'},{'status':'unknown'}]) as check:
   first=host.task_facts(row,{});second=host.task_facts(row,{})
   self.assertEqual(first['preflight']['status'],'eligible');self.assertEqual(second['preflight']['status'],'unknown');self.assertEqual(check.call_count,2);self.assertEqual(candidates.call_count,2)

if __name__=='__main__':unittest.main()
