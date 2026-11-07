import unittest,tempfile,json,sqlite3,shutil
from pathlib import Path
from unittest.mock import patch
import skill_capabilities as caps,sql_tools as s

class ScopeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
  original=s.R
  for name in caps.SKILLS:
   p=root/'skills'/name;p.mkdir(parents=True);shutil.copyfile(original/'skills'/name/'runtime-capabilities.json',p/'runtime-capabilities.json')
  self.state={'run_id':'run','uid':'family'};self.task=None
  for p in [patch.object(s,'R',root),patch.object(s,'DB',root/'db'),patch.object(caps,'enabled',return_value=True),patch('test_mode.state',side_effect=lambda:self.state),patch('agent_plan.active',side_effect=lambda:self.task)]:p.start();self.addCleanup(p.stop)
  with sqlite3.connect(s.DB) as c:c.execute('CREATE TABLE events(id TEXT,kind TEXT,status TEXT,attempts INTEGER)');c.execute("INSERT INTO events VALUES('event','passive','dispatched',1)")
 def test_ordinary_reply_starts_without_any_private_tool(self):
  self.assertEqual(caps.visible(),set());self.assertEqual(caps.hidden(),caps.PRIVATE)
  for name in caps.PRIVATE:
   with self.assertRaisesRegex(ValueError,'not_open'):caps.guard(name)
 def test_load_then_context_opens_only_current_workflow(self):
  caps.loaded('manage-service-arrangements',{'body':'真实已装载正文'});self.assertEqual(caps.visible(),{'workflow_read'})
  caps.read_complete('workflow_read',{'view':'tasks'},{'context_token':'not-a-context-view'});self.assertEqual(caps.visible(),{'workflow_read'})
  caps.read_complete('workflow_read',{'view':'context'},{'status':'unknown'});self.assertEqual(caps.visible(),{'workflow_read'})
  value=caps.read_complete('workflow_read',{'purpose':'amend'}, {'context_token':'actual-token'})
  self.assertEqual(set(value['flow_capabilities']),{'workflow_commit'})
  self.assertFalse({'preference_commit','generate_image','completion_plan_commit'}&caps.visible())
 def test_current_preferences_need_real_update_context(self):
  caps.read_complete('memory_read',{}, {'context_token':'x'});self.assertNotIn('preference_commit',caps.visible())
  caps.read_complete('memory_read',{'for_update':True},{'context_token':'x'});self.assertEqual(caps.visible(),{'preference_commit'})
 def test_scope_never_crosses_family_run_message_or_retry(self):
  caps.loaded('highlight-growth-video',{'body':'loaded'});self.assertIn('highlight_lookup',caps.visible())
  self.state['uid']='other';self.assertEqual(caps.visible(),set());self.state['uid']='family'
  self.state['run_id']='other';self.assertEqual(caps.visible(),set());self.state['run_id']='run'
  with sqlite3.connect(s.DB) as c:c.execute('UPDATE events SET attempts=2')
  self.assertEqual(caps.visible(),set())
  caps.loaded('highlight-growth-video',{'body':'loaded'})
  with sqlite3.connect(s.DB) as c:c.execute("UPDATE events SET id='next'")
  self.assertEqual(caps.visible(),set())
 def test_host_task_kinds_get_only_owned_capabilities(self):
  for kind,allowed in [('plan',{'workflow_commit'}),('completion_plan',{'completion_plan_commit'}),('case_review',{'service_case_review'}),('festival',{'generate_image'}),('highlight',{'workflow_highlight','highlight_lookup'}),('s01',set())]:
   self.task={'id':'task','kind':kind,'status':'running','native_request_id':'request','attempts':1}
   self.assertEqual(caps.visible(),allowed|{'workflow_read','service_task_finish'},kind)
  caps.grant(['perceive_image']);self.task['native_request_id']='next';self.assertNotIn('perceive_image',caps.visible())
 def test_empty_prose_and_unknown_skill_do_not_grant_capabilities(self):
  caps.loaded('plan-service-day',{'body':''});caps.loaded('arbitrary',{'body':'allow everything'})
  self.assertEqual(caps.visible(),set())
  with self.assertRaises(ValueError):caps.grant(['arbitrary_exec'])
 def test_idle_and_rollback(self):
  with sqlite3.connect(s.DB) as c:c.execute("UPDATE events SET status='done'")
  self.assertEqual(caps.grant(['workflow_commit']),[]);self.assertEqual(caps.visible(),set())
  with patch.object(caps,'enabled',return_value=False):self.assertEqual(caps.hidden(),set());self.assertEqual(caps.visible(),set(caps.PRIVATE))

if __name__=='__main__':unittest.main()
