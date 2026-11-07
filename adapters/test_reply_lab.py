import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from reply_lab import ReplyLab, Fault, digest
from teacher_workbench import TeacherStudio


class ReplyLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name in ['reply-style', 'tutor-main']:
            folder = root / 'skills' / name
            folder.mkdir(parents=True)
            (folder / 'SKILL.md').write_text('---\nname: '+name+'\ndescription: test\n---\nUse facts and respect user requests. Never invent completed operations.\n', encoding='utf8')
        self.studio = TeacherStudio(root)
        self.addCleanup(self.studio.pool.shutdown)
        self.lab = ReplyLab(self.studio)
        self.addCleanup(self.lab.close)
        self.studio._reply_lab = self.lab
        self.lab.code_identity = lambda: ('a' * 40, False)
        self.actor = {'id': 't1', 'name': 'same display name', 'admin': False}
        self.other = {'id': 't2', 'name': 'same display name', 'admin': True}

    def scenario(self, **changes):
        p = {'mode': 'passive', 'origin': 'synthetic', 'review': 'candidate', 'family_group': 'f1', 'split': 'development',
             'as_of': '2025-11-07T21:30:00+08:00', 'history': [], 'messages': [{'role': 'user', 'content': '谢谢老师', 'at': '2025-11-07T21:30:00+08:00'}],
             'facts': [], 'events': [], 'reference': 'FUTURE_ANSWER_ONLY', 'renewal_category': 'SECRET_LABEL', 'source_refs': [{'path': 'PRIVATE_UID.xlsx'}]}
        p.update(changes)
        return self.lab.scenario_save(self.actor, {'name': 'case-'+str(len(self.lab.versions(self.actor, 'scenario'))), 'note': 'test', 'payload': p})

    def test_native_part_projection_keeps_sent_text_for_continuation_only(self):
        from reply_lab_native import delivery_parts
        c,scene=self.config(),self.scenario()
        first=self.run_fixture(config=c,scenario=scene)
        parts=delivery_parts({'parts':[
            {'id':'p1','status':'sent','message_type':7,'body':'真正送达的正文'},
            {'id':'p2','status':'sent','message_type':6,'body':'https://experiment.invalid/asset.png'},
            {'id':'p3','status':'unknown','message_type':7,'body':'未确认正文'}]})
        result=first['result'];result['parts']=parts
        with self.lab.connect() as db:
            result['content_digest']=digest({k:v for k,v in result.items() if k!='content_digest'})
            raw=json.dumps(result,ensure_ascii=False)
            db.execute('UPDATE lab_runs SET result=? WHERE id=?',(raw,first['id']))
        history=self.lab.delivered_history(self.actor,self.lab.run(self.actor,first['id']))
        self.assertEqual([x['content'] for x in history if x['role']=='assistant'],['真正送达的正文'])
        self.assertEqual(parts[1]['message_type'],6)
        self.assertEqual(parts[1]['body'],'https://experiment.invalid/asset.png')

    def test_default_is_explicit_owned_immutable_config_not_latest(self):
        chosen = self.config(preference_memory={'enabled': True, 'slow_enabled': True})
        self.config()
        folder = self.studio.root / 'config'
        folder.mkdir()
        (folder / 'reply-lab-default.json').write_text(json.dumps({'config_version': chosen['id']}))
        self.assertEqual(self.lab.default_config(self.actor), chosen['id'])
        self.assertEqual(chosen['payload']['preference_memory'], {'enabled': True, 'slow_enabled': True})
        self.assertEqual(chosen['payload']['component_digests']['preference_memory'], digest(chosen['payload']['preference_memory']))
        self.assertIsNone(self.lab.default_config(self.other))
        with self.assertRaises(Fault): self.config(preference_memory={'enabled': 'true'})

    def config(self, **changes):
        p = {'persona': 'Respond to the current question using real facts.', 'primary_skill': 'tutor-main', 'model': 'test-model', 'parameters': {}}
        p.update(changes)
        return self.lab.config_save(self.actor, {'name': 'config-'+str(len(self.lab.versions(self.actor, 'config'))), 'note': 'test', 'payload': p})

    def run_fixture(self, config=None, scenario=None, **kwargs):
        c, s = config or self.config(), scenario or self.scenario()
        job = self.lab.start(self.actor, {'config': c['id'], 'scenario': s['id'], 'engine': 'fixture', **kwargs})
        self.lab.pool.submit(lambda: None).result(timeout=10)
        return self.lab.run(self.actor, job['id'])

    def native_result(self, e):
        return {'output': '测试输出', 'simulation': True, 'session_id': 'native-session', 'request_id': 'native-request',
                'harness_version': '0.2.1-rc.2', 'trace': [], 'parts': [],
                'assembly': {**{k: e[k] for k in ['config_digest', 'code_commit', 'runtime_root']},
                             'component_digests': e['config']['component_digests'],
                             'model': e['config']['model'], 'parameters': e['config']['parameters'],
                             'writable_paths': {k: str(Path(e['runtime_root']) / k) for k in ['business_db', 'teacher_db', 'config', 'skills', 'sessions', 'queue', 'locks', 'logs', 'clock', 'cache', 'assets']},
                             'loaded_skills': {k: digest(v) for k, v in e['config']['skills'].items()}}}

    def test_undelivered_notice_is_not_replayed_as_assistant(self):
        c,s=self.config(),self.scenario()
        class Runner:
            def run(_,e):return {**self.native_result(e),'output':'内部待人工提示，未发送。','parts':[]}
        self.lab.runner=Runner()
        first=self.lab.start(self.actor,{'config':c['id'],'scenario':s['id'],'engine':'native'})
        self.lab.pool.submit(lambda:None).result(10)
        old=self.lab.run(self.actor,first['id'])['result']
        old['transcript'].append({'role':'assistant','content':'旧版本未送出的内部提示'})
        old['content_digest']=digest({k:v for k,v in old.items() if k!='content_digest'})
        with self.lab.connect() as db:db.execute('UPDATE lab_runs SET result=? WHERE id=?',(json.dumps(old),first['id']))
        second=self.lab.start(self.actor,{'config':c['id'],'scenario':s['id'],'engine':'native','parent_run':first['id'],'messages':['后续消息']})
        self.lab.pool.submit(lambda:None).result(10)
        self.assertFalse(any(x['role']=='assistant' for x in self.lab.run(self.actor,second['id'])['envelope']['history']))

    def test_cross_account_versions_runs_and_restore_denied_even_same_name_admin(self):
        c = self.config(); r = self.run_fixture(config=c)
        for operation in [lambda: self.lab.version(self.other, c['id']), lambda: self.lab.run(self.other, r['id']),
                          lambda: self.lab.dispatch(self.other, 'restore', {'id': c['id']})]:
            with self.assertRaises(Fault) as error: operation()
            self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.lab.versions(self.other, 'config'), [])

    def test_future_fact_message_and_missing_timezone_rejected(self):
        for changes in [{'facts': [{'source': '2026-learning', 'known_at': '2026-01-01T00:00:00+08:00'}]},
                        {'messages': [{'role': 'user', 'content': 'future', 'at': '2026-01-01T00:00:00+08:00'}]},
                        {'as_of': '2025-11-07T21:30:00'}]:
            with self.subTest(changes=changes), self.assertRaises(Fault): self.scenario(**changes)

    def test_family_variants_cannot_cross_splits(self):
        self.scenario(split='development')
        with self.assertRaises(Fault): self.scenario(split='blind')

    def test_source_hash_cannot_cross_splits_by_renaming_family(self):
        source = {'file_sha256': 'a'*64, 'sheet': 'chat', 'visible_rows': [2]}
        self.scenario(source_refs=[source])
        with self.assertRaises(Fault): self.scenario(source_refs=[source], family_group='new-name', split='blind')

    def test_candidate_examples_and_blind_examples_rejected(self):
        for s in [self.scenario(split='examples'), self.scenario(family_group='f2', split='blind', review='approved', reviewer='human')]:
            with self.assertRaises(Fault): self.config(examples=[s['id']])

    def test_example_family_cannot_be_its_own_eval_case(self):
        s = self.scenario(split='examples', review='approved', reviewer='human')
        c = self.config(examples=[s['id']])
        with self.assertRaises(Fault): self.run_fixture(config=c, scenario=s)

    def test_config_is_frozen_after_live_skill_edit(self):
        c = self.config()
        (self.studio.skills / 'reply-style/SKILL.md').write_text('changed', encoding='utf8')
        r = self.run_fixture(config=c)
        self.assertNotIn('changed', json.dumps(r['envelope']['config']))
        self.assertIn('reply-style', c['payload']['skills'])

    def test_optimistic_conflict_restore_and_original_retained(self):
        c = self.config()
        with self.assertRaises(Fault): self.lab.save(self.actor, 'config', c['name'], c['payload'], note='conflicting')
        restored = self.lab.dispatch(self.actor, 'restore', {'id': c['id'], 'expected': c['id']})
        self.assertNotEqual(restored['id'], c['id'])
        self.assertEqual(restored['parent'], c['id'])
        self.assertEqual(restored['digest'], c['digest'])
        self.assertEqual(self.lab.version(self.actor, c['id'])['digest'], c['digest'])

    def test_native_missing_adapter_has_no_side_effects(self):
        c, s = self.config(), self.scenario()
        with self.assertRaises(Fault) as error:
            self.lab.start(self.actor, {'config': c['id'], 'scenario': s['id']})
        self.assertEqual(error.exception.status, 503)
        self.assertFalse((self.lab.home / 'runs').exists())

    def test_fixture_is_not_native_or_quality_and_excludes_reference(self):
        r = self.run_fixture()
        self.assertEqual(r['status'], 'done')
        self.assertFalse(r['result']['native_verified'])
        self.assertFalse(r['result']['quality_accepted'])
        self.assertIsNone(r['result']['assembly'])
        for secret in ['FUTURE_ANSWER_ONLY', 'SECRET_LABEL', 'PRIVATE_UID']:
            self.assertNotIn(secret, json.dumps(r['envelope']))
        self.assertFalse((self.studio.root / 'runtime/live.sqlite').exists())

    def test_history_rebuild_separate_roots_and_config_boundaries(self):
        s = self.scenario(); a = self.run_fixture(scenario=s)
        b = self.run_fixture(scenario=s, parent_run=a['id'], messages=['还有一个问题'])
        self.assertNotEqual(a['envelope']['runtime_root'], b['envelope']['runtime_root'])
        self.assertEqual(b['envelope']['branch_mode'], 'history_rebuild')
        self.assertFalse(b['envelope']['replay_side_effects'])
        self.assertEqual(b['result']['transcript'][0], a['result']['transcript'][0])
        self.assertNotEqual(b['result']['transcript'][-1]['config_version'], a['result']['transcript'][-1]['config_version'])

    def test_comparison_rejects_different_scenario_or_model(self):
        a = self.run_fixture(); b = self.run_fixture()
        # Same scenario content is comparable despite differing immutable IDs.
        pair = self.lab.compare(self.actor, {'left': a['id'], 'right': b['id']})
        self.assertFalse(pair['quality_comparison'])
        with self.assertRaises(Fault): self.lab.compare(self.actor, {'left': a['id'], 'right': b['id'], 'blind': True})
        c = self.run_fixture(scenario=self.scenario(family_group='different'))
        with self.assertRaises(Fault): self.lab.compare(self.actor, {'left': a['id'], 'right': c['id']})

    def test_native_evidence_mismatch_fails_closed(self):
        r = self.run_fixture(); e = r['envelope']
        for key in ['config_digest', 'code_commit', 'runtime_root', 'component_digests', 'loaded_skills']:
            out = self.native_result(e); out['assembly'][key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(Fault): self.lab.verify_native(e, out)
        out = self.native_result(e); out['parts'] = [{'status': 'sent', 'simulation': False}]
        with self.assertRaises(Fault): self.lab.verify_native(e, out)

    def test_tampered_versions_and_runs_are_detected(self):
        c = self.config(); r = self.run_fixture(config=c)
        with self.lab.connect() as db:
            db.execute('UPDATE lab_versions SET payload=? WHERE id=?', ('{}', c['id']))
            db.execute('UPDATE lab_runs SET envelope=? WHERE id=?', ('{}', r['id']))
        with self.assertRaises(Fault): self.lab.version(self.actor, c['id'])
        with self.assertRaises(Fault): self.lab.run(self.actor, r['id'])

    def test_serial_worker_rejects_another_run_during_generation(self):
        c, s = self.config(), self.scenario()
        entered, release = threading.Event(), threading.Event()
        class Runner:
            def run(_, e):
                entered.set(); release.wait(5)
                return self.native_result(e)
        self.lab.runner = Runner()
        job = self.lab.start(self.actor, {'config': c['id'], 'scenario': s['id'], 'engine': 'native'})
        self.assertTrue(entered.wait(3))
        try:
            with self.assertRaises(Fault) as error: self.lab.start(self.actor, {'config': c['id'], 'scenario': s['id'], 'engine': 'fixture'})
            self.assertEqual(error.exception.status, 429)
            self.config(persona='A different candidate during the running request')
        finally: release.set()
        self.lab.pool.submit(lambda: None).result(10)
        r = self.lab.run(self.actor, job['id'])
        self.assertEqual(r['envelope']['config_digest'], c['digest'])
        self.assertEqual(r['status'], 'done')

    def test_blind_material_hides_run_and_version_mapping(self):
        c, s = self.config(), self.scenario()
        class Runner:
            def run(_, e): return self.native_result(e)
        self.lab.runner = Runner()
        ids = []
        for _ in range(2):
            job = self.lab.start(self.actor, {'config': c['id'], 'scenario': s['id'], 'engine': 'native'})
            self.lab.pool.submit(lambda: None).result(10); ids.append(job['id'])
        review = self.lab.compare(self.actor, {'left': ids[0], 'right': ids[1], 'blind': True})
        for secret in [*ids, c['id'], 'FUTURE_ANSWER_ONLY']:
            self.assertNotIn(secret, json.dumps(review))
        from reply_lab import RUBRIC
        vote = {'id': review['id'], 'choice': 'both_bad', 'reason': '测试原句依据',
                'scores': {side: {d: 2 for d in RUBRIC} for side in ['left', 'right']}, 'hard_failures': ['测试硬失败']}
        with self.assertRaises(Fault): self.lab.dispatch(self.other, 'vote', vote)
        self.lab.dispatch(self.actor, 'vote', vote)
        with self.assertRaises(Fault): self.lab.dispatch(self.actor, 'vote', vote)
        report = self.lab.dispatch(self.actor, 'review-report', {})
        self.assertEqual(report['reviewed'], 1)
        self.assertEqual(report['both_bad'], 1)
        self.assertEqual(report['hard_failure_reviews'], 1)
        self.assertFalse(report['quality_accepted'])

    def test_restart_preserves_interrupted_run_without_replay(self):
        r = self.run_fixture()
        with self.lab.connect() as db: db.execute("UPDATE lab_runs SET status='running' WHERE id=?", (r['id'],))
        self.lab.close()
        restarted = ReplyLab(self.studio)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.run(self.actor, r['id'])['status'], 'interrupted')

    def test_asset_review_preview_hash_and_disable_all_versions(self):
        from PIL import Image
        image = io.BytesIO(); Image.new('RGB', (8, 8), 'blue').save(image, format='PNG')
        p = {'rights': 'owned synthetic test', 'caption': 'blue test', 'visible_text': '', 'intents': ['ack'], 'suitable': ['test'], 'avoid': ['complaint'], 'review': 'candidate'}
        a = self.lab.asset_save(self.actor, {'name': 'test', 'note': 'import', 'payload': p, 'file_base64': base64.b64encode(image.getvalue()).decode()})
        with self.assertRaises(Fault): self.config(assets=[a['id']])
        b = self.lab.asset_save(self.actor, {'name': 'test', 'parent': a['id'], 'expected': a['id'], 'note': 'review', 'payload': {**p, 'review': 'approved', 'reviewer': 'test-human'}})
        c = self.config(assets=[b['id']])
        self.assertEqual(self.lab.dispatch(self.actor, 'asset-preview', {'id': b['id']})['mime'], 'image/png')
        replacement = io.BytesIO(); Image.new('RGB', (8, 8), 'red').save(replacement, format='PNG')
        new = self.lab.asset_save(self.actor, {'name': 'test', 'parent': b['id'], 'expected': b['id'], 'note': 'replace image',
                                              'payload': {**p, 'review': 'approved', 'reviewer': 'old-reviewer'},
                                              'file_base64': base64.b64encode(replacement.getvalue()).decode()})
        self.assertEqual(new['parent'], b['id'])
        self.assertNotEqual(new['payload']['sha256'], b['payload']['sha256'])
        self.assertEqual(new['payload']['review'], 'candidate')
        self.lab.dispatch(self.actor, 'asset-disable', {'id': a['id']})
        with self.assertRaises(Fault): self.run_fixture(config=c)
        self.assertEqual(self.lab.version(self.actor, b['id'])['payload']['review'], 'approved')

    def test_teacher_route_initializes_once_and_rejects_publish(self):
        out = self.studio.dispatch(self.actor, 'reply-lab/status')
        self.assertFalse(out['can_publish'])
        self.assertFalse(out['native_available'])
        with self.assertRaises(Fault): self.studio.dispatch(self.actor, 'reply-lab/publish')

    def test_second_worker_cannot_interrupt_live_owner(self):
        with self.assertRaises(Fault) as error: ReplyLab(self.studio)
        self.assertEqual(error.exception.status, 409)

    def test_batch_keeps_separate_runs_and_explicit_rejections(self):
        c, s = self.config(), self.scenario()
        jobs = self.lab.batch(self.actor, {'configs': [c['id']], 'scenarios': [s['id']], 'engine': 'fixture'})
        self.lab.pool.submit(lambda: None).result(10)
        self.assertEqual(len(jobs['jobs']), 1)
        self.assertEqual(jobs['rejected'], [])
        blocked = self.lab.batch(self.actor, {'configs': [c['id']], 'scenarios': [s['id']], 'engine': 'native'})
        self.assertEqual(blocked['jobs'], [])
        self.assertIn('native_adapter_unavailable', blocked['rejected'][0]['error'])

    def test_actual_runtime_paths_and_model_must_match(self):
        e = self.run_fixture()['envelope']
        for changed in ['paths', 'model']:
            result = self.native_result(e)
            if changed == 'paths': result['assembly']['writable_paths']['business_db'] = '/production/live.sqlite'
            else: result['assembly']['model'] = 'other-model'
            with self.assertRaises(Fault): self.lab.verify_native(e, result)

    def test_http_auth_required_and_new_page_available(self):
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from teacher_workbench import handler
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.studio))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        url = 'http://127.0.0.1:'+str(server.server_address[1])
        with urlopen(url+'/skill-studio/reply-lab/') as response:
            self.assertIn('回复工作台', response.read().decode())
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(url+'/skill-studio/api/reply-lab/status', data=b'{}', headers={'Content-Type':'application/json','X-Skill-Studio':'1'}))
        self.assertEqual(error.exception.code, 401)


class ImportManifestTests(unittest.TestCase):
    def test_hash_verification_fails_instead_of_old_batch_fallback(self):
        from reply_lab_import import verify, sha
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root / 'batch'; folder.mkdir()
            raw = folder / 'input.xlsx'; raw.write_bytes(b'private-test-content')
            manifest = folder / 'manifest.json'
            manifest.write_text(json.dumps({'files':[{'path':'input.xlsx','sha256':sha(raw),'bytes':raw.stat().st_size}]}))
            pointer = root / 'TEST_DATA_CURRENT.json'
            pointer.write_text(json.dumps({'manifest':'batch/manifest.json','manifest_sha256':sha(manifest)}))
            self.assertEqual(verify(pointer)[0], folder)
            raw.write_bytes(b'tampered-input')
            with self.assertRaises(ValueError): verify(pointer)
            raw.write_bytes(b'private-test-content'); manifest.write_text('{}')
            with self.assertRaises(ValueError): verify(pointer)


if __name__ == '__main__': unittest.main()

