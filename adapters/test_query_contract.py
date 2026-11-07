import unittest,sys,json,hashlib,types,tempfile,sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
from curriculum_knowledge import Curriculum,KnowledgeUnavailable
from query_contract import Knowledge,conversation_args,provenance,install,schedule_coordinates

ROOT=Path(__file__).resolve().parents[1]
class QueryTests(unittest.TestCase):
 def setUp(self):
  self.c=Curriculum(ROOT/'knowledge/curriculum/L2/catalog.json','unused')
  self.c.read=lambda ident:{**self.c.documents[ident],'body':'原文证据'}
  self.k=Knowledge(self.c,lambda:{'documents':[{'id':'faq-1','kind':'faq'}]},lambda ident:{'status':'ok','body':'导航','can_answer_directly':False},lambda *a:{'status':'ok','matches':[{'body':'导航','document_id':'faq-1','can_answer_directly':False}]})
 def test_search_read_handoff(self):
  r=self.k.search('L2 W1 Day1',scope='curriculum')
  for hit in r['matches']:
   self.assertEqual(self.k.call('knowledge_read',hit['read_args'])['status'],'ok')
 def test_document_parts_must_match_registered_kind(self):
  ident=next(k for k,v in self.c.documents.items() if v['kind']=='reading_qa')
  self.assertEqual(self.k.call('knowledge_read',{'target':{'document_id':ident},'parts':['reading_qa']})['status'],'ok')
  for parts in [[],['course'],['reading_qa','course'],['reading_qa','reading_qa']]:
   self.assertEqual(self.k.call('knowledge_read',{'target':{'document_id':ident},'parts':parts})['status'],'invalid_request')
 def test_all_coordinates(self):
  for w in range(1,49):
   for d in range(1,6):
    r=self.k.call('knowledge_read',{'target':{'level':'L2','week':w,'day':d},'parts':['reading_qa']})
    self.assertEqual(r['status'],'ok');self.assertEqual(r['lesson_id'],f'L2-W{w:02d}-D{d}')
 def test_partial(self):
  r=self.k.read(target={'level':'L2','week':40,'day':1},parts=['reading_qa','course'])
  self.assertEqual(r['status'],'partial');self.assertEqual(r['parts']['course']['status'],'not_available')
 def test_book_mapping(self):
  for book in self.c.books():
   r=self.k.read(target={'book_id':book['book_id'],'book_lesson_no':1},parts=['reading_qa'])
   self.assertEqual(r['lesson_id'],self.c.book(book['book_id'])['lessons'][0]['lesson_id'])
 def test_unknown_and_alias(self):
  self.assertEqual(self.k.read(document_id='unknown')['status'],'not_found')
  self.assertEqual(self.k.call('knowledge_browse',{'book_id':'狐狸爸爸'})['status'],'invalid_request')
 def test_invalid_selector(self):
  for a in [{'target':{'level':'L2','week':1,'day':1}}, {'target':{'level':'L2','week':1,'day':1,'book_id':'bad'},'parts':['course']}, {'document_id':'faq-1','parts':['course']}, {'target':{'level':'L2','week':True,'day':1},'parts':['course']}]:
   self.assertEqual(self.k.call('knowledge_read',a)['status'],'invalid_request')
 def test_exact_search_no_provider(self):
  with patch('curriculum_knowledge.ov',side_effect=AssertionError('semantic query forbidden')):
   r=self.k.search('L2 W1 Day1',scope='curriculum');self.assertTrue(r['matches']);self.assertTrue(r['requires_read'])
 def test_conflicting_book(self):
  b=self.c.books()[-1]['book_id']
  self.assertEqual(self.k.call('knowledge_search',{'query':'L2 W1 Day1','scope':'curriculum','book_id':b})['status'],'invalid_request')
 def test_business_candidate(self):
  r=self.k.search('退款');self.assertNotIn('body',r['matches'][0]);self.assertFalse(r['matches'][0]['can_answer_directly'])
  self.assertFalse(self.k.read(document_id='faq-1')['can_answer_directly'])
 def test_unavailable_and_changed(self):
  with patch.object(self.c,'read',side_effect=KnowledgeUnavailable('QuotaExceeded')):
   self.assertEqual(self.k.read(target={'level':'L2','week':1,'day':1},parts=['course'])['status'],'unavailable')
  with patch.object(self.c,'read',side_effect=ValueError('hash')):
   self.assertEqual(self.k.read(target={'level':'L2','week':1,'day':1},parts=['course'])['status'],'changed')
 def test_conversation(self):
  self.assertEqual(conversation_args({'source_id':'msg'})[0],'conversation_read')
  self.assertEqual(conversation_args({})[0],'conversation_window_read')
  self.assertEqual(conversation_args({'snapshot':'s','offset':10})[1]['snapshot'],'s')
  for a in [{'source_id':'m','offset':0},{'offset':1},{'offset':True},{'family_id':'other'},{'snapshot':''}]:
   with self.assertRaises(ValueError):conversation_args(a)
 def test_provenance_preserves_snapshot(self):
  r=provenance({'snapshot':'s','next_offset':9,'source_at':'old'},'conversation',True)
  self.assertEqual(r['source_at'],'old');self.assertEqual(r['next_offset'],9);self.assertFalse(r['context_contract']['grants_execution_authority'])
 def test_registration(self):
  def schema(p=None,r=()):return {'type':'object','properties':p or {},'required':list(r),'additionalProperties':False}
  server=types.SimpleNamespace(schema=schema,base_mcp=types.SimpleNamespace(TOOLS=[('send_message','',{}),('course_read','',{}),('conversation_window_read','',{})]))
  install(server);install(server)
  names=[t[0] for t in server.base_mcp.TOOLS]
  self.assertEqual(len(names),len(set(names)));self.assertNotIn('course_read',names);self.assertIn('send_message',names)
 def test_schedule_catalog_join(self):
  r=schedule_coordinates({'days':[{'lesson_no':6,'has_lesson':True},{'lesson_no':6,'has_lesson':None},{'lesson_no':999}]},'L2',self.c.data['lessons'])
  self.assertEqual(r['days'][0]['curriculum_ref']['week'],2)
  self.assertIsNone(r['days'][1]['curriculum_ref']);self.assertIsNone(r['days'][2]['curriculum_ref'])
 def test_readonly_scope_revokes_grants(self):
  # Execute the actual scoped-grant implementation against an isolated SQLite DB.
  import importlib.util
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/'x.sqlite';fake=types.SimpleNamespace(DB=db,now=lambda:'now')
   with patch.dict(sys.modules,{'sql_tools':fake}):
    spec=importlib.util.spec_from_file_location('isolated_scopes',ROOT/'adapters/skill_capabilities.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    owner={'key':'owner','kind':'dialogue','run_id':'r','uid':'f'}
    self.assertEqual(m.workflow_scope({'purpose':'amend'},{'context_token':'test-only'},owner),['workflow_commit'])
    self.assertEqual(m.workflow_scope({}, {'context_token':'test-only'},owner),[])
    with closing(sqlite3.connect(db)) as c:self.assertEqual(c.execute('select purpose from skill_workflow_grants').fetchone()[0],'inspect')
 def test_default_workflow_route(self):
  import importlib.util
  with patch.dict(sys.modules,{'sql_tools':types.SimpleNamespace()}):
   spec=importlib.util.spec_from_file_location('isolated_cap',ROOT/'adapters/capability_surface.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
   r=m.workflow_contract({'context_token':'test-only','owner':{'type':'dialogue'}})
   self.assertEqual(r['operation_contract']['mode'],'dialogue_inspect')

if __name__=='__main__':unittest.main(verbosity=2)
