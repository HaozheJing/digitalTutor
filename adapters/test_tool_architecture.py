import unittest,tempfile,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sql_tools as s,tool_surface as surface

LEGACY={'service_schedule','service_schedule_cancel','save_plan','pause_contact','service_case_feedback','memory_remember'}
NIGHT={'memory_consolidation_context','memory_evidence_read','memory_consolidation_prepare','memory_consolidation_commit'}

class ArchitectureTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);(self.root/'config').mkdir();(self.root/'config/experiment.json').write_text('{}')
  p=patch.object(s,'R',self.root);p.start();self.addCleanup(p.stop)
  for name in ('service_workflow.enabled','preference_memory.enabled'):
   p=patch(name,return_value=True);p.start();self.addCleanup(p.stop)
  self.flags(retire_legacy=True,memory_scope=True)
 def flags(self,**extra):
  (self.root/'config/tool-surface.json').write_text(json.dumps({'version':2,'context':True,**extra}))
 def test_model_catalog_and_dispatch_remove_only_owned_entries(self):
  calls=[]
  class Denied(ValueError):pass
  names=LEGACY|NIGHT|{'reply_to_message','learning_query','service_task_finish','preference_commit'}
  server=SimpleNamespace(schema=lambda *a:None,base_mcp=SimpleNamespace(TOOLS=[(n,n,{}) for n in names]),agent_plan=SimpleNamespace(ServiceValidationError=Denied),business_call=lambda n,a:calls.append(n))
  surface.install(server)
  self.assertEqual({n for n,d,p in server.base_mcp.TOOLS},names-LEGACY-NIGHT)
  for name in LEGACY|NIGHT:
   with self.assertRaises(Denied):server.business_call(name,{})
  self.assertEqual(calls,[])
  server.business_call('learning_query',{});self.assertEqual(calls,['learning_query'])
 def test_rollback_and_old_mode_keep_old_capabilities(self):
  self.assertTrue((LEGACY|NIGHT)<=surface.retired())
  self.flags(retire_legacy=False,memory_scope=False)
  self.assertFalse((LEGACY|NIGHT)&surface.retired())
  self.flags(retire_legacy=True,memory_scope=True)
  with patch('service_workflow.enabled',return_value=False),patch('preference_memory.enabled',return_value=False):
   self.assertFalse(LEGACY&surface.retired());self.assertTrue(NIGHT<=surface.retired())
  (self.root/'config/experiment.json').unlink()
  self.assertEqual(surface.retired(),set())
 def test_nightly_owner_keeps_exact_tools_and_handler(self):
  import memory_session as night,memory_protocol,preference_memory
  self.assertEqual(night.NAMES,NIGHT)
  self.assertEqual({x[0] for x in memory_protocol.tools(preference_memory.KEYS) if x[0] in night.NAMES},NIGHT)
  with patch.object(night.m,'consolidation_commit',return_value={'status':'committed'}) as commit,patch('experiment_runtime.verify'),patch('observability.invoke',side_effect=lambda n,a,fn,**kw:fn()):
   self.assertEqual(night.call('memory_consolidation_commit',{'event_id':'night','prepared_token':'token'}),{'status':'committed'})
   commit.assert_called_once_with(event_id='night',prepared_token='token')

if __name__=='__main__':unittest.main()
