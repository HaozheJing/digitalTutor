import unittest
from tool_result_contract import annotate
class Labels(unittest.TestCase):
 def test_read(self):
  r=annotate('knowledge_read',{}, {'status':'ok','parts':{'reading_qa':{'status':'ok','body':'source'}}})
  self.assertEqual(r['result_evidence']['retrieval']['state'],'read');self.assertEqual(r['result_evidence']['verification']['state'],'unknown')
 def test_partial(self):
  r=annotate('knowledge_read',{}, {'status':'partial','parts':{'reading_qa':{'status':'ok','body':'source'},'course':{'status':'not_available'}}})
  self.assertEqual(r['result_evidence']['retrieval']['state'],'partial');self.assertEqual(r['result_evidence']['retrieval']['parts']['course'],'not_available')
 def test_unavailable(self):
  r=annotate('knowledge_read',{}, {'status':'unavailable','parts':{'course':{'status':'unavailable'}}})
  self.assertEqual(r['result_evidence']['retrieval']['state'],'unavailable')
 def test_browse(self):
  r=annotate('knowledge_browse',{}, {'status':'catalog','books':[]})
  self.assertEqual(r['result_evidence']['retrieval']['state'],'catalog');self.assertNotEqual(r['result_evidence']['verification']['state'],'verified')
if __name__=='__main__':unittest.main()
