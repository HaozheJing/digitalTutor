"""Integration contract: every action must be loadable with its real dependencies.

No production sends/network. Fixture DB and all RPC calls are isolated.
Behavioral authoring/forward tests are recorded separately from these checks.
"""
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
import test_fixture_integration as fixtures
import service_catalog as catalog
import service_content as content
import agent_plan as ap
import sql_tools as s
import test_mode as tm


class ServiceAlignmentTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def test_every_action_loads_through_actual_mcp_with_references(self):
        import mcp_server as mcp
        ap.initialize()
        before=s.read('SELECT * FROM deliveries')
        tools={name for name,_,_ in mcp.base_mcp.TOOLS}
        self.assertTrue({'service_catalog','service_skill','service_reference'}<=tools)
        result=mcp.audited_call('service_catalog',{})
        expected={f'{prefix}{n:02d}' for prefix,count in [('S',11),('I',5),('H',13),('Q',7)] for n in range(1,count+1)}
        self.assertEqual({a['id'] for a in result['actions']},expected)
        self.assertEqual(result['mounted'],36)
        for action in result['actions']:
            with self.subTest(action=action['id']):
                value=mcp.audited_call('service_skill',{'name':action['skill']})
                self.assertIn(action['id'],value['action_ids'])
                self.assertEqual(value['sha256'],action['sha256'])
                required='reply-style' if action['id'].startswith('S') or action['id'] in catalog.WEEKLY_INTERACTION_IDS else 'service-policy'
                # Current host contracts may preload shared rules through declared dependencies.
                # The loop below still loads each dependency through the real MCP handler.
                self.assertTrue(required in value['body'] or required in action['dependencies']['skills'],
                                'Required shared rule is absent from both Skill prose and host dependencies')
                for name in action['dependencies']['skills']:
                    mcp.audited_call('service_skill',{'name':name})
                for ref in action['dependencies']['references']:
                    self.assertTrue(mcp.audited_call('service_reference',ref)['body'])
                # Resolve every declared reference with the actual containment guard.
                for path in value['references']:
                    read=mcp.audited_call('service_reference',{'name':action['skill'],'path':path,'max_lines':120})
                    self.assertTrue(read['body'])
                if action['skill']!='invite-reading-interaction':
                    self.assertTrue(value['references'],'Action needs attributable examples/usage notes')
        self.assertEqual(s.read('SELECT * FROM deliveries'),before)
        reads=s.read("SELECT name FROM tool_audit WHERE name='service_skill' AND status='ok'")
        self.assertGreaterEqual(len(reads),36)

    def test_shared_policy_is_mandatory_and_version_sensitive(self):
        ap.initialize()
        ap.load_skill('tutor-main')
        with self.assertRaisesRegex(ValueError,'service-policy'):ap.require_skills('tutor-main')
        ap.load_skill('service-policy');ap.require_skills('tutor-main')
        original=content.skill_file
        changed=self.root/'new-policy.md'
        changed.write_text(original('service-policy').read_text(encoding='utf8')+'\nNew policy revision\n',encoding='utf8')
        with patch.object(content,'skill_file',side_effect=lambda name:changed if name=='service-policy' else original(name)):
            with self.assertRaisesRegex(ValueError,'service-policy'):ap.require_skills('tutor-main')
            ap.load_skill('service-policy');ap.require_skills('tutor-main')

    def test_weekly_interaction_has_one_bundle_for_all_four_angles(self):
        ap.initialize()
        actions=[a for a in catalog.catalog()['actions'] if a['id'] in catalog.WEEKLY_INTERACTION_IDS]
        self.assertEqual(len(actions),4)
        self.assertEqual({a['skill'] for a in actions},{catalog.WEEKLY_INTERACTION_SKILL})
        self.assertEqual(len({a['sha256'] for a in actions}),1)
        self.assertTrue(all(a['selection']=='agent_choice' and not a['independent_task'] for a in actions))
        for a in actions:
            self.assertEqual(set(a['shared_with']),set(catalog.WEEKLY_INTERACTION_IDS)-{a['id']})
        ap.load_skill('service-policy')
        for old in catalog.SKILL_ALIASES:
            loaded=ap.load_skill(old)
            self.assertEqual(loaded['name'],catalog.WEEKLY_INTERACTION_SKILL)
            self.assertEqual(loaded['action_ids'],list(catalog.WEEKLY_INTERACTION_IDS))
            ap.require_skills(old,catalog.WEEKLY_INTERACTION_SKILL)
            self.assertEqual(loaded['references'],['references/planning-contract.md'])
            with self.assertRaises(ValueError):content.reference(old,'references/examples.md')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_legacy_interaction_publication_cannot_override_merged_bundle(self):
        root_patch=patch.object(s,'R',self.root)
        root_patch.start();self.addCleanup(root_patch.stop)
        name=catalog.WEEKLY_INTERACTION_SKILL
        home=s.R/'runtime/skill-studio';home.mkdir(parents=True,exist_ok=True)
        version='a'*32;legacy='b'*32
        new=home/'bundles'/version/name/'SKILL.md';new.parent.mkdir(parents=True)
        new.write_text('Current teacher-published weekly interaction',encoding='utf8')
        old_name=next(iter(catalog.SKILL_ALIASES))
        old=home/'bundles'/legacy/old_name/'SKILL.md';old.parent.mkdir(parents=True)
        old.write_text('Old standalone angle',encoding='utf8')
        (home/'active.json').write_text(json.dumps({name:{'version':version},old_name:{'version':legacy}}),encoding='utf8')
        self.assertEqual(content.skill_file(old_name),new)
        self.assertEqual(content.skill_file(name),new)

    def test_shared_reference_change_invalidates_prior_read(self):
        ap.initialize()
        original=content.skill_file
        policy=self.root/'policy'/'SKILL.md'
        refs=policy.parent/'references';refs.mkdir(parents=True)
        policy.write_text('unchanged policy body',encoding='utf8')
        rules=refs/'shared.md';rules.write_text('rule version one',encoding='utf8')
        with patch.object(content,'skill_file',side_effect=lambda name:policy if name=='service-policy' else original(name)):
            first=ap.load_skill('service-policy');ap.require_skills()
            rules.write_text('rule version two',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'service-policy'):ap.require_skills()
            second=ap.load_skill('service-policy');ap.require_skills()
            self.assertEqual(first['sha256'],second['sha256'])
            self.assertNotEqual(first['bundle_sha256'],second['bundle_sha256'])
            self.assertIn('rule version two',[x['body'] for x in second['shared_rules']])
            self.assertNotIn('rule version one',[x['body'] for x in second['shared_rules']])

    def test_passive_reply_cannot_bypass_shared_read_gate(self):
        import mcp_server as mcp
        ap.enable()
        with self.assertRaisesRegex(ValueError,'service-policy'):
            mcp.business_call('reply_to_message',{'event_id':'test-no-event','body':'test'})
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_catalog_preserves_merged_birthday_and_capability_gaps(self):
        actions={a['id']:a for a in catalog.catalog()['actions']}
        self.assertEqual(actions['I05']['skill'],actions['H10']['skill'])
        self.assertEqual(actions['I05']['shared_with'],['H10'])
        self.assertEqual(actions['I01']['dependencies']['skills'],['reply-style'])
        self.assertIn('工单未接',actions['Q04']['runtime_path'])
        self.assertIn('未接',actions['H13']['runtime_path'])
        self.assertNotIn('approved',actions['Q01']['runtime_path'])
        with self.assertRaises(ValueError):catalog.catalog('../../config')
        self.assertEqual(catalog.catalog('Q05')['total'],1)

    def test_model_catalog_has_no_future_fixture_or_private_configuration(self):
        value=json.dumps(catalog.catalog(),ensure_ascii=False)
        for forbidden in ['candidate_at','img_url','birthday_today','Bearer ','api_key','wechat-token']:
            self.assertNotIn(forbidden,value)


if __name__=='__main__':unittest.main()
