"""Internal Skill editing is enabled only by the isolated reply-lab server."""
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
import teacher_workbench as teacher
from reply_lab_native import native_mode, planning_result


class InternalDraftTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.studio=object.__new__(teacher.TeacherStudio)
        self.studio.root=root;self.studio.skills=root/'skills';self.studio.source_home=root/'runtime/skill-studio'
        for name in ('plan-after-lesson-completion','review-service-case','reply-style'):
            directory=self.studio.skills/name;directory.mkdir(parents=True)
            (directory/'SKILL.md').write_text('---\nname: '+name+'\ndescription: test internal draft\n---\n\n# Task\n\nKeep actual records and avoid sending extra messages.\n')

    def test_default_workbench_keeps_existing_internal_exclusion(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(self.studio.installed(),['reply-style'])
            with self.assertRaises(teacher.legacy.Fault):self.studio.source('plan-after-lesson-completion')

    def test_isolated_drafts_are_editable_but_never_publishable(self):
        with patch.dict(os.environ,{'TUTOR_EXPERIMENT_INTERNAL_DRAFTS':'1'}):
            names=self.studio.installed()
            self.assertIn('plan-after-lesson-completion',names);self.assertIn('review-service-case',names)
            for name in ('plan-after-lesson-completion','review-service-case'):
                files=self.studio.source(name);self.assertEqual(self.studio.validate(name,files),files)
            with self.assertRaises(teacher.legacy.Fault):self.studio.publish(None,{})

    def test_planning_uses_its_own_mode_and_distinguishes_commit_from_ok_rpc(self):
        self.assertEqual(native_mode('planning'),'planning')
        self.assertEqual(native_mode('planning','plan-after-lesson-completion'),'completion')
        self.assertEqual(native_mode('planning','review-service-case'),'case_review')
        with self.assertRaises(KeyError):native_mode('unrecognized')
        db=Path(self.temp.name)/'plan.sqlite'
        with sqlite3.connect(db) as c:
            c.execute('CREATE TABLE service_tasks (id TEXT,skill TEXT,kind TEXT,status TEXT,result TEXT,created_at TEXT)')
            c.execute("INSERT INTO service_tasks VALUES ('t','plan-today','plan','done','{}','2025-01-01')")
        trace=[{'name':'service_skill','status':'ok','input':'{"name":"plan-service-day"}'},
               {'name':'workflow_commit','status':'ok','output':'{"status":"needs_revision"}'}]
        result=planning_result(db,{'tool_trace':trace,'parts':[]})
        self.assertTrue(result['plan_skill_loaded']);self.assertFalse(result['plan_commit_observed'])
        trace[1]['output']='{"status":"committed"}'
        self.assertTrue(planning_result(db,{'tool_trace':trace,'parts':[]})['plan_commit_observed'])
        self.assertEqual(planning_result(db,{'tool_trace':trace,'parts':[{}]})['external_message_count'],1)
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO service_tasks VALUES ('c','plan-after-lesson-completion','completion_plan','done','{}','2025-01-01')")
        completion=[{'name':'service_skill','status':'ok','input':'{"name":"plan-after-lesson-completion"}'},
                    {'name':'completion_plan_commit','status':'ok','output':'{"status":"committed"}'}]
        result=planning_result(db,{'tool_trace':completion,'parts':[]},'plan-after-lesson-completion')
        self.assertTrue(result['plan_skill_loaded']);self.assertTrue(result['plan_commit_observed']);self.assertEqual(result['tasks'][0]['id'],'c')
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO service_tasks VALUES ('r','review-service-case','case_review','done','{}','2025-01-01')")
        review=[{'name':'service_skill','status':'ok','input':'{"name":"review-service-case"}'},
                {'name':'service_case_review','status':'ok','output':'{"status":"reviewed"}'}]
        result=planning_result(db,{'tool_trace':review,'parts':[]},'review-service-case')
        self.assertTrue(result['plan_skill_loaded']);self.assertTrue(result['plan_commit_observed']);self.assertEqual(result['tasks'][0]['id'],'r')


if __name__=='__main__':main()
