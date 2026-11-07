from contextlib import closing
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import passive_context as p
import sql_tools as s

class PassiveCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        q=patch.object(s,'DB',Path(self.tmp.name)/'db');q.start();self.addCleanup(q.stop)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('CREATE TABLE messages(source_id TEXT,speaker TEXT,body TEXT,visible INTEGER,created_at TEXT)')
        self.state={'run_id':'run','uid':'A','as_of':'2025-11-07T12:00:00+08:00'}
        self.data={'uid':'A','enrollments':[{'id':'enroll-A'}],'learning_events':[]}
        for target,kw in [('passive_context.enabled',{'return_value':True}),
                          ('passive_context.scope_data',{'side_effect':lambda:(self.sc,self.state,self.data)}),
                          ('completion_events.saved',{'return_value':[]})]:
            q=patch(target,**kw);q.start();self.addCleanup(q.stop)
        self.sc='scope-A';self.provider=Mock(return_value={'as_of':self.state['as_of'],'summary_as_of':{'completed':19},'lessons_as_of':[{'scheduled_date':'2025-11-07','status':'not_started'}],'events':[{'body':'history'*10000}]})

    def query(self):return p.query('learning_query',{},self.provider)

    def test_hour_reuse_expiry_replaces_head_and_stale_ref_denied(self):
        a=self.query();self.assertFalse(a['passive_cache']['cache_hit']);self.assertLess(len(json.dumps(a)),3000)
        self.state['as_of']='2025-11-07T12:59:59+08:00';b=self.query()
        self.assertEqual(a['material_ref'],b['material_ref']);self.assertEqual(self.provider.call_count,1)
        self.state['as_of']='2025-11-07T13:00:00+08:00';c=self.query()
        self.assertNotEqual(a['material_ref'],c['material_ref']);self.assertEqual(self.provider.call_count,2)
        self.assertEqual(p.read(a['material_ref'])['status'],'stale')
        with closing(sqlite3.connect(s.DB)) as db,db:
            self.assertEqual(db.execute('SELECT count(*) FROM passive_material_heads').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM passive_materials').fetchone()[0],2)

    def test_parent_signal_invalidates_but_does_not_create_completion(self):
        a=self.query()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO messages(source_id,speaker,body,visible) VALUES(?,?,?,1)',('new','user','刚刚已经读完了，请看看记录'))
        self.assertEqual(p.materials(),[])
        b=self.query();self.assertNotEqual(a['material_ref'],b['material_ref'])
        self.assertEqual(b['lessons_as_of'][0]['status'],'not_started')

    def test_reference_to_previously_queried_learning_is_not_a_change_signal(self):
        a=self.query()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO messages(source_id,speaker,body,visible) VALUES(?,?,?,1)',('reuse','user','沿用刚才查到的学情和读过的课文就好，这会儿没有新的学习进展。'))
        b=self.query()
        self.assertEqual(a['material_ref'],b['material_ref'])
        self.assertEqual(self.provider.call_count,1)
        self.assertTrue(b['passive_cache']['cache_hit'])
        self.assertEqual(p.materials(True)[0]['material_ref'],a['material_ref'])

    def test_feed_event_changes_and_cross_midnight_invalidate(self):
        a=self.query();self.data['learning_events']=[{'id':'quiz','occurred_at':'2025-11-07T12:00:00+08:00','kind':'quiz'}]
        self.assertEqual(p.read(a['material_ref'])['status'],'stale')
        b=self.query();self.state['as_of']='2025-11-08T00:00:00+08:00'
        self.assertEqual(p.read(b['material_ref'])['status'],'stale')

    def test_family_enrollment_and_query_scope_do_not_share(self):
        a=self.query();self.sc='scope-B'
        with self.assertRaisesRegex(ValueError,'current_family'):p.read(a['material_ref'])
        b=self.query();self.assertNotEqual(a['material_ref'],b['material_ref'])
        self.sc='scope-A'
        c=p.query('learning_query',{'day':'2025-11-06'},self.provider)
        self.assertNotEqual(a['material_ref'],c['material_ref'])

    def test_versioned_knowledge_survives_hour_and_is_revoked_by_publication(self):
        provider=Mock(return_value={'status':'ok','body':'兔子课程','live_read':True})
        with patch('course_service.entry',return_value={'uri':'course20','sha256':'v1'}) as version:
            a=p.query('course_knowledge',{'class_type':'L2','lesson_no':20},provider)
            self.state['as_of']='2025-11-08T12:00:00+08:00'
            b=p.query('course_knowledge',{'class_type':'L2','lesson_no':20},provider)
            self.assertEqual(a['material_ref'],b['material_ref']);self.assertFalse(b['live_read']);self.assertEqual(provider.call_count,1)
            version.return_value={'uri':'course20','sha256':'v2'}
            self.assertEqual(p.read(a['material_ref'])['status'],'stale')
            c=p.query('course_knowledge',{'class_type':'L2','lesson_no':20},provider)
            self.assertNotEqual(a['material_ref'],c['material_ref'])

    def test_cache_read_paging_preserves_full_snapshot_and_active_bypasses(self):
        a=self.query();parts=[];offset=0
        while True:
            page=p.read(a['material_ref'],offset);parts.append(page['json_fragment']);offset=page['next_offset']
            if offset is None:break
        self.assertEqual(json.loads(''.join(parts)),self.provider.return_value)
        with patch.object(p,'enabled',return_value=False):self.assertIs(p.query('learning_query',{},self.provider),self.provider.return_value)
        self.assertEqual(self.provider.call_count,2)

    def test_registered_faq_body_reuses_version_without_claiming_approved_answer(self):
        import knowledge_search as knowledge
        body='导航资料，不是已批准答案'
        catalog={'documents':[{'id':'faq1','uri':'viking://resources/faq1','sha256':p.hashlib.sha256(body.encode()).hexdigest()}]}
        with patch.object(knowledge,'catalog',return_value=catalog),patch.object(knowledge,'cli',return_value=body) as provider:
            a=knowledge.read_document('faq1');b=knowledge.read_document('faq1')
            self.assertEqual(provider.call_count,1);self.assertEqual(a['material_ref'],b['material_ref'])
            self.assertFalse(b['live_read']);self.assertFalse(b['can_answer_directly'])
            self.assertEqual(b['current_skill_routes'],a['current_skill_routes'])
            catalog['documents'][0]['sha256']='new-version'
            self.assertEqual(p.read(a['material_ref'])['status'],'stale')

    def test_changed_feed_during_query_is_not_cached(self):
        def change():
            self.data['learning_events'].append({'id':'new','occurred_at':self.state['as_of']})
            return self.provider.return_value
        with self.assertRaisesRegex(ValueError,'changed_during_read'):p.query('learning_query',{},change)
        self.assertEqual(p.materials(),[])

    def test_learning_context_does_not_cache_contact_state(self):
        with patch('test_mode.snapshot',return_value=self.provider.return_value) as provider:
            p.query('learning_context',{},Mock(side_effect=AssertionError('volatile aggregate called')))
            provider.assert_called_once()

    def test_unknown_receipt_and_original_parent_words_survive_checkpoint(self):
        with patch('test_mode.state',return_value=self.state),patch.object(s,'read',side_effect=[
            [{'id':'e','status':'needs_attention','payload':json.dumps({'source_message_id':'m'})}],
            [{'action_key':'reply:e','body':'outbound','status':'unknown'}]]),patch.object(s,'conversation_read',return_value=[{'body':'先别发，等我确认'}]):
            checkpoint=p.checkpoint('e','rpc')
            self.assertEqual(checkpoint['deliveries'][0]['status'],'unknown')
            self.assertEqual(checkpoint['parent_messages'][0]['body'],'先别发，等我确认')

    def test_entry_does_not_eagerly_query_learning_or_load_actions(self):
        self.data['profile']={'child_nickname':'侨侨','guardian_role':'mother'}
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('CREATE TABLE contact_constraints(day TEXT,quote TEXT)')
            c.execute("INSERT INTO contact_constraints VALUES('2025-11-07','今天别提醒')")
            c.execute('CREATE TABLE service_workflow_constraints(id TEXT,run_id TEXT,uid TEXT,scope TEXT,starts_at TEXT,ends_at TEXT,source TEXT)')
            c.execute("INSERT INTO service_workflow_constraints VALUES('pause','run','A','all','2025-11-07','2025-11-08','家长明确约定')")
            c.execute("INSERT INTO service_workflow_constraints VALUES('foreign','run','B','all','2025-11-07','2025-11-08','其他家庭')")
        with (patch('test_mode.snapshot',side_effect=AssertionError('eager learning read')),
              patch('preference_memory.enabled',return_value=False),patch('course_service.binding',return_value={})):
            value=p.entry('2025-11-07')
        self.assertNotIn('learning',value);self.assertNotIn('actions',value)
        self.assertEqual(value['contact_constraints'][0]['quote'],'今天别提醒')
        self.assertEqual([c['id'] for c in value['workflow_constraints']],['pause'])

if __name__=='__main__':unittest.main()
