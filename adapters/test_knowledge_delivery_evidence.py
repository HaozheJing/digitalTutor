"""Unified knowledge reads must satisfy existing course delivery guards exactly."""
from pathlib import Path
from unittest.mock import patch
import copy,hashlib,json,tempfile,unittest
import sql_tools as s,course_navigation as navigation,course_service,weekly_interaction

class UnifiedCourseEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        body='已登记第二课的完整正文。';sha=hashlib.sha256(body.encode()).hexdigest()
        self.doc={'document_id':'course-2','lesson_id':'L2-W01-D2','kind':'course','uri':'viking://resources/test/lesson2/course/content.md','sha256':sha}
        data={'lessons':[{'level':'L2','global_lesson_no':2,'lesson_id':'L2-W01-D2','resources':{'course':'course-2'}}],'documents':[self.doc]}
        path=root/'knowledge/curriculum/L2/catalog.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(data))
        change=patch.object(s,'R',root);change.start();self.addCleanup(change.stop)
        self.material={**self.doc,'body':body,'status':'ok','source_role':'curriculum_material'}
        self.result={'status':'partial','lesson_id':'L2-W01-D2','parts':{'course':self.material,'reading_qa':{'status':'not_available'}}}
    def call(self,result=None):return {'name':'knowledge_read','arguments':{'target':{'level':'L2','week':1,'day':2},'parts':['course']},'result':self.result if result is None else result}
    def match(self,result=None,lesson=2):return navigation.matching_material([self.call(result)],'L2',lesson,full_only=True)
    def test_registered_course_part_even_when_another_part_missing(self):
        self.assertEqual(self.match()['body'],self.material['body'])
    def test_registered_direct_document_read_accepted(self):
        self.assertEqual(self.match(self.material)['lesson_no'],2)
    def test_book_selector_uses_verified_returned_identity(self):
        call=self.call();call['arguments']={'target':{'book_id':'formal-book','book_lesson_no':2},'parts':['course']}
        self.assertIsNotNone(navigation.matching_material([call],'L2',2,full_only=True))
    def test_other_lesson_and_outer_mismatch_rejected(self):
        self.assertIsNone(self.match(lesson=3))
        changed=copy.deepcopy(self.result);changed['lesson_id']='L2-W01-D3';self.assertIsNone(self.match(changed))
    def test_qa_only_cannot_replace_course_body(self):
        changed=copy.deepcopy(self.result);changed['parts']={'reading_qa':self.material};self.assertIsNone(self.match(changed))
    def test_unavailable_changed_and_tampered_body_rejected(self):
        for fields in ({'status':'unavailable'},{'status':'changed'},{'body':'相同课次但不同正文'},{'sha256':'0'*64},{'uri':'viking://resources/other'},{'kind':'reading_qa'},{'document_id':'unregistered'}):
            with self.subTest(fields=fields):self.assertIsNone(self.match({**self.material,**fields}))
    def test_weekly_preparation_accepts_only_actually_completed_bound_lesson(self):
        snap={'profile':{'uid':'synthetic-family'},'weekly_learning':{'current_week':{'lessons':[{'status':'completed','lesson_no':2}]}}}
        with patch.object(course_service,'binding',return_value={'class_type':'L2'}):
            self.assertIsNone(weekly_interaction.preparation([self.call()],snap))
            snap['weekly_learning']['current_week']['lessons'][0].update(status='not_started')
            self.assertIsNotNone(weekly_interaction.preparation([self.call()],snap))

if __name__=='__main__':unittest.main()
