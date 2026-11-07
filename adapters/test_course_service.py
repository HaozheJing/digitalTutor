import datetime as dt
import hashlib
import json
import unittest
from unittest.mock import patch
import course_service as cs
import test_s01_delivery as fixtures
import sql_tools as s
import observability as audit
import wechat_test as w


class ScheduleTests(unittest.TestCase):
    def test_weekdays_cohort_and_before_start(self):
        for day,number in [('2025-10-13',1),('2025-10-14',2),('2025-10-15',3),('2025-10-16',4),('2025-10-17',5),('2025-10-20',6),('2025-11-07',20),('2025-11-10',21)]:
            self.assertEqual(cs.course_schedule(day,'L2','61期')['lesson_no'],number)
        self.assertEqual(cs.course_schedule('2025-10-18','L2','61')['status'],'no_lesson')
        self.assertEqual(cs.course_schedule('2025-10-19','L2',61)['status'],'no_lesson')
        self.assertEqual(cs.course_schedule('2025-10-10','L2','61')['status'],'not_started')
        self.assertEqual(cs.course_schedule('2025-10-13','L2','62')['status'],'unknown')
        self.assertEqual(cs.course_schedule('2025-10-13','L3','61')['status'],'unknown')
        with self.assertRaises(ValueError):cs.course_schedule('2025-02-30','L2','61')

    def test_exact_knowledge_read_preserves_provenance_and_rejects_changed_body(self):
        item={**cs.entry('L2',1),'sha256':hashlib.sha256('正文'.encode()).hexdigest()}
        with patch.object(cs,'entry',return_value=item),patch.object(cs.knowledge,'cli',return_value='正文') as reader:
            result=cs.course_knowledge('L2',1)
            self.assertEqual(result['status'],'ok');self.assertTrue(result['live_read'])
            self.assertEqual(result['business_review_status'],'unverified')
            reader.assert_called_once_with(['read',item['uri']])
        with patch.object(cs.knowledge,'cli',return_value='错误课程'):
            self.assertEqual(cs.course_knowledge('L2',1)['status'],'changed')
        with patch.object(cs.knowledge,'cli',side_effect=RuntimeError):
            self.assertEqual(cs.course_knowledge('L2',1)['status'],'unavailable')
        self.assertEqual(cs.course_knowledge('L2',21)['status'],'no_match')
        self.assertIsNone(cs.entry('L3',1))


class CourseDeliveryTests(fixtures.S01DeliveryTests):
    def test_read_current_course_enables_model_invitation(self):
        self.begin();body='第一课讲三个饲养场场主，用人物信息卡认识他们。'
        item={**cs.entry('L2',1),'sha256':hashlib.sha256(body.encode()).hexdigest()}
        with patch.object(cs,'entry',return_value=item),patch.object(cs.knowledge,'cli',return_value=body):
            audit.invoke('course_schedule',{'date':'2025-10-13','class_type':'L2','cohort':'61'},lambda:cs.course_schedule('2025-10-13','L2','61'))
            audit.invoke('course_knowledge',{'class_type':'L2','lesson_no':1},lambda:cs.course_knowledge('L2',1))
            self.assertEqual(s.check('2025-10-13')['status'],'eligible')
            with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'course-test'}):
                result=s.deliver('2025-10-13','侨侨，第一课已解锁，一起来认识三个饲养场场主吧！家长可以转告孩子，找个方便的时间开始阅读。')
            self.assertIn('三个饲养场场主',result['body']);self.assertEqual(result['status'],'accepted')

    def test_mismatched_lesson_or_unread_course_cannot_enable_rich_text(self):
        self.begin()
        self.assertNotIn('content_mode',s.check('2025-10-13'))
        audit.invoke('course_knowledge',{'class_type':'L2','lesson_no':2},lambda:{'status':'ok','live_read':True,'body':'wrong','sha256':'fake'})
        self.assertNotIn('content_mode',s.check('2025-10-13'))
        with patch.object(cs,'course_schedule',return_value={'status':'scheduled','class_type':'L2','cohort':'61','lesson_no':2,'lesson_id':'L2-002'}):
            self.assertEqual(s.check('2025-10-13')['status'],'unknown')


if __name__=='__main__':unittest.main()
