import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import tool_result_contract as contract


class EvidenceTests(unittest.TestCase):
    def test_completion_actions_use_explicit_completion_plan_fixture(self):
        from reply_lab_native import native_mode
        for name in ('celebrate-lesson-completion','remind-historical-makeup','celebrate-learning-streak','prevent-lesson-skipping'):
            self.assertEqual(native_mode('proactive_expression',name),'completion_action')
        self.assertEqual(native_mode('proactive_expression','remind-reading'),'execution')

    def test_duplicate_catalog_views_only_seed_one_identical_opportunity(self):
        from native_experiments import unique_seed_contract
        opportunity={'id':'birthday-1','status':'eligible','title':'生日'}
        catalog=[{'skill':'birthday','kind':'action','opportunities':[opportunity]},
                 {'skill':'birthday','kind':'highlight','opportunities':[copy.deepcopy(opportunity)]}]
        index,contract,rows=unique_seed_contract(catalog,{'skill':'birthday'})
        self.assertEqual(index,0);self.assertEqual(rows,[opportunity])
        catalog[0]['opportunities']=[]
        index,contract,rows=unique_seed_contract(catalog,{'skill':'birthday'})
        self.assertEqual(index,1);self.assertEqual(contract['kind'],'highlight')
        catalog[0]['opportunities']=[opportunity]
        catalog[1]['opportunities'][0]['id']='other'
        with self.assertRaisesRegex(ValueError,'ambiguous_seed_skill'):
            unique_seed_contract(catalog,{'skill':'birthday'})
        catalog[1]['opportunities'].append(copy.deepcopy(opportunity))
        with self.assertRaisesRegex(ValueError,'ambiguous_seed_opportunity'):
            unique_seed_contract(catalog,{'skill':'birthday'})

    def test_bad_source_reference_gets_actionable_error_without_delivery(self):
        import mcp_server,agent_plan,business
        with patch('service_workflow.enabled',return_value=False), patch('completion_planner.internal',return_value=False), \
             patch('operator_alerts.guard_model_delivery'), patch.object(agent_plan,'enabled',return_value=True), \
             patch.object(agent_plan,'require_skills'), patch.object(business,'original',side_effect=lambda x:x), \
             patch.object(business.s,'read',return_value=[]) as read, patch('wechat_test.http_send') as sender:
            with self.assertRaisesRegex(agent_plan.ServiceValidationError,'event_id 应使用当前任务引用'):
                mcp_server._business_call('reply_to_message',{'event_id':'source-message','body':'测试'})
            read.assert_any_call('SELECT kind FROM events WHERE id=?', ('source-message',))
            sender.assert_not_called()

    def test_passive_trial_selection_only_accepts_frozen_skill(self):
        from reply_lab_native import selected_passive_skill
        import reply_lab
        self.assertIsNone(selected_passive_skill('passive',None,{}))
        self.assertIsNone(selected_passive_skill('proactive_expression','x',{}))
        self.assertEqual(selected_passive_skill('passive','x',{'x':{}}),'x')
        with self.assertRaises(reply_lab.Fault):selected_passive_skill('passive','x',{})

    def test_found_is_not_verified_and_original_is_unchanged(self):
        raw={'status':'ok','matches':[{'uri':'viking://x','business_review_status':'unverified','body':'source','sha256':'same'}]}
        frozen=copy.deepcopy(raw);got=contract.annotate('course_search',{},raw)
        self.assertEqual(got['result_evidence']['retrieval']['state'],'found')
        self.assertEqual(got['result_evidence']['verification']['state'],'unverified')
        self.assertEqual(raw,frozen);self.assertEqual(got['matches'][0]['body'],'source')

    def test_review_is_per_item_and_unknown_is_not_promoted(self):
        rows=[{'business_review_status':'verified'},{'business_review_status':'unverified'},{}]
        got=contract.annotate('knowledge_search',{}, {'status':'ok','matches':rows})
        self.assertEqual(got['result_evidence']['verification']['state'],'mixed')
        self.assertEqual([x['material_verification']['state'] for x in got['matches']],['verified','unverified','unknown'])
        got=contract.annotate('course_read',{}, {'status':'changed','body':'old','business_review_status':'verified'})
        self.assertEqual(got['result_evidence']['verification']['state'],'unknown')
        got=contract.annotate('knowledge_read',{}, {'status':'ok','body':'FAQ','can_answer_directly':False,'business_review_status':'verified'})
        self.assertEqual(got['result_evidence']['verification']['state'],'unverified')

    def test_empty_search_does_not_claim_found_or_verified(self):
        for status in ['ok','no_match','unavailable','suppressed']:
            got=contract.annotate('knowledge_search',{}, {'status':status,'matches':[]})
            self.assertNotEqual(got['result_evidence']['retrieval']['state'],'found')
            self.assertEqual(got['result_evidence']['verification']['state'],'unknown')

    def test_emoji_authorization_is_preserved_not_business_verification(self):
        raw={'status':'ok','matches':[{'asset_id':'a'}],'selection_authorized_for_current_turn':True,'policy':{'max_per_delivery':1}}
        got=contract.annotate('knowledge_search',{'scope':'emoji'},raw)
        self.assertEqual(got['result_evidence']['verification']['scope'],'emoji_usage_only')
        self.assertTrue(got['selection_authorized_for_current_turn']);self.assertEqual(got['policy'],raw['policy'])

    def test_delivery_states_never_resolve_business_issue(self):
        for status in ['queued','accepted','sent','partial','unknown','failed','needs_revision','replan_required']:
            with self.subTest(status=status):
                raw={'status':status,'action_key':'same','reason':'original'}
                got=contract.annotate('deliver_proactive',{},raw)
                self.assertEqual({k:got[k] for k in raw},raw)
                self.assertEqual(got['result_evidence']['operation']['state'],status)
                self.assertEqual(got['result_evidence']['completion']['state'],'not_established')

    def test_finish_scope_and_noncompletion_outcomes(self):
        for outcome in ['completed','blocked','no_contact','cancelled']:
            got=contract.annotate('service_task_finish',{}, {'status':'done','outcome':outcome})
            self.assertEqual(got['result_evidence']['completion'],{'state':outcome,'scope':'current_service_task','note':'仅表示这个事件或任务的收尾结果，不证明退款完成、人工受理或家长疑虑解决。'})
        got=contract.annotate('finish_event',{}, {'status':'awaiting_delivery'})
        self.assertEqual(got['result_evidence']['completion']['state'],'awaiting_delivery')
        got=contract.annotate('service_task_finish',{}, {'status':'awaiting_delivery','outcome':'completed'})
        self.assertEqual(got['result_evidence']['completion']['state'],'awaiting_delivery')
        got=contract.annotate('finish_event',{}, {'status':'done'})
        self.assertEqual(got['result_evidence']['completion']['scope'],'current_message_event')

    def test_handoff_and_plan_are_not_execution(self):
        for name,state in [('request_human_confirmation','human_confirmation_required'),('workflow_commit','committed')]:
            got=contract.annotate(name,{}, {'status':state})
            self.assertEqual(got['result_evidence']['operation']['state'],state)
            self.assertEqual(got['result_evidence']['completion']['state'],'not_established')

    def test_wrapper_keeps_audit_and_original_exceptions(self):
        import experiment_result_mcp as wrapper
        import experiment_runtime as ex
        original=wrapper.original.business_call
        self.addCleanup(setattr,wrapper.original,'business_call',original)
        with patch.object(ex,'verify',return_value={'simulation':True}), patch.object(wrapper.original,'business_call',side_effect=ValueError('original_error')):
            wrapper.install()
            with self.assertRaisesRegex(ValueError,'original_error'):wrapper.original.business_call('deliver_message',{})
        with patch.object(ex,'verify',return_value=None):
            with self.assertRaisesRegex(ValueError,'experiment_only'):wrapper.install()


class DialogueSkillTests(unittest.TestCase):
    import test_message_hooks as base
    setUp=base.MessageHookTests.setUp
    tearDown=base.MessageHookTests.tearDown
    create_source=base.MessageHookTests.create_source
    set_context=base.MessageHookTests.set_context
    inbound=base.MessageHookTests.inbound

    def event(self):
        import agent_plan as ap,live_runtime
        ap.initialize();self.inbound();self.clock+=60
        return live_runtime.claim()['id']

    def test_real_skill_registration_bodies_and_version_refresh(self):
        import experiment_dialogue_context as context,experiment_runtime as ex
        import agent_plan as ap,service_content as content,sql_tools as s,test_mode as tm
        ident=self.event();names=['service-policy','tutor-main','reply-style']
        folder=self.root/'published';folder.mkdir()
        for name in names:(folder/name).write_text('BODY '+name,encoding='utf8')
        revisions={name:'v1-'+name for name in names}
        with patch.object(ex,'verify',return_value={'simulation':True}), patch.object(context.business,'task',return_value={'prompt':'current message'}), \
             patch.object(content,'skill_file',side_effect=lambda name:folder/name), patch.object(content,'bundle_digest',side_effect=lambda name:revisions[name]), \
             patch.object(content,'reference_files',return_value=[]):
            got=context.prepare(ident)
            ap.require_skills('tutor-main','reply-style')
            for name in names:self.assertIn('BODY '+name,got['prompt'])
            self.assertEqual([x['name'] for x in got['prepared_skills']],names)
            rows=s.read('SELECT * FROM service_skill_reads WHERE run_id=?',(tm.state()['run_id'],))
            self.assertEqual({x['name'] for x in rows},set(names))
            audit=s.read("SELECT origin,name,output FROM tool_audit WHERE name='service_skill'")
            self.assertEqual(len(audit),3);self.assertTrue(all(x['origin']=='host' for x in audit))
            revisions['reply-style']='v2';(folder/'reply-style').write_text('NEW BODY',encoding='utf8')
            with self.assertRaises(ap.ServiceValidationError):ap.require_skills('reply-style')
            got=context.prepare(ident);self.assertIn('NEW BODY',got['prompt']);ap.require_skills('reply-style')

    def test_no_registration_outside_experiment_or_for_missing_event(self):
        import experiment_dialogue_context as context,experiment_runtime as ex,agent_plan as ap
        self.event()
        with patch.object(ex,'verify',return_value=None),patch.object(ap,'load_skill') as load:
            with self.assertRaises(ValueError):context.prepare('missing')
            load.assert_not_called()
        with patch.object(ex,'verify',return_value={'simulation':True}),patch.object(ap,'load_skill') as load:
            with self.assertRaisesRegex(ValueError,'dispatched_event_required'):context.prepare('missing')
            load.assert_not_called()

    def test_family_passive_preload_and_send_share_style_free_contract(self):
        import agent_plan as ap, business, experiment_dialogue_context as context
        import experiment_runtime, family_lab_bridge, mcp_server
        ident = self.event()
        with patch.object(family_lab_bridge, 'configuration', return_value={'family_id':'F01'}), patch.object(family_lab_bridge, 'verify', return_value=None), patch.object(experiment_runtime, 'verify', return_value=True), patch('service_content.skill_file', side_effect=lambda n: Path(__file__).resolve().parents[1]/'skills'/n/'SKILL.md'), patch.object(business, 'task', return_value={'prompt':'current message'}):
            packet = context.prepare(ident)
            self.assertEqual([x['name'] for x in packet['prepared_skills']], ['service-policy','tutor-main'])
            with self.assertRaises(ap.ServiceValidationError):
                ap.require_skills('reply-style')
            with patch('service_workflow.enabled',return_value=False), patch('completion_planner.internal',return_value=False), patch('operator_alerts.guard_model_delivery'), patch.object(ap,'enabled',return_value=True), patch.object(business,'original',return_value='missing'), patch('wechat_test.http_send') as sender:
                # Reach event validation without any reply-style read or real delivery.
                with self.assertRaisesRegex(ap.ServiceValidationError, 'event_id 应使用当前任务引用'):
                    mcp_server._business_call('reply_to_message', {'event_id':'missing','body':'测试'})
                sender.assert_not_called()

    def test_missing_body_stops_before_model_prompt(self):
        import experiment_dialogue_context as context,experiment_runtime as ex,agent_plan as ap
        ident=self.event()
        with patch.object(ex,'verify',return_value={'simulation':True}),patch.object(context.business,'task',return_value={'prompt':'message'}),patch.object(ap,'load_skill',return_value={'body':''}):
            with self.assertRaisesRegex(ValueError,'body_missing'):context.prepare(ident)

    def test_explicit_passive_trial_preloads_actual_frozen_skill(self):
        import experiment_dialogue_context as context,experiment_runtime as ex
        import agent_plan as ap,service_content as content,sql_tools as s
        ident=self.event();names=['service-policy','tutor-main','reply-style','respond-child-sharing']
        folder=self.root/'published';folder.mkdir()
        for name in names:(folder/name).write_text('BODY '+name,encoding='utf8')
        frozen=self.root/'config/frozen-experiment-input.json';frozen.parent.mkdir(parents=True,exist_ok=True)
        frozen.write_text(json.dumps({'scenario':{'preload_skill':names[-1]},'config':{'skills':{names[-1]:{}}}}),encoding='utf8')
        with patch.object(s,'R',self.root),patch.object(ex,'verify',return_value={'simulation':True}),patch.object(context.business,'task',return_value={'prompt':'message'}), \
             patch.object(content,'skill_file',side_effect=lambda name:folder/name),patch.object(content,'bundle_digest',side_effect=lambda name:'digest-'+name), \
             patch.object(content,'reference_files',return_value=[]):
            got=context.prepare(ident)
            self.assertEqual([x['name'] for x in got['prepared_skills']],names)
            self.assertIn('BODY respond-child-sharing',got['prompt']);ap.require_skills('respond-child-sharing')
            frozen.write_text(json.dumps({'scenario':{'preload_skill':names[-1]},'config':{'skills':{}}}),encoding='utf8')
            with self.assertRaisesRegex(ValueError,'selected_skill_not_in_frozen_config'):context.prepare(ident)


if __name__=='__main__':unittest.main()
