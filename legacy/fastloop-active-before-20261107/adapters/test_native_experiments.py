"""Exercise real Git exports and isolated Python initialization, with no model."""
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
import native_experiments as native


class NativePreparationTests(unittest.TestCase):
    def setUp(self):
        fixture=native.ROOT/'runtime/fixtures/mock_uid_00001_first_month.json'
        if not fixture.exists():self.skipTest('Private fact fixture not installed')
        self.fixture=json.loads(fixture.read_text())
        self.temp=tempfile.TemporaryDirectory(dir=native.ROOT/'runtime');self.addCleanup(self.temp.cleanup)
        self.source=pathlib.Path(self.temp.name)/'source'
        subprocess.run(['git','clone','--quiet','--shared',str(native.ROOT),str(self.source)],check=True)
        self.config={'config_version':'config-1','persona':'Frozen persona','model':'test-model'}
        self.scene={'scenario_version':'scene-1','mode':'passive','family_uid':self.fixture['uid'],'as_of':'2025-10-13T07:00:00+08:00','fixture':self.fixture,'history':[]}

    def prepare(self,**changes):return native.prepare(self.config,{**self.scene,**changes},source=self.source)

    def test_override_removes_deleted_nested_references(self):
        self.config['skills']={'service-policy':{'SKILL.md':'Frozen complete candidate'}}
        result=self.prepare();w=pathlib.Path(result['run_root'])/'workspace'
        files={p.relative_to(w/'skills/service-policy').as_posix() for p in (w/'skills/service-policy').rglob('*.md')}
        self.assertEqual(files,{'SKILL.md'})
        check=subprocess.run([sys.executable,'-B',str(w/'adapters/experiment_runtime.py'),'verify'],capture_output=True,text=True)
        self.assertEqual(check.returncode,0,check.stderr)

    def test_two_runs_have_distinct_database_session_and_frozen_components(self):
        first=self.prepare();self.config['persona']='Edited persona';second=self.prepare()
        self.assertNotEqual(first['business_run_id'],second['business_run_id'])
        self.assertNotEqual(first['run_root'],second['run_root'])
        one=pathlib.Path(first['run_root'])/'workspace';two=pathlib.Path(second['run_root'])/'workspace'
        self.assertEqual(json.loads((one/'config/frozen-experiment-input.json').read_text())['config']['persona'],'Frozen persona')
        self.assertFalse((one/'runtime/service-session.json').exists())
        with closing(sqlite3.connect(one/'runtime/live.sqlite')) as c,c:c.execute("INSERT INTO messages VALUES('only-first','user','first','2025-10-13T06:00:00+08:00','test','hash',1)")
        with closing(sqlite3.connect(two/'runtime/live.sqlite')) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        (one/'skills/reply-style/SKILL.md').write_text('unexpected edit')
        p=subprocess.run([sys.executable,'-B',str(one/'adapters/experiment_runtime.py'),'verify'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0);self.assertIn('configuration_changed',p.stderr)

    def test_history_never_replays_and_future_history_rejected(self):
        row={'source_id':'history','speaker':'user','body':'prior message','created_at':'2025-10-13T06:00:00+08:00'}
        result=self.prepare(history=[row]);w=pathlib.Path(result['run_root'])/'workspace'
        with closing(sqlite3.connect(w/'runtime/live.sqlite')) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM messages').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],0)
        with self.assertRaisesRegex(ValueError,'future_or_invalid'):self.prepare(history=[{**row,'created_at':'2025-10-14T06:00:00+08:00'}])

    def test_execution_fixture_is_labeled_and_uses_same_task_contract(self):
        # S01 shares the 06:30 planning wake even when this experiment starts at 07:00.
        due=self.scene['as_of'][:10]+'T06:30:00+08:00'
        result=self.prepare(mode='execution',initial_tasks=[{'skill':'remind-reading','run_at':due}])
        with closing(sqlite3.connect(pathlib.Path(result['run_root'])/'workspace/runtime/live.sqlite')) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM service_task_versions').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM service_cases').fetchone()[0],1)
            self.assertEqual(json.loads(c.execute('SELECT body FROM daily_plans').fetchone()[0])['source'],'experiment_task_fixture')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],0)
            self.assertEqual(c.execute("SELECT due_at FROM service_tasks WHERE skill='remind-reading'").fetchone()[0],due)

    def test_unknown_model_parameters_and_foreign_family_rejected(self):
        with self.assertRaisesRegex(ValueError,'fixture_family_mismatch'):self.prepare(family_uid='foreign')
        self.config['temperature']=0.7
        with self.assertRaisesRegex(ValueError,'unsupported_experiment_config'):self.prepare()

    def test_parameters_are_frozen_per_run(self):
        self.config.update(model='deepseek-v4.1-flash-economy',parameters={'thinking':'disabled','temperature':.4,'max_tokens':4096})
        first=self.prepare();self.config['parameters']['temperature']=.8;second=self.prepare()
        def params(run):return json.loads((pathlib.Path(run['run_root'])/'workspace/config/frozen-experiment-input.json').read_text())['config']['parameters']
        self.assertEqual(params(first)['temperature'],.4);self.assertEqual(params(second)['temperature'],.8)

    def test_readonly_knowledge_binding_preserves_frozen_file_set(self):
        result=self.prepare();w=pathlib.Path(result['run_root'])/'workspace'
        private=pathlib.Path(self.temp.name)/'test-credential';private.write_text('{"not_a_real_secret":true}')
        destination=native.bind_knowledge(w,{'knowledge_ovcli_config':str(private)})
        self.assertEqual(destination.parent,w/'runtime')
        self.assertEqual(destination.stat().st_mode & 0o777,0o600)
        p=subprocess.run([sys.executable,'-B',str(w/'adapters/experiment_runtime.py'),'verify'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)


if __name__=='__main__':unittest.main()
