import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import material_cache as m
import sql_tools as s
class MaterialTests(unittest.TestCase):
 def test_complete_paging_reuse_and_isolation(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(s,'DB',Path(tmp)/'db'),patch('test_mode.state',return_value={'run_id':'run','uid':'A'}) as state:
   source={'body':'第三题'*6000,'version':'v1','uri':'course/lesson1'}
   first=m.capture('course_read',{'uri':source['uri']},source);second=m.capture('course_read',{'uri':source['uri']},source)
   self.assertEqual(first['material_ref'],second['material_ref']);self.assertEqual(len(m.index()),1)
   offset=0;parts=[]
   while True:
    page=m.read(first['material_ref'],offset);parts.append(page['json_fragment']);offset=page['next_offset']
    if offset is None:break
   self.assertEqual(json.loads(''.join(parts)),source)
   self.assertFalse(page['authority'])
   self.assertNotEqual(m.capture('course_read',{},dict(source,version='v2'))['material_ref'],first['material_ref'])
   state.return_value={'run_id':'run','uid':'B'}
   self.assertEqual(m.index(),[])
   with self.assertRaisesRegex(ValueError,'not_in_current_family'):m.read(first['material_ref'])
 def test_dynamic_facts_are_not_cached(self):
  value={'completed':False}
  self.assertIs(m.capture('learning_query',{},value),value)

 def test_large_learning_material_has_scoped_selective_read(self):
  import working_context as w
  with tempfile.TemporaryDirectory() as tmp,patch.object(s,'DB',Path(tmp)/'db'),patch('test_mode.state',return_value={'run_id':'run','uid':'A'}):
   source={'learning':{'lessons':[{'id':'lesson1','body':'课文'*20000}],'current':{'completed':False}},'constraints':{'pause':True}}
   view=w.model(source);self.assertTrue(view['learning']['requires_read']);self.assertEqual(view['constraints'],source['constraints'])
   page=w.page(view['context_archive']['context_id'],'learning',path=['current'])
   self.assertEqual(json.loads(page['json_fragment']),{'completed':False})
   with self.assertRaisesRegex(ValueError,'invalid_context_path'):w.page(view['context_archive']['context_id'],'learning',path=['lessons',-1])

 def test_restart_cleanup_does_not_confuse_previous_attempt_with_new_running_owner(self):
  import context_lifecycle as lifecycle
  row={'id':'t','status':'running','native_request_id':'new-request'}
  with patch('test_mode.state',return_value={'run_id':'run','uid':'A'}),patch.object(s,'read',return_value=[row]),patch('material_cache.index',return_value=[]):
   self.assertFalse(lifecycle.checkpoint('t','new-request')['ready'])
  with patch('test_mode.state',return_value={'run_id':'run','uid':'A'}),patch.object(s,'read',side_effect=[[row],[]]),patch('material_cache.index',return_value=[]):
   result=lifecycle.checkpoint('t','old-request');self.assertTrue(result['ready']);self.assertTrue(result['recovered_previous_attempt']);self.assertEqual(result['task']['status'],'running')
