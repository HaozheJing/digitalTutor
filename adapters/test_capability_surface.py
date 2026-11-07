import unittest
from types import SimpleNamespace
from unittest.mock import patch
import capability_surface as cap

class CapabilityTests(unittest.TestCase):
 def server(self,fn):
  schema=lambda properties=None,required=None:{'type':'object','properties':properties or {},'required':required or [],'additionalProperties':False}
  names=set().union(*cap.READ_GROUPS.values())|{'learning_query','reply_to_message'}
  return SimpleNamespace(schema=schema,base_mcp=SimpleNamespace(TOOLS=[(n,n,schema()) for n in names]),business_call=fn,agent_plan=SimpleNamespace(ServiceValidationError=ValueError))
 def test_mapped_reads_keep_original_guard_and_audit_not_duplicate_model_calls(self):
  calls=[]
  def fn(n,a):
   calls.append((n,a))
   if a.get('name')=='forbidden':raise ValueError('original_scope_denied')
   return {'status':'ok','version':'kept','context_token':'kept'}
  server=self.server(fn)
  with patch.object(cap,'enabled',return_value=True),patch('observability.invoke',side_effect=lambda n,a,fn,**kw:fn()) as audit:
   cap.install(server)
   names={n for n,d,p in server.base_mcp.TOOLS}
   self.assertEqual(names,set(cap.READ_GROUPS)|{'learning_query','reply_to_message'})
   self.assertEqual(server.business_call('skill_read',{'name':'answer-family-question'})['version'],'kept')
   self.assertEqual(calls[-1],('service_skill',{'name':'answer-family-question'}))
   self.assertEqual(audit.call_args.kwargs['origin'],'adapter')
   server.business_call('skill_read',{'name':'skill','path':'references/a.md','start_line':61})
   self.assertEqual(calls[-1][0],'service_reference');self.assertEqual(calls[-1][1]['start_line'],61)
   self.assertEqual(server.business_call('workflow_read',{'view':'refresh'})['context_token'],'kept');self.assertEqual(calls[-1],('workflow_recheck',{}))
   with self.assertRaisesRegex(ValueError,'original_scope_denied'):server.business_call('skill_read',{'name':'forbidden'})
   for n in cap.retired():
    with self.assertRaisesRegex(ValueError,'consolidated'):server.business_call(n,{})
 def test_forget_state_cannot_fall_back_to_old_memory_table(self):
  server=self.server(lambda n,a:self.fail('must not query legacy records'))
  with patch.object(cap,'enabled',return_value=True),patch('observability.invoke',side_effect=lambda n,a,fn,**kw:fn()),patch('preference_memory.enabled',return_value=True),patch('preference_memory.internal',return_value=False),patch('experiment_runtime.verify'),patch('preference_memory.recall',return_value={'items':[],'forgotten_keys':['reply.length']}) as recall:
   cap.install(server);result=server.business_call('memory_read',{})
   self.assertEqual(result['forgotten_keys'],['reply.length']);recall.assert_called_once()
 def test_update_context_and_rollback_keep_their_contracts(self):
  calls=[];server=self.server(lambda n,a:calls.append((n,a)))
  with patch.object(cap,'enabled',return_value=True),patch('observability.invoke',side_effect=lambda n,a,fn,**kw:fn()):
   cap.install(server);server.business_call('memory_read',{'for_update':True});self.assertEqual(calls,[('preference_context',{})])
  server=self.server(lambda n,a:None);original=server.base_mcp.TOOLS;fn=server.business_call
  with patch.object(cap,'enabled',return_value=False):cap.install(server);self.assertIs(server.base_mcp.TOOLS,original);self.assertIs(server.business_call,fn);self.assertEqual(cap.native_retired(),[])
 def test_incompatible_read_arguments_are_rejected(self):
  for name,args in [('skill_read',{'path':'references/a.md'}),('skill_read',{'name':'a','start_line':3}),('workflow_read',{'view':'send'}),('workflow_read',{'task_id':'foreign'}),('memory_read',{'for_update':'yes'})]:
   with self.assertRaises(ValueError):cap.route(name,args)

if __name__=='__main__':unittest.main()


class WorkflowContractTests(unittest.TestCase):
 def test_dialogue_empty_tasks_does_not_become_a_daily_plan(self):
  from capability_surface import workflow_contract
  data={'context_token':'token','owner':{'type':'dialogue'},'tasks':[],'catalog':[{'skill':'x'}]}
  value=workflow_contract(data,{'purpose':'amend'})
  self.assertEqual(value['operation_contract']['mode'],'dialogue_revision')
  self.assertEqual(value['tasks'],[])
  self.assertIn('never promise',value['operation_contract']['success_evidence'])
  self.assertIn('No existing task',value['operation_contract']['empty_tasks'])
  self.assertNotIn('operation_contract',data)
 def test_daily_contract_and_unknown_do_not_grant_authority(self):
  from capability_surface import workflow_contract
  self.assertEqual(workflow_contract({'status':'unknown'}),{'status':'unknown'})
  self.assertEqual(workflow_contract({'context_token':'t','owner':{'type':'daily'}})['operation_contract']['mode'],'daily_planning')
