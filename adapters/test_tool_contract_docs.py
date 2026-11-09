"""Contract regressions: misleading prose must not grant effects or imply a disabled job."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import tool_surface as surface
import capability_surface as capabilities
import preference_memory as memory
import sql_tools as sql
import service_catalog as catalog

ROOT=Path(__file__).resolve().parents[1]

class MemoryConfigurationDocumentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'config').mkdir()
        p=patch.object(sql,'R',self.root);p.start();self.addCleanup(p.stop)

    def configure(self,**value):
        (self.root/'config/preference-memory.json').write_text(json.dumps(value))

    def test_absent_config_does_not_claim_governance_or_nightly_extraction(self):
        value=surface.memory_guidance()
        self.assertIn('治理记忆未启用',value)
        self.assertIn('夜间整理未启用',value)
        self.assertIn('speaker.self_reported_name',value)
        self.assertFalse((self.root/'runtime').exists())
        self.assertFalse((self.root/'config/preference-memory.json').exists())

    def test_enabled_governance_without_scheduler_never_promises_nightly_work(self):
        self.configure(enabled=True,slow_enabled=False)
        self.assertIn('治理记忆已启用',surface.memory_guidance())
        self.assertIn('夜间整理未启用',surface.memory_guidance())
        self.assertNotIn('每天北京时间22点统一提取',memory.fast_instruction())
        self.assertIn('不承诺稍后自动提取',memory.fast_instruction())

    def test_nightly_requires_both_flags_and_does_not_enable_them(self):
        for enabled,slow in [(False,True),(True,True)]:
            self.configure(enabled=enabled,slow_enabled=slow)
            before=(self.root/'config/preference-memory.json').read_bytes()
            self.assertEqual('夜间整理已启用' in surface.memory_guidance(),enabled and slow)
            self.assertEqual(before,(self.root/'config/preference-memory.json').read_bytes())

    def test_public_guidance_uses_actual_config_without_opening_more_tools(self):
        self.configure(enabled=False)
        with patch.object(capabilities,'enabled',return_value=True),patch('delivery_capability.enabled',return_value=True):
            before=capabilities.retired().copy()
            self.assertIn('治理记忆未启用',capabilities.guidance())
            self.assertEqual(before,capabilities.retired())
        with patch.object(surface,'enabled',return_value=True),patch('service_workflow.enabled',return_value=True),patch('host_tool_steps.enabled',return_value=True):
            self.assertNotIn('memory_remember',surface.retired())
            self.configure(enabled=True,slow_enabled=False)
            self.assertIn('memory_remember',surface.retired())

class ToolInstructionTests(unittest.TestCase):
    def test_current_skill_instructions_do_not_request_retired_calls(self):
        retired={'service_skill','service_reference','service_catalog','reply_to_message','reply_with_highlights','deliver_message','deliver_proactive','deliver_festival','family_profile','student_tier','daily_context','learning_context','action_candidates','check_proactive','completion_plan_context','finish_event','pause_contact','service_schedule','save_plan','transcribe_audio','inspect_festival_image','course_read','course_browse','course_search','course_knowledge','workflow_context','workflow_recheck'}
        violations=[]
        for file in (ROOT/'skills').rglob('*.md'):
            relative=file.relative_to(ROOT).as_posix()
            if relative in {'skills/plan-today/SKILL.md','skills/service-policy/references/supervision.md'}:continue
            for number,line in enumerate(file.read_text().splitlines(),1):
                for name in retired:
                    if re.search(r'(?<![\w.-])'+name+r'\s*\(',line):violations.append((relative,number,name))
        self.assertEqual(violations,[])

    def test_host_revision_instructions_request_amend_not_read_only_inspect(self):
        with patch.object(capabilities,'enabled',return_value=True),patch('host_tool_steps.enabled',return_value=True),patch('delivery_capability.enabled',return_value=True):
            text=surface.current_instructions('本轮使用workflow_context和workflow_commit修订任务')
            self.assertIn('workflow_read(view=context,purpose=amend)',text)
        with patch.object(capabilities,'enabled',return_value=False):
            original='本轮使用workflow_context和workflow_commit修订任务'
            self.assertEqual(surface.current_instructions(original),original)

    def test_s01_and_passive_examples_cannot_select_event_day_or_other_candidates(self):
        text=(ROOT/'skills/remind-reading/SKILL.md').read_text()
        calls=re.findall(r'`send_message\((.*?)\)`',text)
        self.assertTrue(calls)
        self.assertTrue(all('day=' not in c and 'candidate_ids=' not in c and 'event_id=' not in c for c in calls))
        text=(ROOT/'skills/tutor-main/SKILL.md').read_text()
        self.assertIn('event_id、day、家庭由宿主绑定',text)
        self.assertIn('workflow_fulfill',text)

    def test_catalog_dependencies_match_independent_task_loading_without_new_actions(self):
        self.assertEqual(len(catalog.ROWS),36)
        self.assertEqual(len(catalog.SUPPLEMENTAL_ROWS),1)
        for code in [f'S{i:02d}' for i in range(1,12)]+['I01','I02','I03','I04','I05-festival']:
            dependency=catalog.execution_dependencies(code)
            self.assertEqual(dependency['skills'],['reply-style'],code)
            self.assertFalse(any(x.startswith('course_read') or x=='course_knowledge' for x in catalog.action_dependencies(code)['knowledge']))
        self.assertIn('service-policy',catalog.action_dependencies('S08')['skills'])
        self.assertEqual({r[2] for r in catalog.ROWS if r[0] in catalog.WEEKLY_INTERACTION_IDS},{'invite-reading-interaction'})

if __name__=='__main__':unittest.main()
