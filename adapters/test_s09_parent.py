import copy,unittest
import s09_service as svc,weekly_materials as wm

class S09ParentTests(unittest.TestCase):
    def snapshot(self):
        rows=[{'lesson_id':str(n),'lesson_no':n,'scheduled_date':date,'status':status,'title':'第'+str(n)+'课'} for n,date,status in [(4,'2025-10-16','absent'),(5,'2025-10-17','in_progress'),(6,'2025-10-20','completed'),(9,'2025-10-23','absent'),(10,'2025-10-24','not_started'),(11,'2025-10-27','not_started')]]
        return {'as_of':'2025-10-24T20:00:00+08:00','weekly_learning':{'current_week':{'start':'2025-10-20','coverage_complete':True}},'lessons_as_of':rows}
    def test_s09_friday_still_has_no_fixed_file(self):
        from unittest.mock import patch
        with patch('test_mode.state',return_value={'as_of':'2025-10-24T20:00:00+08:00'}):
            self.assertEqual(wm.queue()['status'],'not_due')
    def test_four_missing_two_historical_and_two_current(self):
        result=svc.makeup_scope(self.snapshot())
        self.assertEqual([x['lesson_no'] for x in result['ordered_lessons']],[4,5,9,10])
        self.assertEqual((result['historical_count'],result['current_week_count'],result['total_known_missing']),(2,2,4))
        self.assertIsNone(result['duration_basis'])
    def test_reminder_scope_includes_historical_lessons(self):
        import proactive
        item={'rule':'plan-weekend-makeup','evidence':{'overdue_lessons':svc.makeup_scope(self.snapshot())['ordered_lessons']}}
        self.assertEqual(proactive.reminder_lessons(item),{'4','5','9','10'})
    def test_unsourced_duration_and_progress_require_revision(self):
        for text in ['安排二十来分钟把第5课读完','每节15分钟，两节半个小时','第5课读到一半','只剩一点的第5课']:
            with self.subTest(text=text):self.assertIsNotNone(svc.review_body(text,self.snapshot()))
        self.assertIsNone(svc.review_body('周六上午读第4课、接着读已开始的第5课；周日上午读第9、10课，您看合适吗？',self.snapshot()))

    def test_conflicting_records_make_candidate_unknown(self):
        from unittest.mock import MagicMock
        s=self.snapshot();s['weekly_learning']['current_week'].update(scheduled=2,lessons=s['lessons_as_of'][3:5])
        row=copy.deepcopy(s['lessons_as_of'][0]);row['status']='completed';s['lessons_as_of'].append(row)
        c=MagicMock();c.execute.return_value.fetchone.return_value=None
        result=svc.decision(c,{'as_of':s['as_of'],'run_id':'test'},s)
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['evidence']['overdue_lessons'],[])

    def test_same_record_deduped_and_conflicting_status_unknown(self):
        s=self.snapshot();s['lessons_as_of'].append(copy.deepcopy(s['lessons_as_of'][0]));self.assertEqual(svc.makeup_scope(s)['total_known_missing'],4)
        s['lessons_as_of'][-1]['status']='completed';self.assertEqual(svc.makeup_scope(s)['status'],'unknown')
    def test_unknown_state_is_not_missing_and_fresh_completion_removed(self):
        s=self.snapshot();s['lessons_as_of'][0]['status']='unknown';r=svc.makeup_scope(s);self.assertEqual(r['status'],'incomplete');self.assertEqual(r['total_known_missing'],3)
        s['lessons_as_of'][0]['status']='completed';r=svc.makeup_scope(s);self.assertEqual(r['total_known_missing'],3);self.assertNotIn(4,[x['lesson_no'] for x in r['ordered_lessons']])
if __name__=='__main__':unittest.main()
