import unittest,json,tempfile,sqlite3
from pathlib import Path
from unittest.mock import patch
import schedule_query as q, family_session as f, sql_tools as s, tool_surface as surface

class DataToolsTests(unittest.TestCase):
 def test_annual_range_day_and_unknown(self):
  year=q.read('L2','61');self.assertEqual(len(year['days']),365)
  known=[x for x in year['days'] if x['has_lesson'] is True]
  self.assertEqual([x['lesson_no'] for x in known],list(range(1,21)))
  self.assertEqual(q.read('L2','61',date='2025-10-13')['days'],[{'date':'2025-10-13','has_lesson':True,'lesson_no':1}])
  self.assertEqual(q.read('L2','61',start_date='2025-10-17',end_date='2025-10-20')['days'],[
   {'date':'2025-10-17','has_lesson':True,'lesson_no':5},{'date':'2025-10-18','has_lesson':False,'lesson_no':None},
   {'date':'2025-10-19','has_lesson':False,'lesson_no':None},{'date':'2025-10-20','has_lesson':True,'lesson_no':6}])
  self.assertIsNone(q.read('L2','61',date='2025-11-10')['days'][0]['has_lesson'])
  self.assertEqual(q.read('L3','61')['status'],'unknown')
  for args in [dict(date='2025-10-13',start_date='2025-10-13',end_date='2025-10-14'),dict(start_date='2025-10-13'),dict(start_date='2025-10-14',end_date='2025-10-13'),dict(date='2025-02-30')]:
   with self.assertRaises(ValueError):q.read('L2','61',**args)
  with self.assertRaises(TypeError):q.read('L2')
 def test_calendar_audit_is_complete(self):
  from observability import clean
  self.assertEqual(clean(q.read('L2','61')),q.read('L2','61'))
 def test_session_binding_is_idempotent_and_never_reassigned(self):
  with tempfile.TemporaryDirectory() as d,patch.object(s,'DB',Path(d)/'db'),patch.object(f.tm,'state',return_value={'uid':'A','run_id':'R'}) as state,patch.object(s,'_source_family_profile',return_value={'uid':'A','child_nickname':'child'}) as provider:
   one=f.bind('session-1');two=f.bind('session-1');self.assertEqual(one,two);provider.assert_called_once()
   self.assertEqual(f.current('session-1')['family_uid'],'A')
   state.return_value={'uid':'B','run_id':'R'}
   with self.assertRaises(ValueError):f.bind('session-1')
   with self.assertRaises(ValueError):f.current('session-1')
 def test_flags_only_apply_to_experiment_and_can_roll_back(self):
  with tempfile.TemporaryDirectory() as d,patch.object(s,'R',Path(d)):
   (s.R/'config').mkdir();p=s.R/'config/tool-surface.json';p.write_text(json.dumps({'version':2,'identity':True}))
   self.assertFalse(surface.enabled('identity'))
   (s.R/'config/experiment.json').write_text('{}');self.assertTrue(surface.enabled('identity'))
   self.assertEqual(surface.retired(),{'family_profile','student_tier'})
   p.write_text(json.dumps({'version':2,'identity':False}));self.assertEqual(surface.retired(),set())

class ReaderTests(unittest.TestCase):
 def test_exact_registered_course_and_uri(self):
  import data_readers as d, course_service as c
  for i in range(1,21):
   entry=c.entry('L2',i);args=d.course_args(class_type='L2',lesson_no=i)
   self.assertEqual(args,{'uri':entry['uri'],'detail':'full'})
   self.assertEqual(d.course_args(uri=entry['uri'])['detail'],'overview')
  self.assertIsNone(d.course_args(class_type='L2',lesson_no=21))
  for args in [{},{'class_type':'L2'},{'class_type':'L2','lesson_no':True},{'uri':'x','class_type':'L2','lesson_no':1}]:
   with self.assertRaises(ValueError):d.course_args(**args)
 def test_paging_is_typed_and_rejects_ambiguous_inputs(self):
  import data_readers as d
  self.assertEqual(d.evidence_target({'kind':'material','id':'m'},offset=2),('material_read',{'material_id':'m','offset':2}))
  self.assertEqual(d.evidence_target({'kind':'context','id':'c'},section='chat')[0],'context_page')
  for ref,kw in [({'kind':'context','id':'c'},{}),({'kind':'material','id':'m'},{'section':'chat'}),({'kind':'other','id':'m'},{}),({'kind':'material','id':''},{}),({'kind':'material','id':'m'},{'offset':-1})]:
   with self.assertRaises(ValueError):d.evidence_target(ref,**kw)
 def test_search_keeps_whole_body_or_readable_reference(self):
  import data_readers as d
  value={'matches':[{'document_id':'a','body':'abc','sha256':'1'},{'document_id':'b','body':'123456','sha256':'2'}]}
  result=d.knowledge_result(value,budget=4)
  self.assertEqual(result['matches'][0]['body'],'abc')
  self.assertNotIn('body',result['matches'][1]);self.assertEqual(result['matches'][1]['document_id'],'b')
  self.assertEqual(value['matches'][1]['body'],'123456')

class SurfaceRoutingTests(unittest.TestCase):
 def test_aliases_keep_original_permission_path_and_arguments(self):
  from types import SimpleNamespace
  import tool_surface as t
  calls=[]
  def original(name,args):
   calls.append((name,args))
   if args.get('execution_token')=='invalid':raise ValueError('execution_authority_rejected')
   return {'status':'sent'}
  schema=lambda properties=None,required=None:{'type':'object','properties':properties or {},'required':required or []}
  definitions=[(n,'old',schema({'highlight_ids':{'type':'array'},'generation_token':{'type':'string'},'execution_token':{'type':'string'}})) for n in ['reply_to_message','reply_with_highlights','deliver_festival','learning_query','family_profile','student_tier','daily_context','learning_context','course_knowledge','material_read','context_page']]
  server=SimpleNamespace(base_mcp=SimpleNamespace(TOOLS=definitions),business_call=original,schema=schema,agent_plan=SimpleNamespace(ServiceValidationError=ValueError))
  with patch.object(t,'enabled',return_value=True):
   t.install(server);names={x[0] for x in server.base_mcp.TOOLS}
   self.assertTrue({'reply_to_message','send_image','evidence_read','learning_query'}<=names)
   self.assertFalse(t.retired() & names)
   args={'event_id':'e','body':'hi','highlight_ids':['h'],'generation_token':'g'}
   server.business_call('reply_to_message',args);self.assertEqual(calls[-1],('reply_with_highlights',args))
   args={'candidate_id':'c','asset_id':'a','body':'hi','execution_token':'v','generation_token':'g'}
   server.business_call('send_image',args);self.assertEqual(calls[-1],('deliver_festival',args))
   with self.assertRaisesRegex(ValueError,'execution_authority_rejected'):server.business_call('send_image',{**args,'execution_token':'invalid'})
   server.business_call('evidence_read',{'ref':{'kind':'material','id':'m'}});self.assertEqual(calls[-1],('material_read',{'material_id':'m','offset':0}))
   for name in t.retired():
    with self.assertRaisesRegex(ValueError,'tool_entry_retired'):server.business_call(name,{})
 def test_partial_rollback_guidance_matches_enabled_components(self):
  import tool_surface as t
  with patch.object(t,'enabled',side_effect=lambda component=None:component is None or component=='knowledge'):
   guidance=t.guidance();self.assertIn('course_read',guidance)
   for absent in ['family_profile','student_tier','course_schedule','send_image','daily_context']:self.assertNotIn(absent,guidance)
 def test_disabled_surface_preserves_original_definitions(self):
  from types import SimpleNamespace
  import tool_surface as t
  defs=[('family_profile','legacy',{})];server=SimpleNamespace(base_mcp=SimpleNamespace(TOOLS=defs))
  with patch.object(t,'enabled',return_value=False):t.install(server)
  self.assertIs(server.base_mcp.TOOLS,defs)

if __name__=='__main__':unittest.main()
