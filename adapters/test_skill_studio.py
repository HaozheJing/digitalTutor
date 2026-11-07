import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import skill_studio as st
import service_content as content


class StudioTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/'runtime/studio-tests';base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.name='highlight-course-start'
        directory=self.root/'skills'/self.name; (directory/'references').mkdir(parents=True)
        self.body='---\nname: '+self.name+'\ndescription: First lesson celebration\n---\n\n# Action\nRead the evidence and check eligibility before delivery.\n[Cases](references/examples.md)\n'
        (directory/'SKILL.md').write_text(self.body,encoding='utf-8'); (directory/'references/examples.md').write_text('Example A',encoding='utf-8')
        self.studio=st.Studio(self.root);self.addCleanup(self.studio.pool.shutdown)
        self.actor={'id':'teacher','name':'Teacher A','admin':False}
        self.files=self.studio.source(self.name)
        self.data={'summary':'首课已完成','as_of':'2025-10-13T19:00:00+08:00','tool_results':{'check_proactive':{'default':{'status':'eligible'},'cases':[{'match':{'candidate_id':'old'},'result':{'status':'already_delivered'}}]}}}

    def save(self, extra=''):
        return self.studio.save(self.actor,{'skill':self.name,'files':{**self.files,'SKILL.md':self.body+extra},'note':'new version'})

    def test_versions_are_immutable_and_editing_does_not_change_live(self):
        a=self.save();b=self.save('More rules.');self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(self.studio.version(a['id'])['files'],self.files)
        self.assertEqual(self.studio.source(self.name),self.files)

    def test_weekly_interaction_is_one_editable_item_and_old_names_cannot_publish(self):
        import service_catalog as catalog
        items=[i for i in st.ITEMS if i['skill']==catalog.WEEKLY_INTERACTION_SKILL]
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['action_ids'],list(catalog.WEEKLY_INTERACTION_IDS))
        self.assertEqual(items[0]['title'],'周互动')
        for old in catalog.SKILL_ALIASES:
            self.assertNotIn(old,st.ALLOWED)
            with self.assertRaises(st.Fault):self.studio.save(self.actor,{'skill':old,'files':self.files,'note':'old action'})
        base=content.skill_file(catalog.WEEKLY_INTERACTION_SKILL).parent
        files={p.relative_to(base).as_posix():p.read_text(encoding='utf8') for p in base.rglob('*.md')}
        merged=self.studio.save(self.actor,{'skill':catalog.WEEKLY_INTERACTION_SKILL,'files':files,'note':'merged'})
        for old in catalog.SKILL_ALIASES:
            result=self.studio.mock_tool('service_skill',{'name':old},self.data,merged)
            self.assertEqual(result['body'],files['SKILL.md'])

    def test_excludes_shared_rules_and_arbitrary_paths(self):
        for name in ['service-policy','tutor-main','../../config/provider.json']:
            with self.assertRaises(st.Fault):self.studio.validate(name,self.files)
        for path in ['../secret.md','references/../../secret.md','/tmp/secret.md','references/tool.py']:
            with self.assertRaises(st.Fault): self.studio.validate(self.name,{**self.files,path:'bad'})
        with self.assertRaises(st.Fault): self.studio.validate(self.name,{'SKILL.md':self.body})

    def test_publish_conflict_and_rollback_restore_exact_bundle(self):
        old=st.digest(self.files); v=self.save('A new rule.');p=self.studio.apply({'version':v['id'],'expected':old,'author':'Teacher A'})
        self.assertEqual(self.studio.source(self.name),v['files'])
        with self.assertRaises(st.Fault) as e:self.studio.apply({'version':v['id'],'expected':old})
        self.assertEqual(e.exception.status,409)
        self.studio.apply({'version':p['previous'],'expected':v['digest'],'author':'Teacher B'})
        self.assertEqual(self.studio.source(self.name),self.files)
        with self.studio.connect() as c:self.assertEqual(c.execute("SELECT count(*) FROM publications WHERE status='applied'").fetchone()[0],2)

    def test_native_reader_loads_published_body_and_references(self):
        v=self.save('Published rule.'); self.studio.apply({'version':v['id'],'expected':st.digest(self.files)})
        with patch.object(content.s,'R',self.root):
            self.assertEqual(content.skill_file(self.name).read_text(encoding='utf-8'),v['files']['SKILL.md'])
            self.assertEqual(content.reference(self.name,'references/examples.md')['body'],'Example A')

    def test_parent_must_match_skill(self):
        v=self.save()
        with self.assertRaises(st.Fault):self.studio.save(self.actor,{'skill':'remind-reading','parent':v['id'],'files':{'SKILL.md':self.body.replace(self.name,'remind-reading')},'note':'x'})

    def test_token_auth_revocation_and_admin_only_invites(self):
        user=self.studio.add_user('Teacher')
        self.assertEqual(self.studio.authenticate('Bearer '+user['token'])['name'],'Teacher')
        with self.assertRaises(st.Fault):self.studio.authenticate('Bearer wrong')
        with self.assertRaises(st.Fault):self.studio.dispatch(self.actor,'invite',{'name':'Other'})
        with self.studio.connect() as c:
            self.assertNotIn(user['token'],c.execute('SELECT token_hash FROM users').fetchone()[0])
            c.execute('UPDATE users SET enabled=0')
        with self.assertRaises(st.Fault):self.studio.authenticate('Bearer '+user['token'])

    def test_owner_can_reuse_workbench_key_without_export(self):
        directory=self.root/'config';directory.mkdir()
        (directory/'workbench.json').write_text(json.dumps({'token':'existing-owner-key'}))
        self.assertTrue(self.studio.authenticate('Bearer existing-owner-key')['admin'])
        with self.assertRaises(st.Fault):self.studio.authenticate('Bearer similar-key')

    def test_dataset_requires_time_and_limits_tools(self):
        self.studio.validate_dataset(self.data)
        for d in [{**self.data,'as_of':'2025-10-13'}, {**self.data,'tool_results':{'shell_exec':{}}}, {'summary':'missing'}]:
            with self.assertRaises(st.Fault):self.studio.validate_dataset(d)

    def test_s10_context_and_tool_reads_exclude_policy_bundle(self):
        for name in ['service-policy','tutor-main','proactive-service','plan-today']:
            directory=self.root/'skills'/name;(directory/'references').mkdir(parents=True)
            (directory/'SKILL.md').write_text('excluded-policy-marker',encoding='utf-8')
            (directory/'references/action-templates.md').write_text('excluded-template-marker',encoding='utf-8')
        version={'skill':'weekly-learning-review','files':{'SKILL.md':'S10 body with reply-style'}}
        context=self.studio.context(version)
        self.assertNotIn('excluded-policy-marker',context)
        self.assertNotIn('excluded-template-marker',context)
        for tool,args in [('service_skill',{'name':'service-policy'}),('service_reference',{'name':'service-policy','path':'references/action-templates.md'})]:
            self.assertEqual(self.studio.mock_tool(tool,args,self.data,version)['status'],'not_applicable')

    def test_mock_reads_match_arguments_missing_fails_closed(self):
        v=self.save()
        self.assertEqual(self.studio.mock_tool('check_proactive',{'candidate_id':'old'},self.data,v)['status'],'already_delivered')
        self.assertEqual(self.studio.mock_tool('check_proactive',{'candidate_id':'new'},self.data,v)['status'],'eligible')
        self.assertEqual(self.studio.mock_tool('knowledge_read',{},self.data,v)['status'],'unknown')
        self.assertEqual(self.studio.mock_tool('shell_exec',{},self.data,v)['status'],'blocked')
        self.assertEqual(self.studio.mock_tool('deliver_proactive',{},self.data,v)['status'],'mock_sent')

    def test_family_profile_is_exposed_and_reads_only_simulated_identity(self):
        v=self.save()
        data={**self.data,'tool_results':{'family_profile':{'default':{'child_nickname':'侨侨','guardian_role':'mother'}}}}
        self.studio.validate_dataset(data)
        self.assertIn('family_profile',st.READ_TOOLS)
        self.assertEqual(st.tool_schema('family_profile')['properties'],{})
        self.assertEqual(self.studio.mock_tool('family_profile',{},data,v)['child_nickname'],'侨侨')
        self.assertEqual(self.studio.mock_tool('family_profile',{},self.data,v)['status'],'unknown')

    def test_model_sees_business_contract_while_audit_keeps_simulation_receipt(self):
        v=self.save()
        responses=iter([
            {'role':'assistant','tool_calls':[{'id':'c1','type':'function','function':{'name':'family_profile','arguments':'{}'}}]},
            {'role':'assistant','content':'{"decision":"受阻"}'}])
        def model(messages,tools):
            visible=json.dumps([messages,tools],ensure_ascii=False)
            for marker in ['正在测试','教师测试沙箱','模拟事实','mock_sent','simulation']:
                self.assertNotIn(marker,visible)
            return next(responses)
        self.studio.model=model
        result=self.studio.simulate(v,self.data)
        self.assertTrue(result['simulation'])
        self.assertTrue(result['trace'][0]['result']['simulation'])

    def test_s01_checks_latest_learning_inside_send_without_precheck(self):
        v=self.save()
        for completed,read_course,send_day,expected in [(False,True,'2025-10-13','mock_sent'),(True,True,'2025-10-13','blocked'),(False,False,'2025-10-13','blocked'),(False,True,'2025-10-12','blocked')]:
            day='2025-10-13'
            profile={'class_type':'L2','cohort':'61'}
            schedule={**profile,'date':day,'status':'scheduled','lesson_id':'L2-001','lesson_no':1}
            learning={'lessons_as_of':[{'scheduled_date':day,'lesson_id':'L2-001','lesson_no':1,'status':'completed' if completed else 'not_started'}]}
            data={**self.data,'tool_results':{'family_profile':profile,'course_schedule':schedule,'learning_query':learning,'course_knowledge':{'status':'ok','body':'三个饲养场场主'}}}
            calls=[('course_schedule',{'date':day,**profile}),('learning_query',{'day':day})]
            if read_course:calls.append(('course_knowledge',{'class_type':'L2','lesson_no':1}))
            calls.append(('deliver_message',{'day':send_day,'body':'一起来认识三个饲养场场主吧！'}))
            responses=iter([{'role':'assistant','tool_calls':[{'id':str(i),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]} for i,(name,args) in enumerate(calls)]+[{'role':'assistant','content':'{"decision":"结束"}'}])
            self.studio.model=lambda *args:next(responses)
            result=self.studio.simulate(v,data)
            self.assertEqual(result['trace'][-1]['result']['status'],expected)
            if expected=='mock_sent':self.assertEqual(result['trace'][-1]['result']['body'],calls[-1][1]['body'])
        self.assertNotIn('check_s01',st.READ_TOOLS)

    def test_s08_uses_task_identifier_and_rechecks_weekly_learning(self):
        version={**self.save(),'skill':'close-week-reading-gap'}
        day='2025-10-24'
        lessons=[{'lesson_id':'L2-'+str(6+i),'lesson_no':6+i,'scheduled_date':'2025-10-'+str(20+i),
                  'status':'in_progress' if i==4 else 'completed'} for i in range(5)]
        for case,expected in [('eligible','mock_sent'),('completed','blocked'),('unknown','blocked'),('early','blocked'),('missing_material','blocked')]:
            current={'start':'2025-10-20','end':day,'coverage_complete':case!='unknown','scheduled':5,'completed':4,'lessons':lessons}
            data={'summary':'周五收尾','as_of':day+('T18:59:00+08:00' if case=='early' else 'T19:00:00+08:00'),
                  'delivery_candidate_id':'ref-s08','tool_results':{'learning_query':{'weekly_learning':{'current_week':current},'lessons_as_of':current['lessons']},
                    'daily_context':{},'course_schedule':{'status':'scheduled','date':day,'class_type':'L2','cohort':'61','lesson_id':'L2-10','lesson_no':10},
                    'course_knowledge':{'status':'ok','body':'人物信息卡'}}}
            if case=='completed':
                current['lessons']=[{**row,'status':'completed'} for row in lessons]
                current['completed']=5
            calls=[('learning_query',{'day':day}),('daily_context',{'day':day}),
                   ('course_schedule',{'date':day,'class_type':'L2','cohort':'61'})]
            if case!='missing_material':calls.append(('course_knowledge',{'class_type':'L2','lesson_no':10}))
            calls.append(('deliver_proactive',{'candidate_ids':['ref-s08'],'body':'这周只差今天一节，可以找方便的时间读一读。'}))
            responses=iter([{'tool_calls':[{'id':str(i),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
                            for i,(name,args) in enumerate(calls)]+[{'content':'{"decision":"执行"}'}])
            def model(messages,tools):
                self.assertIn('ref-s08',messages[1]['content'])
                return next(responses)
            self.studio.model=model
            result=self.studio.simulate(version,data)
            self.assertEqual(result['trace'][-1]['result']['status'],expected,case)
            self.assertFalse((self.root/'runtime/live.sqlite').exists())

    def test_real_model_loop_records_calls_but_has_no_delivery_client(self):
        v=self.save(); responses=iter([
            {'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':'check_proactive','arguments':'{"candidate_id":"old"}'}}]},
            {'role':'assistant','content':json.dumps({'decision':'跳过','reason':'already_delivered','reply':''})}])
        self.studio.model=lambda *args:next(responses)
        result=self.studio.simulate(v,self.data)
        self.assertTrue(result['simulation']);self.assertEqual(result['output']['decision'],'跳过')
        self.assertEqual(result['trace'][0]['result']['status'],'already_delivered')
        self.assertFalse((self.root/'runtime/live.sqlite').exists())

    def test_generation_and_jobs_persist_version_and_dataset(self):
        v=self.save();self.studio.model=lambda *args:{'content':json.dumps(self.data)}
        job=self.studio.start_job(self.actor,{'version':v['id'],'prompt':'首课刚完成'},'generate')
        self.studio.pool.shutdown(wait=True)
        result=self.studio.job(job['id']);self.assertEqual(result['status'],'done')
        self.assertEqual(result['result'],self.data);self.assertEqual(result['version'],v['id'])

    def test_expected_outcome_hidden_and_unchecked_send_is_blocked(self):
        v=self.save();data={**self.data,'expected':'SECRET_EXPECTATION','summary':'SECRET_SUMMARY'}
        responses=iter([
            {'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':'deliver_proactive','arguments':'{"candidate_ids":["new"],"body":"test"}'}}]},
            {'role':'assistant','content':'{"decision":"受阻"}'}])
        def model(messages,tools):
            self.assertNotIn('SECRET_EXPECTATION',json.dumps(messages));self.assertNotIn('SECRET_SUMMARY',json.dumps(messages))
            return next(responses)
        self.studio.model=model
        result=self.studio.simulate(v,data)
        self.assertEqual(result['trace'][0]['result']['status'],'blocked')

    def test_ui_controls_referenced_by_script_exist(self):
        root=Path(__file__).parent
        html=(root/'skill-studio.html').read_text(encoding='utf-8')
        script=(root/'skill-studio.js').read_text(encoding='utf-8')
        ids=set(re.findall(r'id="([^"]+)"',html))
        references=set(re.findall(r"\$\('([^']+)'\)",script)) | set(re.findall(r"listen\('([^']+)'",script))
        self.assertFalse(references-ids)

    def test_candidate_id_is_exact_and_checked_send_succeeds(self):
        v=self.save();data={**self.data,'tool_results':{'check_proactive':{'default':{'candidate_id':'mock-candidate','status':'eligible'}}}}
        self.assertEqual(self.studio.mock_tool('check_proactive',{'candidate_id':'H01'},data,v)['status'],'unknown')
        responses=iter([
            {'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':'check_proactive','arguments':'{"candidate_id":"mock-candidate"}'}}]},
            {'role':'assistant','content':None,'tool_calls':[{'id':'c2','type':'function','function':{'name':'deliver_proactive','arguments':'{"candidate_ids":["mock-candidate"],"body":"test"}'}}]},
            {'role':'assistant','content':'{"decision":"执行"}'}])
        self.studio.model=lambda *args:next(responses)
        result=self.studio.simulate(v,data)
        self.assertEqual(result['trace'][-1]['result']['status'],'mock_sent')


if __name__=='__main__':unittest.main()
