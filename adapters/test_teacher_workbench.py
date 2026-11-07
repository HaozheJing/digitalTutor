import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import teacher_workbench as tw
import skill_studio as legacy


class TeacherWorkbenchTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/'runtime/teacher-workbench-tests';base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.actor={'id':'teacher','name':'老师甲','admin':False}
        for name in ['celebrate-lesson-completion','reply-style','tutor-main','service-policy','student-service-tier','custom-runtime-rule']:
            self.write_skill(name)
        self.studio=tw.TeacherStudio(self.root);self.addCleanup(self.studio.pool.shutdown)

    def write_skill(self,name,suffix=''):
        p=self.root/'skills'/name;p.mkdir(parents=True,exist_ok=True)
        text='---\nname: '+name+'\ndescription: Teacher fixture\n---\n\n# '+name+'\nUse current facts, keep uncertainty visible, and follow the selected action.\n'+suffix
        (p/'SKILL.md').write_text(text,encoding='utf8')
        return text

    def save(self,name='celebrate-lesson-completion',suffix=''):
        files=self.studio.source(name);files['SKILL.md']+=suffix
        return self.studio.save(self.actor,{'skill':name,'files':files,'note':'测试版本'})

    def dataset(self):return {'summary':'测试摘要不得泄露给被测模型 SUMMARY_ONLY','as_of':'2025-10-14T18:00:00+08:00','message':'老师，这节课读完了。','expected':'EXPECTED_ONLY','tool_results':{}}

    def test_catalog_includes_shared_and_installed_support_skills(self):
        result=self.studio.dispatch(self.actor,'catalog')
        self.assertEqual({r['skill'] for r in result['items']},set(self.studio.installed()))
        self.assertIn('reply-style',{r['skill'] for r in result['items']})
        self.assertIn('custom-runtime-rule',{r['skill'] for r in result['items']})
        self.assertFalse(result['can_publish']);self.assertEqual(result['model'],tw.MODEL)

    def test_shared_rule_draft_and_restore_never_change_production(self):
        before=(self.root/'skills/reply-style/SKILL.md').read_bytes()
        a=self.save('reply-style','\nOriginal draft.');b=self.save('reply-style','\nSecond draft.')
        restored=self.studio.restore(self.actor,{'version':a['id']})
        self.assertNotIn(restored['id'],[a['id'],b['id']]);self.assertEqual(restored['parent'],a['id'])
        self.assertEqual(restored['files'],a['files']);self.assertEqual((self.root/'skills/reply-style/SKILL.md').read_bytes(),before)
        self.assertFalse((self.root/'runtime/skill-studio/active.json').exists())

    def test_published_action_uses_active_bundle_and_shared_ignores_stale_pointer(self):
        home=self.root/'runtime/skill-studio';home.mkdir(parents=True)
        ident='a'*32;p=home/'bundles'/ident/'celebrate-lesson-completion';p.mkdir(parents=True)
        (p/'SKILL.md').write_text('active action body',encoding='utf8')
        (home/'active.json').write_text(json.dumps({'celebrate-lesson-completion':{'version':ident},'reply-style':{'version':'b'*32}}))
        self.assertEqual(self.studio.source('celebrate-lesson-completion')['SKILL.md'],'active action body')
        self.assertIn('name: reply-style',self.studio.source('reply-style')['SKILL.md'])

    def test_broken_active_pointer_does_not_silently_use_old_source(self):
        p=self.root/'runtime/skill-studio';p.mkdir(parents=True)
        (p/'active.json').write_text(json.dumps({'celebrate-lesson-completion':{'version':'../secret'}}))
        with self.assertRaises(legacy.Fault):self.studio.source('celebrate-lesson-completion')

    def test_all_public_publish_spellings_deny_owner_and_teacher_without_network(self):
        with patch.object(tw,'urlopen',side_effect=AssertionError('must not reach network')):
            for actor in [self.actor,{**self.actor,'admin':True}]:
                for path in ['publish','apply','rollback','skills-publish']:
                    with self.subTest(actor=actor['admin'],path=path),self.assertRaises(legacy.Fault) as raised:self.studio.dispatch(actor,path,{'version':'x'})
                    self.assertEqual(raised.exception.status,403)
            with self.assertRaises(legacy.Fault):self.studio.apply({})

    def test_source_and_save_reject_uninstalled_and_path_escape(self):
        for name in ['../config/provider','not-installed','reply-style/../../secret']:
            with self.assertRaises(legacy.Fault):self.studio.source(name)
        v=self.save()
        with self.assertRaises(legacy.Fault):self.studio.save(self.actor,{'skill':v['skill'],'files':{**v['files'],'references/../../key.md':'bad'},'note':'bad'})

    def test_context_can_combine_business_action_with_edited_shared_rule(self):
        edited=self.save('reply-style','\nTEACHER_STYLE_MARKER')
        a=self.studio.assemble(self.actor,{'version':edited['id'],'primary_skill':'celebrate-lesson-completion'})
        self.assertIn('TEACHER_STYLE_MARKER',a['text']);self.assertIn('celebrate-lesson-completion',a['bundle'])
        self.assertEqual(next(m for m in a['manifest'] if m['skill']=='reply-style')['version'],edited['id'])
        self.assertEqual(self.studio.assembly(a['id'])['digest'],a['digest'])

    def test_assembly_is_frozen_even_when_source_and_other_drafts_change(self):
        v=self.save();a=self.studio.assemble(self.actor,{'version':v['id']})
        self.write_skill('reply-style','LIVE_CHANGED_AFTER_ASSEMBLY')
        self.save('reply-style','OTHER_NEW_DRAFT')
        persisted=self.studio.assembly(a['id']);self.assertEqual(persisted['text'],a['text'])
        effective={**v,'_assembly':persisted}
        reply=self.studio.mock_tool('service_skill',{'name':'reply-style'},self.dataset(),effective)
        self.assertNotIn('LIVE_CHANGED_AFTER_ASSEMBLY',reply['body'])
        self.assertEqual(reply['context_digest'],a['digest'])

    def test_inactive_reference_is_visible_in_source_but_not_added_to_model_context(self):
        directory=self.root/'skills/service-policy/references';directory.mkdir()
        (directory/'supervision.md').write_text('INACTIVE_MARKER')
        v=self.save('service-policy');a=self.studio.assemble(self.actor,{'version':v['id']})
        self.assertIn('references/supervision.md',v['files']);self.assertNotIn('INACTIVE_MARKER',a['text'])
        self.assertEqual(a['manifest'][0]['omitted_inactive_files'],['references/supervision.md'])

    def test_wrong_skill_override_and_tampered_snapshot_fail_closed(self):
        v=self.save();other=self.save('reply-style')
        with self.assertRaises(legacy.Fault):self.studio.assemble(self.actor,{'version':v['id'],'overrides':{'tutor-main':other['id']}})
        a=self.studio.assemble(self.actor,{'version':v['id']})
        with self.studio.connect() as c:c.execute('UPDATE assemblies SET payload=? WHERE id=?',('{}',a['id']))
        with self.assertRaises(legacy.Fault):self.studio.assembly(a['id'])

    def test_snapshot_reference_cannot_read_unassembled_or_arbitrary_files(self):
        v=self.save();a=self.studio.assemble(self.actor,{'version':v['id']});effective={**v,'_assembly':a}
        for name,path in [('reply-style','../../config/provider.json'),('tutor-main','SKILL.md')]:
            out=self.studio.mock_tool('service_reference',{'name':name,'path':path},self.dataset(),effective)
            self.assertIn(out['status'],['blocked','unknown'])

    def test_simulation_hides_expectation_and_does_not_use_production_tools(self):
        v=self.save();seen=[]
        def model(messages,tools=None):
            seen.append(json.dumps(messages,ensure_ascii=False))
            return {'content':json.dumps({'decision':'受阻','reason':'缺少候选','reply':'','evidence':[],'gaps':['候选缺失']},ensure_ascii=False)}
        self.studio.model=model
        job=self.studio.start_job(self.actor,{'version':v['id'],'dataset':self.dataset()},'simulate')
        self.studio.pool.shutdown(wait=True)
        result=self.studio.job(job['id']);self.assertEqual(result['status'],'done')
        self.assertNotIn('EXPECTED_ONLY',seen[0]);self.assertNotIn('SUMMARY_ONLY',seen[0])
        self.assertEqual(result['result']['context_digest'],job['context_digest']);self.assertFalse(result['result']['can_publish'])
        self.assertFalse((self.root/'runtime/live.sqlite').exists())

    def test_changed_version_cannot_reuse_a_different_assembly(self):
        a=self.save();b=self.save(suffix='\nDifferent version.')
        snapshot=self.studio.assemble(self.actor,{'version':a['id']})
        with self.assertRaises(legacy.Fault):self.studio.start_job(self.actor,{'version':b['id'],'assembly_id':snapshot['id'],'dataset':self.dataset()},'simulate')

    def test_generation_and_history_keep_context_and_data(self):
        v=self.save();self.studio.model=lambda *args:{'content':json.dumps(self.dataset())}
        job=self.studio.start_job(self.actor,{'version':v['id'],'prompt':'一个简单的完课场景'},'generate')
        self.studio.pool.shutdown(wait=True)
        result=self.studio.job(job['id']);self.assertEqual(result['result']['dataset'],self.dataset())
        self.assertEqual(result['result']['model'],tw.MODEL);self.assertIn('context_manifest',result['result'])

    def test_fixed_model_and_usage_are_independent_of_main_runtime_model(self):
        cfg=self.root/'config';cfg.mkdir();(cfg/'provider.json').write_text(json.dumps({'large_llm_name':'doubao-seed-2.1-pro','api_key':'test-not-real','base_url':'https://model.invalid/v1'}))
        captured=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'model':tw.MODEL,'choices':[{'message':{'content':'{}'}}],'usage':{'prompt_tokens':1000,'completion_tokens':100,'prompt_tokens_details':{'cached_tokens':500}}}).encode()
        def call(request,**kwargs):captured.append(json.loads(request.data));return Response()
        self.studio.local.calls=[]
        with patch.object(tw,'urlopen',call):self.studio.call_model([{'role':'user','content':'test'}])
        self.assertEqual(captured[0]['model'],tw.MODEL)
        summary=self.studio.usage_summary(self.studio.local.calls)
        self.assertTrue(summary['usage_available']);self.assertEqual(summary['cached_input_tokens'],500)
        self.assertAlmostEqual(summary['estimated_cny'],.001456)
        self.assertEqual(json.loads((cfg/'provider.json').read_text())['large_llm_name'],'doubao-seed-2.1-pro')

    def test_missing_usage_is_not_reported_as_zero_cost(self):
        out=self.studio.usage_summary([{'usage':None}]);self.assertFalse(out['usage_available']);self.assertIsNone(out['estimated_cny'])
        out=self.studio.usage_summary([{'usage':{'prompt_tokens':10,'completion_tokens':3,'prompt_tokens_details':None}}])
        self.assertTrue(out['usage_available']);self.assertIsNone(out['cached_input_tokens']);self.assertGreater(out['estimated_cny'],0)

    def test_generator_metadata_is_normalized_without_allowing_unknown_tools(self):
        data=self.dataset();data['tool_results']['simulation']=True
        value=self.studio.validate_dataset(data)
        self.assertTrue(value['simulation']);self.assertNotIn('simulation',value['tool_results'])
        self.assertIn('simulation',data['tool_results'])
        for bad in [{'shell_exec':{}},{'simulation':{}},{'simulation':True,'arbitrary_network':{}}]:
            with self.assertRaises(legacy.Fault):self.studio.validate_dataset({**self.dataset(),'tool_results':bad})

    def test_teacher_auth_revocation_and_admin_scope(self):
        user=self.studio.add_user('新老师');actor=self.studio.authenticate('Bearer '+user['token'])
        self.assertFalse(actor['admin'])
        with self.assertRaises(legacy.Fault):self.studio.dispatch(actor,'invite',{'name':'other'})
        with self.assertRaises(legacy.Fault):self.studio.dispatch(actor,'publish',{})
        self.studio.dispatch({**self.actor,'admin':True},'revoke',{'id':user['id']})
        with self.assertRaises(legacy.Fault):self.studio.authenticate('Bearer '+user['token'])

    def test_legacy_credentials_and_versions_are_copied_without_mutating_legacy_store(self):
        old=legacy.Studio(self.root);self.addCleanup(old.pool.shutdown)
        user=old.add_user('原老师');v=old.save(self.actor,{'skill':'celebrate-lesson-completion','files':old.source('celebrate-lesson-completion'),'note':'原草稿'})
        before=hashlib.sha256(old.db.read_bytes()).hexdigest()
        other_root=self.root/'copy';other_root.mkdir()
        import shutil
        shutil.copytree(self.root/'skills',other_root/'skills');shutil.copytree(self.root/'runtime/skill-studio',other_root/'runtime/skill-studio')
        copied=tw.TeacherStudio(other_root);self.addCleanup(copied.pool.shutdown)
        self.assertEqual(copied.authenticate('Bearer '+user['token'])['name'],'原老师')
        self.assertEqual(copied.version(v['id'])['files'],v['files'])
        self.assertEqual(hashlib.sha256(old.db.read_bytes()).hexdigest(),before)
        self.assertNotEqual(copied.db,other_root/'runtime/skill-studio/studio.sqlite')


if __name__=='__main__':unittest.main()
