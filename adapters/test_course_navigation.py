import hashlib
import unittest
from unittest.mock import patch
import course_navigation as nav
import course_service as cs
import s01_contract
import s08_service
import test_fixture_integration as fixtures
import test_mode as tm


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.points=nav.entry_points('L2',10)
        self.root=self.points['book_uri'];self.lesson=self.points['lesson_uri'];self.file=self.points['content_uri']

    def test_manifest_matches_all_twenty_exact_course_bindings(self):
        self.assertEqual(len(nav.catalog()['documents']),28)
        for number in range(1,21):
            points=nav.entry_points('L2',number);original=cs.entry('L2',number)
            self.assertEqual(points['content_uri'],original['uri'])
            self.assertEqual(nav.resolve(points['content_uri'])['sha256'],original['sha256'])
            self.assertEqual(points['week_uri'],points['lesson_uri'].rsplit('/',1)[0])
        self.assertIsNone(nav.entry_points('L3',1))

    def test_unregistered_and_ambiguous_addresses_never_reach_openviking(self):
        with patch.object(nav.knowledge,'cli') as cli:
            for uri in [self.root+'/../other',self.root+'/.abstract.md',self.root+'?x=1',self.root+'#x',
                        self.root+'/%2e%2e/x',self.root.replace('nosplit-v2','nosplit-v1'),
                        self.root+'-other','https://example.com/','viking://resources/other']:
                with self.subTest(uri=uri),self.assertRaises(ValueError):nav.course_read(uri)
            cli.assert_not_called()

    def test_browse_is_lazy_scoped_and_paginated(self):
        week=self.points['week_uri']
        with patch.object(nav.knowledge,'cli',return_value=[{'uri':week},{'uri':self.file},{'uri':self.root+'/unknown'}]) as cli:
            result=nav.course_browse(self.root,limit=3)
        self.assertEqual([x['uri'] for x in result['children']],[week])
        self.assertNotIn('body',result['children'][0]);self.assertEqual(result['next_offset'],3)
        cli.assert_called_once_with(['ls',self.root,'--offset','0','--limit','3'])

    def test_search_only_returns_relevant_registered_locations_without_fetching_bodies(self):
        found={'resources':[{'uri':self.lesson+'/.overview.md','abstract':'本周复盘','score':.8},
                            {'uri':self.lesson+'/.abstract.md','abstract':'重复'},
                            {'uri':self.file,'abstract':'推测表达'},
                            {'uri':nav.entry_points('L2',1)['content_uri']},
                            {'uri':self.root+'/unknown/content.md'}]}
        with patch.object(nav.knowledge,'cli',return_value=found) as cli:
            result=nav.course_search('推测表达',scope_uri=self.points['week_uri'])
        self.assertEqual([x['uri'] for x in result['matches']],[self.lesson,self.file])
        self.assertTrue(all('body' not in x for x in result['matches']))
        self.assertEqual(cli.call_count,1);self.assertEqual(cli.call_args.args[0][0],'find')

    def test_chosen_depth_and_version_validation(self):
        with patch.object(nav.knowledge,'cli',return_value='复盘与表达') as cli:
            result=nav.course_read(self.lesson,'overview')
            cli.assert_called_once_with(['overview',self.lesson])
        self.assertEqual(result['lesson_no'],10);self.assertEqual(result['detail'],'overview')
        self.assertEqual(result['business_review_status'],'unverified')
        with patch.object(nav.knowledge,'cli',return_value='变更正文'):
            self.assertEqual(nav.course_read(self.file,'full')['status'],'changed')
        item={**nav.resolve(self.file),'sha256':hashlib.sha256('核对正文'.encode()).hexdigest()}
        with patch.object(nav,'resolve',return_value=item),patch.object(nav.knowledge,'cli',return_value='核对正文'):
            self.assertEqual(nav.course_read(self.file,'full')['body'],'核对正文')
        with patch.object(nav.knowledge,'cli',side_effect=RuntimeError) as cli:
            self.assertEqual(nav.course_read(self.lesson,'abstract')['status'],'unavailable')
            self.assertEqual(cli.call_count,1)
        with self.assertRaises(ValueError):nav.course_read(self.root,'full')
        with patch.object(nav.knowledge,'cli',return_value='概览') as cli:
            self.assertEqual(nav.course_read(self.file,'overview')['uri'],self.lesson)
            cli.assert_called_once_with(['overview',self.lesson])
        with patch.object(nav.knowledge,'cli',return_value='变更正文') as cli:
            self.assertEqual(nav.course_read(self.lesson,'full')['status'],'changed')
            cli.assert_called_once_with(['read',self.file])

    def trace(self):
        day='2025-10-24';schedule=cs.course_schedule(day,'L2','61')
        return [{'name':'course_schedule','arguments':{'date':day,'class_type':'L2','cohort':'61'},'result':schedule},
                {'name':'learning_query','arguments':{'day':day},'result':{'lessons_as_of':[{'scheduled_date':day,'lesson_id':'L2-010','lesson_no':10,'status':'in_progress'}]}}]

    def reading(self,number,detail):
        points=nav.entry_points('L2',number);uri=points['content_uri'] if detail=='full' else points['lesson_uri']
        return {'name':'course_read','arguments':{'uri':uri,'detail':detail},
                'result':{'status':'ok','uri':uri,'class_type':'L2','lesson_no':number,'detail':detail,'body':'相应课程资料'}}

    def test_s08_accepts_exact_lesson_overview_and_full_from_new_reader(self):
        for detail in ['abstract','overview','full']:
            with self.subTest(detail=detail):self.assertIsNone(s08_service.preparation(self.trace()+[self.reading(10,detail)],'2025-10-24'))
        call=self.reading(10,'full');call['arguments']['uri']=self.lesson
        self.assertIsNone(s08_service.preparation(self.trace()+[call],'2025-10-24'))

    def test_search_other_lesson_and_week_summary_cannot_stand_in_for_today(self):
        trace=self.trace()
        self.assertIsNotNone(s08_service.preparation(trace+[self.reading(9,'full')],'2025-10-24'))
        week={'name':'course_read','arguments':{'uri':self.points['week_uri'],'detail':'overview'},
              'result':{'status':'ok','body':'整周概览','uri':self.points['week_uri'],'detail':'overview','class_type':'L2'}}
        self.assertIsNotNone(s08_service.preparation(trace+[week],'2025-10-24'))
        hit={'name':'course_search','arguments':{'query':'复盘'},'result':{'status':'ok','matches':[{'uri':self.file}]}}
        self.assertIsNotNone(s08_service.preparation(trace+[hit],'2025-10-24'))

    def test_s01_keeps_fulltext_requirement_but_accepts_new_reader(self):
        trace=self.trace();schedule=trace[0]['result']
        self.assertIsNotNone(s01_contract.preparation(trace+[self.reading(10,'overview')],schedule,'2025-10-24'))
        self.assertIsNone(s01_contract.preparation(trace+[self.reading(10,'full')],schedule,'2025-10-24'))


class KnowledgeContextTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def test_time_bounded_learning_carries_entries_for_visible_lessons(self):
        tm.activate('2025-10-24 19:00',False)
        snapshot=tm.snapshot()
        self.assertEqual(snapshot['course_knowledge']['book_uri'],nav.catalog()['root_uri'])
        self.assertEqual(snapshot['lessons_as_of'][-1]['lesson_no'],10)
        self.assertTrue(all(row['scheduled_date']<='2025-10-24' for row in snapshot['lessons_as_of']))
        for row in snapshot['lessons_as_of']:
            self.assertEqual(row['knowledge_uri'],nav.entry_points('L2',row['lesson_no'])['lesson_uri'])
        self.assertIn('course_read',tm.context('2025-10-24')['course_material_status'])


if __name__=='__main__':unittest.main()
