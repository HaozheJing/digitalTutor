import unittest,sqlite3
from unittest.mock import patch
import skill_capabilities as caps,capability_surface as cap,tool_surface as surface,sql_tools as s
import test_skill_capabilities as scope_tests

class WorkflowExposureTests(unittest.TestCase):
 setUp=scope_tests.ScopeTests.setUp
 def context(self,**task):
  return {'context_token':'ctx','owner':{'type':'dialogue'},'tasks':[{'id':'task-one','revision':3,'status':'planned','kind':'action',**task}]}
 def read(self,purpose='amend',data=None,**args):
  return caps.read_complete('workflow_read',{'purpose':purpose,**args},data if data is not None else self.context())
 def test_pause_only_opens_amend_and_binds_current_context(self):
  self.read(data={'context_token':'ctx','tasks':[]})
  self.assertEqual(caps.visible(),{'workflow_commit'})
  caps.guard('workflow_commit',{'context_token':'ctx'})
  for name,args in [('workflow_fulfill',{'context_token':'ctx'}),('workflow_commit',{'context_token':'old'})]:
   with self.assertRaises(ValueError):caps.guard(name,args)
 def test_inspection_revokes_previous_write_and_does_not_promise_commit(self):
  self.read();self.read('inspect');self.assertEqual(caps.visible(),set())
  v=cap.workflow_contract(self.context(),{'purpose':'inspect'})
  self.assertEqual(v['operation_contract']['mode'],'dialogue_inspect')
  self.assertNotIn('empty_tasks',v['operation_contract'])
 def test_fulfillment_is_bound_to_one_task_revision_and_context(self):
  self.read();self.read('fulfill',task_id='task-one')
  self.assertEqual(caps.visible(),{'workflow_fulfill'})
  args={'context_token':'ctx','task_id':'task-one','revision':3}
  caps.guard('workflow_fulfill',args)
  for extra in [{'context_token':'old'},{'task_id':'task-two'},{'revision':4},{'revision':True}]:
   with self.assertRaises(ValueError):caps.guard('workflow_fulfill',{**args,**extra})
  with self.assertRaises(ValueError):caps.guard('workflow_commit',{'context_token':'ctx'})
  self.read();self.assertEqual(caps.visible(),{'workflow_commit'})
 def test_empty_foreign_terminal_and_internal_tasks_cannot_open_fulfillment(self):
  for data in [{'context_token':'ctx','tasks':[]},self.context(id='other'),self.context(status='done'),self.context(status='awaiting_delivery'),*[self.context(kind=k) for k in ['plan','completion_plan','case_review','festival']]]:
   self.read()
   with self.subTest(data=data),self.assertRaisesRegex(ValueError,'existing_eligible_task'):self.read('fulfill',data,task_id='task-one')
   self.assertEqual(caps.visible(),set())
 def test_deferred_tasks_and_public_id_aliases_preserve_existing_identity(self):
  with sqlite3.connect(s.DB) as c:
   c.execute('CREATE TABLE business_ids(token TEXT PRIMARY KEY,original TEXT)');c.execute("INSERT INTO business_ids VALUES('ref-one','task-one')")
  self.read('fulfill',self.context(status='deferred'),task_id='ref-one')
  for ident in ['ref-one','task-one']:caps.guard('workflow_fulfill',{'context_token':'ctx','task_id':ident,'revision':3})
 def test_missing_context_revokes_old_writes(self):
  self.read();self.read(data={'status':'unknown'});self.assertEqual(caps.visible(),set())
 def test_context_purpose_does_not_leak_across_retry(self):
  self.read('fulfill',task_id='task-one')
  with sqlite3.connect(s.DB) as c:c.execute('UPDATE events SET attempts=2')
  self.assertEqual(caps.visible(),set())
  with self.assertRaises(ValueError):caps.guard('workflow_fulfill',{'context_token':'ctx','task_id':'task-one','revision':3})
 def test_internal_tasks_deny_delivery_even_after_loading_audio_skill(self):
  for kind in caps.INTERNAL_KINDS:
   self.task={'id':kind,'kind':kind,'status':'running','native_request_id':'request','attempts':1}
   opened=caps.loaded('generate-reading-audio',{'body':'loaded'})['flow_capabilities']
   self.assertEqual(opened,[])
   caps.grant(['workflow_fulfill','workflow_highlight','generate_image'])
   self.assertFalse(caps.visible()&caps.INTERNAL_FORBIDDEN)
   for name in caps.INTERNAL_FORBIDDEN:
    self.assertIn(name,caps.hidden())
    with self.assertRaisesRegex(ValueError,'not_open'):caps.guard(name,{})
  self.task={'id':'s01','kind':'s01','status':'running','native_request_id':'next','attempts':1}
  self.assertNotIn('send_message',caps.hidden());caps.guard('send_message',{})
 def test_read_router_strips_intent_before_domain_call_and_rejects_bad_mix(self):
  self.assertEqual(cap.route('workflow_read',{'purpose':'fulfill','task_id':'task-one'}),('workflow_context',{}))
  for args in [{'purpose':'fulfill'},{'purpose':'bad'},{'purpose':'inspect','task_id':'one'},{'view':'tasks','purpose':'amend'},{'view':'refresh','task_id':'one'}]:
   with self.assertRaises(ValueError):cap.route('workflow_read',args)

class GuidanceTests(unittest.TestCase):
 def setUp(self):
  for target in ['tool_surface.enabled','capability_surface.enabled','host_tool_steps.enabled','delivery_capability.enabled','skill_capabilities.enabled']:
   p=patch(target,return_value=True);p.start();self.addCleanup(p.stop)
 def test_current_guidance_has_no_retired_model_instructions(self):
  text=surface.guidance()
  for old in ['workflow_context','workflow_recheck','service_skill','service_reference','reply_to_message','deliver_message','inspect_festival_image','check_proactive','preference_context','finish_event']:
   self.assertNotIn(old,text)
  self.assertIn('purpose=fulfill',text);self.assertIn('workflow_read(view=refresh)',text)
 def test_festival_chain_delegates_checks_to_host(self):
  old='本次仅节日图文：generate_image生成后inspect_festival_image核对，check_proactive通过后deliver_festival一次提交，不用deliver_proactive只发文字。'
  text=surface.current_instructions(old)
  for retired in ['inspect_festival_image','check_proactive','deliver_festival','deliver_proactive']:self.assertNotIn(retired,text)
  self.assertIn('实际检查结果',text);self.assertIn('send_message',text)
 def test_rollback_keeps_old_host_contract(self):
  with patch('capability_surface.enabled',return_value=False):self.assertEqual(surface.current_instructions('workflow_context'),'workflow_context')

class RealWorkflowTests(unittest.TestCase):
 from test_service_workflow import WorkflowTests as Base
 setUp=Base.setUp;tearDown=Base.tearDown;begin_workflow=Base.begin_workflow
 create_source=Base.create_source;set_context=Base.set_context
 plan=Base.plan;stop_plan=Base.stop_plan;new_task=Base.new_task;dialogue=Base.dialogue
 def test_binding_preserves_actual_fulfillment_idempotence_and_receipts(self):
  tid=self.new_task();ctx=self.dialogue();task=ctx['tasks'][0]
  import service_workflow as wf,agent_plan as ap,wechat_test
  with patch.object(caps,'enabled',return_value=True),patch.object(caps,'policies',return_value={}):
   caps.read_complete('workflow_read',{'purpose':'fulfill','task_id':tid},ctx)
   args={'request_id':'scope-fulfill-real','context_token':ctx['context_token'],'task_id':tid,'revision':task['revision'],'body':'完整的服务回复。','evidence':'当前对话完整履行原事项'}
   caps.guard('workflow_fulfill',args)
   with patch.object(ap,'require_skills'),patch.object(wechat_test,'flush'):
    receipt=wf.fulfill(**args);caps.guard('workflow_fulfill',args);again=wf.fulfill(**args)
   self.assertEqual(receipt['status'],'bound');self.assertTrue(again['duplicate'])
   self.assertEqual(len(s.read('SELECT * FROM service_links WHERE task_id=?',(tid,))),1)
   self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(tid,))[0]['status'],'awaiting_delivery')

if __name__=='__main__':unittest.main()

class TurnLifetimeTests(unittest.TestCase):
 setUp=WorkflowExposureTests.setUp
 def test_internal_finish_stays_hidden_until_own_turn_released(self):
  self.task={'id':'internal','kind':'plan','status':'running','native_request_id':'r1','attempts':1}
  with sqlite3.connect(s.DB) as c:
   c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
   c.execute('CREATE TABLE service_tasks(id TEXT,run_id TEXT,native_request_id TEXT,attempts INTEGER,kind TEXT,status TEXT)')
   c.execute("INSERT INTO service_tasks VALUES('internal','run','r1',1,'plan','done')")
   c.execute("UPDATE events SET status='done'")
  caps.bind_turn('internal','r1');self.task=None
  self.assertIn('send_message',caps.hidden())
  with self.assertRaises(ValueError):caps.guard('send_message',{})
  caps.release_turn('older-request');self.assertIn('send_message',caps.hidden())
  caps.release_turn('r1');self.assertNotIn('send_message',caps.hidden())
 def test_turn_binding_rejects_wrong_request_and_cannot_cross_run(self):
  self.task={'id':'internal','kind':'plan','status':'running','native_request_id':'r1','attempts':1}
  with self.assertRaisesRegex(ValueError,'binding_mismatch'):caps.bind_turn('internal','other')

class HostPointerTests(unittest.TestCase):
 def test_projected_catalog_uses_current_reader_without_mutating_business_data(self):
  import working_context
  value={'catalog':[{'skill':'remind-reading','body':'stored business prose'}]}
  with patch('capability_surface.enabled',return_value=True):
   result=working_context.project(value)
   self.assertEqual(result['catalog'][0]['content_access']['tool'],'skill_read')
   self.assertEqual(value['catalog'][0]['body'],'stored business prose')
  with patch('capability_surface.enabled',return_value=False):
   self.assertEqual(working_context.project(value)['catalog'][0]['content_access']['tool'],'service_skill')
