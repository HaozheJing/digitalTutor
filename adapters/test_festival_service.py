import hashlib
import json
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch
import agent_plan as ap
import business as b
import festival_service as f
import observability as audit
import proactive as p
import sql_tools as s
import test_fixture_integration as fixtures
import test_mode as tm
import wechat_test as w


class FestivalTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def begin(self):
        tm.activate('2025-11-05 19:00',True)
        state=tm.state();state['task_scope']=['festival']
        state['festival_simulation']={'run_id':state['run_id'],'day':'2025-11-05','festival':'mid-autumn'}
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(json.dumps(state),))
        ap.enable();task=ap.claim();self.assertEqual(task['kind'],'festival')
        for name in [f.RULE,'reply-style']:ap.load_skill(name)
        for name,fn in [('family_profile',s.family_profile),('learning_query',tm.snapshot),
                        ('daily_context',lambda:b.context('2025-11-05')),('action_candidates',lambda:b.candidates('2025-11-05'))]:
            audit.invoke(name,{},fn)
        candidate=next(x for x in p.refresh('2025-11-05') if x['skill']==f.RULE)
        audit.invoke('check_proactive',{'candidate_id':b.token(candidate['id'])},lambda:b.project(p.check(candidate['id'])))
        f.initialize()
        return task,candidate

    def asset(self, task, inspected=True):
        self.root.joinpath('artifacts/media').mkdir(parents=True,exist_ok=True)
        data=b'fixture-image';sha=hashlib.sha256(data).hexdigest()
        file=self.root/'artifacts/media'/(sha+'.png');file.write_bytes(data)
        url='https://82.156.162.52/festival-media/'+file.name
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO festival_images VALUES(?,?,?,?,?,?,?,?,?)',
                      ('asset-1',tm.state()['run_id'],task['id'],'prompt',str(file),sha,url,'checked' if inspected else None,s.now()))
        return file,url

    def test_calendar_and_scoped_simulation(self):
        self.assertEqual(f.event({'as_of':'2026-09-25'})['festival'],'mid-autumn')
        self.assertEqual(f.event({'as_of':'2025-01-28'})['festival'],'new-year-eve')
        self.assertIsNone(f.event({'as_of':'2025-11-05'}))
        state={'as_of':'2025-11-05','run_id':'new','task_scope':['festival'],
               'festival_simulation':{'run_id':'old','day':'2025-11-05','festival':'mid-autumn'}}
        self.assertIsNone(f.event(state))
        state['festival_simulation']['run_id']='new'
        self.assertTrue(f.event(state)['simulated'])
        state['task_scope']=['interaction'];self.assertIsNone(f.event(state))

    def test_no_task_on_normal_day_and_auto_off(self):
        tm.activate('2025-11-05 19:00',False);ap.enable()
        self.assertFalse(any(t['kind']=='festival' for t in ap.tasks()))
        with self.assertRaises(ValueError):f.task()

    def test_explicit_one_shot_keeps_automatic_off(self):
        task,candidate=self.begin()
        state=tm.state();state['auto_highlights']=False;state['manual_service_task_id']=task['id']
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='planned' WHERE id=?",(task['id'],))
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(json.dumps(state),))
        self.assertEqual(ap.claim()['id'],task['id'])
        self.assertFalse(tm.state()['auto_highlights'])
        self.assertNotIn('manual_service_task_id',tm.state())
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET status='deferred' WHERE id=?",(task['id'],))
        self.assertIsNone(ap.claim())

    def test_independent_skill_and_plain_text_rejected(self):
        task,candidate=self.begin()
        self.assertTrue(ap.receipt_driven(task))
        self.assertEqual(ap.load_skill('service-policy')['status'],'not_applicable')
        self.assertIsNone(ap.load_skill(f.RULE)['required_shared_skill'])
        ap.guard_delivery([candidate['id']])
        with self.assertRaisesRegex(ValueError,'requires'):
            p.deliver([candidate['id']],'侨侨宝贝，中秋快乐～')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_foreign_or_uninspected_images_rejected(self):
        task,candidate=self.begin();self.asset(task,False)
        with patch.object(s,'R',self.root):
            with self.assertRaisesRegex(ValueError,'inspected'):
                p.deliver([candidate['id']],'侨侨，中秋快乐～',generated_asset_id='asset-1')
            self.assertFalse(f.allowed_public_image('https://example.com/picture.png'))
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE festival_images SET run_id='other'")
            with self.assertRaisesRegex(ValueError,'registered'):f.read_asset('asset-1')

    def test_bundle_requires_both_receipts_and_never_resends(self):
        task,candidate=self.begin();file,url=self.asset(task)
        with patch.object(s,'R',self.root),patch.object(w,'http_send',return_value={'errcode':0,'requestId':'receipt'}) as send:
            result=p.deliver([candidate['id']],'侨侨宝贝，中秋快乐～',generated_asset_id='asset-1')
            self.assertEqual(result['delivery_format'],'text_and_generated_image')
            ap.link_delivery(result);w.flush()
            self.assertEqual(send.call_count,2)
            self.assertEqual(send.call_args_list[1].args[-2:],(url,6))
            key=result['action_key']
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute("UPDATE test_outbox SET status='sent' WHERE message_type=7")
                self.assertNotEqual(w.reconcile_bundle(c,key),'sent')
                c.execute("UPDATE test_outbox SET status='sent' WHERE message_type=6")
                self.assertEqual(w.reconcile_bundle(c,key),'sent')
            self.assertTrue(p.deliver([candidate['id']],'侨侨宝贝，中秋快乐～',generated_asset_id='asset-1')['duplicate'])
            w.flush();self.assertEqual(send.call_count,2)
        self.assertEqual(ap.finish(task['id'],'completed','图文送达')['status'],'done')

    def test_pause_preserved_and_tamper_rejected(self):
        task,candidate=self.begin();file,url=self.asset(task)
        with patch.object(s,'R',self.root):
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-11-05','x','pause',s.now()))
            self.assertEqual(p.deliver([candidate['id']],'侨侨，中秋快乐～',generated_asset_id='asset-1')['status'],'skipped')
            file.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'integrity'):f.allowed_public_image(url)

    def test_experiment_publication_never_writes_public_directory(self):
        task,candidate=self.begin();file,url=self.asset(task)
        import experiment_runtime
        manifest={'experiment_run_id':'exp-isolated'}
        with patch.object(s,'R',self.root),patch.dict('os.environ',{'TUTOR_EXPERIMENT_ID':'exp-isolated'}),patch.object(experiment_runtime,'verify',return_value=manifest),patch.object(f.shutil,'copyfile') as copy,patch.object(f,'urlopen') as network:
            result=f.publish_asset(f.read_asset('asset-1'))
            self.assertTrue(result.startswith('https://experiment.invalid/festival-media/exp-isolated/'))
            copy.assert_not_called();network.assert_not_called()

    def test_isolated_inspector_uses_its_own_parameters_and_persists_evidence(self):
        task,candidate=self.begin();file,url=self.asset(task,False)
        import io,experiment_media
        private=self.root/'inspector.json'
        private.write_text(json.dumps({'base_url':'https://model.invalid','api_key':'fake','vlm_model':'vision-test','inspection_parameters':{'max_tokens':8192}}))
        response=io.BytesIO(json.dumps({'id':'inspection-test','choices':[{'message':{'content':'画面包含书本与灯笼，无可读文字。'}}],'usage':{'total_tokens':12}}).encode())
        with patch.object(s,'R',self.root),patch.object(f,'task',return_value=task),patch.object(experiment_media,'configuration',return_value=private),patch.object(f,'urlopen',return_value=response) as api:
            result=f.inspect_festival_image('asset-1')
        body=json.loads(api.call_args.args[0].data)
        self.assertEqual(body['model'],'vision-test');self.assertEqual(body['max_tokens'],8192)
        self.assertNotIn('thinking',body)
        self.assertTrue(body['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(result['sha256'],hashlib.sha256(file.read_bytes()).hexdigest())
        evidence=json.loads(next((self.root/'artifacts/media').glob('inspection-*.json')).read_text())
        self.assertEqual(evidence['response_id'],'inspection-test')
        self.assertEqual(evidence['parameters'],{'stream':False,'max_tokens':8192})
        self.assertNotIn('fake',json.dumps(evidence))

    def test_missing_media_provider_reports_unavailable(self):
        task,candidate=self.begin()
        with patch.object(s,'R',self.root),patch.object(f,'task',return_value=task):
            result=f.generate_image('Test festival illustration','isolated-op')
        self.assertEqual(result['status'],'unavailable')
        self.assertFalse(result['delivery_completed'])


if __name__=='__main__':unittest.main()
