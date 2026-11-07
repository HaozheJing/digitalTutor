import unittest,json,datetime as dt,sqlite3
from contextlib import closing
import test_preference_memory as fixtures
import preference_memory as m,dialogue_context,sql_tools as s
def item(key,subject,note,seq):
 return {'key':key,'subject':subject,'basis':'reported','applicability':'persistent','note':note,'evidence':[seq]}
class IntegrationMemoryTests(unittest.TestCase):
 def test_regression_1(self):
  results={}
  case=fixtures.PreferenceMemoryTests();case.setUp()
  try:
   src=case.message('孩子喜欢恐龙故事，请记住。')
   ev=case.start();page=m.consolidation_context(ev);seq=next(x['seq'] for x in page['items'] if x['source_id']==src)
   case.finish(ev,candidates=[item('reading.interest','child','家长明确表示孩子喜欢恐龙故事',seq)])
   forget=case.message('请忘记孩子的阅读兴趣。')
   case.commit(case.change(forget,key='reading.interest',subject='child',action='forget'))
   case.now+=dt.timedelta(days=1)
   fresh=case.message('请重新记住，孩子现在明确喜欢太空故事。')
   ev=case.start();page=m.consolidation_context(ev);seq=next(x['seq'] for x in page['items'] if x['source_id']==fresh)
   try:case.finish(ev,candidates=[item('reading.interest','child','家长重新明确要求记住孩子现在喜欢太空故事',seq)]);error=None
   except ValueError as exc:error=str(exc)
   results['new_explicit_evidence_after_forget']={'new_message':fresh,'new_seq':seq,'error':error,'remaining':m.recall()['memories']}
   self.assertIsNone(error);self.assertEqual(len(m.recall()['memories']),1)
  finally:case.doCleanups()
 def test_regression_2(self):
  results={}
  case=fixtures.PreferenceMemoryTests();case.setUp()
  try:
   src=case.message('我叫林琳，孩子喜欢恐龙故事，请记住。')
   ev=case.start();page=m.consolidation_context(ev);seq=next(x['seq'] for x in page['items'] if x['source_id']==src)
   case.finish(ev,candidates=[item('reading.interest','child','家长明确表示孩子喜欢恐龙故事',seq),item('address.name','parent','家长自报称呼是林琳',seq)])
   before=m.recall()['memories']
   forget=case.message('请忘记孩子的阅读兴趣，保留我的称呼。')
   case.commit(case.change(forget,key='reading.interest',subject='child',action='forget'))
   results['shared_source_scoped_forget']={'before':[(x['subject'],x['key'],x['note']) for x in before],'after':[(x['subject'],x['key'],x['note']) for x in m.recall()['memories']],
     'preference_context_still_returns_forgotten_source':any(x['source_id']==src for x in m.context()['sources']),
     'dialogue_window_returns_forgotten_source':any(x['source_id']==src for x in dialogue_context.window(case.now.date().isoformat())['recent_conversation'])}
   self.assertEqual([(x['subject'],x['key']) for x in m.recall()['memories']],[('parent','address.name')])
  finally:case.doCleanups()
 def test_regression_3(self):
  results={}
  case=fixtures.PreferenceMemoryTests();case.setUp()
  try:
   # Trusted fixture setup represents a pre-v4 operational preference retained by migration.
   old=case.message('以后不要给我发图片。')
   case.commit(case.change(old,key='media.images',value='disabled'))
   fresh=case.message('我改主意了，现在允许你给我发图片，请按这个要求。')
   case.commit(case.change(fresh,key='media.images',value='allowed'))
   ev=case.start();page=m.consolidation_context(ev);seq=next(x['seq'] for x in page['items'] if x['source_id']==fresh)
   case.finish(ev,candidates=[item('media.images','parent','家长明确修正旧偏好，现在允许发送图片',seq)])
   try:
    with closing(sqlite3.connect(s.DB)) as c,c:m.guard_media(c,6)
    error=None
   except ValueError as exc:error=str(exc)
   results['legacy_preference_correction']={'active':[(x['key'],x['value']) for x in m.recall()['items']],
    'nightly_memories':[(x['key'],x['note']) for x in m.recall()['memories']],'image_guard_error':error}
   self.assertIsNone(error)
  finally:case.doCleanups()
 def test_regression_4(self):
  results={}
  case=fixtures.PreferenceMemoryTests();case.setUp()
  try:
   old=case.message('我叫林琳。')
   s.memory_remember('speaker.self_reported_name','林琳',old,'我叫林琳。')
   forget=case.message('请忘记我的名字，不要继续保留。')
   receipt=case.commit(case.change(forget,key='address.name',subject='parent',action='forget'))
   results['legacy_name_survives_forget']={'receipt_status':receipt['status'],'forgotten_keys':m.recall()['forgotten_keys'],
    'legacy_recall':[(x['memory_key'],x['value'],x['quote']) for x in s.memory_recall()],
    'original_still_in_dialogue':any(x['source_id']==old for x in dialogue_context.window(case.now.date().isoformat())['recent_conversation'])}
   self.assertEqual(s.memory_recall(),[]);self.assertFalse(results['legacy_name_survives_forget']['original_still_in_dialogue'])
  finally:case.doCleanups()

