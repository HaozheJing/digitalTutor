import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from digital_tutor.application import Application
from digital_tutor.demo import seed_learning
from digital_tutor.hermes_tools import register_tools
from digital_tutor.tools import ToolRegistry


class BridgeTests(unittest.TestCase):
    def test_native_handler_preserves_sql_result_and_records_scope_without_rows(self):
        app=Application(':memory:')
        try:
            app.create_family('A','secret-uid-A','mock-A')
            seed_learning(app,'secret-uid-A','secret-uid-B')
            token=app.open_session('A','service'); registered={}; trace=[]
            def register(**kwargs): registered[kwargs['name']]=kwargs
            with patch.dict(sys.modules,{'tools.registry':SimpleNamespace(registry=SimpleNamespace(register=register))}):
                names=register_tools(ToolRegistry(app,token),trace,exclude_materials=True)
            self.assertNotIn('dt_skills_read',names)
            self.assertNotIn('dt_planner_commit_daily',names)
            output=json.loads(registered['dt_learning_query_sql']['handler']({'sql':'SELECT uid,day FROM learning_days'}))
            self.assertTrue(output['ok'])
            self.assertEqual(len(output['result']['rows']),3)
            self.assertTrue(trace[-1]['authorized_uid_only'])
            self.assertEqual(trace[-1]['returned_rows'],3)
            self.assertNotIn('secret-uid',json.dumps(trace))
            app.revoke_session(token)
            rejected=json.loads(registered['dt_state_read']['handler']({}))
            self.assertEqual(rejected['error']['code'],'RUN_REVOKED')
        finally:
            app.close()
