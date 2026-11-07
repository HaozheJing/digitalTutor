import copy,json,sqlite3,unittest,tempfile,hashlib
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf,highlight_events as he,host_highlights as hh,wechat_test as wechat
from test_service_workflow import WorkflowTests
from test_fixture_integration import confirm_outbox

class HighlightEventsTests(unittest.TestCase):
 setUp=WorkflowTests.setUp
 tearDown=WorkflowTests.tearDown
 create_source=WorkflowTests.create_source
 set_context=WorkflowTests.set_context
 begin_workflow=WorkflowTests.begin_workflow
 stop_plan=WorkflowTests.stop_plan
 def begin(self,at='2025-10-19 19:00'):
  cfg={'enabled':True,'source':'test-producer','source_mode':'external','allowed_image_hosts':['coolcdn.igetcool.com']}
  p=patch.object(he,'config',return_value=cfg);p.start();self.addCleanup(p.stop)
  p=patch('course_service.binding',return_value={'class_type':'L2','cohort':'61'});p.start();self.addCleanup(p.stop)
  return self.begin_workflow(at)
 def event(self):return {'schema_version':1,'event_id':'event-1','achievement_id':'L2-61-week-1','uid':'00001','enrollment':{'class_type':'L2','cohort':'61'},'achievement_type':'seven_day_completion','achieved_at':'2025-10-19T19:00:00+08:00','expires_at':'2025-10-19T21:59:59+08:00','caption':'接口原文：一周读完五个课程日。\n保留原文，不改写。','image':{'url':'https://coolcdn.igetcool.com/test-card.png'},'evidence':'外部程序已确认窗口内5个不同课程日完成'}
 def planned(self):
  plan,_=self.begin();payload=self.event();he.ingest(payload);ctx=wf.context();entry=next(x for x in ctx['catalog'] if x['kind']=='highlight');candidate=entry['opportunities'][0]
  choices=[{'skill':x['skill'],'decision':'skip','reason':'本测试不安排其他动作'} for x in ctx['catalog'] if x['skill']!=entry['skill']]
  choices.append({'skill':entry['skill'],'decision':'create','reason':'外部已确认，交付绑定原文原图','opportunity_id':candidate['id'],'run_at':candidate['time_window']['earliest']})
  wf.commit('external-highlight-plan',ctx['context_token'],choices);self.stop_plan(plan['id'])
  task=s.read("SELECT * FROM service_tasks WHERE kind='highlight'")[0]
  with closing(sqlite3.connect(s.DB)) as c,c:
   c.execute("UPDATE service_tasks SET status='running',attempts=1,started_at=? WHERE id=?",(s.now(),task['id']));c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(task['id']),))
  return task,payload
 def test_event_idempotency_conflict_and_family_binding(self):
  self.begin();p=self.event();self.assertFalse(he.ingest(p)['duplicate']);self.assertTrue(he.ingest(p)['duplicate'])
  for changed in [{**p,'caption':'改写'}, {**p,'event_id':'new-event'}, {**p,'uid':'other'}]:
   with self.assertRaises((he.InvalidEvent,he.EventConflict)):he.ingest(changed)
  self.assertEqual(len(s.read('SELECT * FROM external_highlight_events')),1)
  bad={**p,'event_id':'bad','achievement_id':'bad','image':{'url':'http://127.0.0.1/private'}}
  with self.assertRaises(he.InvalidEvent):he.ingest(bad)
 def test_external_fact_does_not_reinfer_learning(self):
  self.begin();p=self.event();he.ingest(p)
  h=tm.highlight_lookup()['highlights'];self.assertEqual(len(h),1);self.assertEqual(h[0]['authority'],'external_program_confirmed');self.assertEqual(h[0]['share_txt'],p['caption'])
 def test_overnight_event_waits_until_open_and_retries_after_expiry(self):
  self.begin();state=tm.state();state['as_of']='2025-10-19T23:00:00+08:00'
  with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
  p=self.event();p.update(achieved_at='2025-10-19T22:30:00+08:00',expires_at='2025-10-20T20:00:00+08:00');he.ingest(p)
  self.assertEqual(he.records(tm.state())[0]['candidate_at'],'2025-10-20T06:30:00+08:00')
  state=tm.state();state['as_of']='2025-10-21T06:30:00+08:00'
  with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
  self.assertTrue(he.ingest(p)['duplicate']);self.assertEqual(he.records(tm.state()),[])
 def test_original_caption_image_delivered_and_callback_completes(self):
  task,p=self.planned()
  with patch.object(wechat,'http_send',return_value={'errcode':0,'requestId':'test-receipt'}) as sender:
   result=hh.execute(task['id']);self.assertEqual(result['status'],'awaiting_delivery')
   self.assertEqual([x.args[3] for x in sender.call_args_list],[p['caption'],p['image']['url']]);self.assertEqual(sender.call_count,2)
   self.assertEqual(he.status(p['event_id'])['delivery_status'],'accepted')
   confirm_outbox();self.assertEqual(he.status(p['event_id'])['delivery_status'],'sent')
   self.assertEqual(hh.execute(task['id'])['status'],'done');self.assertEqual(sender.call_count,2)
 def test_unknown_never_replays_or_claims_sent(self):
  task,p=self.planned()
  with patch.object(wechat,'http_send',side_effect=TimeoutError('test-timeout')) as sender:
   result=hh.execute(task['id']);calls=sender.call_count;self.assertEqual(result['status'],'blocked');hh.execute(task['id']);self.assertEqual(sender.call_count,calls)
   self.assertNotEqual(he.status(p['event_id'])['delivery_status'],'sent')
 def test_pause_stops_host_original_card(self):
  task,p=self.planned()
  with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT OR REPLACE INTO contact_constraints(day,source_message_id,quote,created_at) VALUES(?,?,?,?)',(task['day'],'今天暂停','source',s.now()))
  with patch.object(wechat,'http_send') as sender:
   self.assertEqual(hh.execute(task['id'])['status'],'blocked');sender.assert_not_called()

class EmojiRuntimeTests(unittest.TestCase):
 def test_missing_package_fails_before_native_launch(self):
  import emoji_runtime
  with tempfile.TemporaryDirectory() as folder:
   with self.assertRaisesRegex(ValueError,'emoji_assets_missing'):emoji_runtime.bind(Path(folder),{'emoji_ovcli_config':'credential'},{})
   self.assertEqual(emoji_runtime.bind(Path(folder),{}, {'emoji_enabled':False})['status'],'disabled_explicitly')
 def test_pinned_registry_and_private_credential(self):
  import emoji_runtime
  with tempfile.TemporaryDirectory() as folder:
   r=Path(folder);(r/'config').mkdir();credential=r/'upstream.conf';credential.write_text('secret-fixture')
   raw=json.dumps({'version':'v1','retrieval':{'uri':'viking://test'},'assets':[{'status':'approved','reviewed_by':'reviewer','knowledge':{'sha256':'a'*64}}]}).encode();(r/'config/emoji-assets.json').write_bytes(raw)
   result=emoji_runtime.bind(r,{'emoji_ovcli_config':str(credential)},{'emoji_registry_sha256':hashlib.sha256(raw).hexdigest()});self.assertEqual(result['status'],'ready');self.assertEqual((r/'runtime/emoji-ovcli.conf').read_text(),'secret-fixture')
   with self.assertRaisesRegex(ValueError,'digest_mismatch'):emoji_runtime.bind(r,{'emoji_ovcli_config':str(credential)},{'emoji_registry_sha256':'wrong'})

if __name__=='__main__':unittest.main()
