"""Missing-source responsibility is checked per intent, not per message only."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from digital_tutor.application import Application

scripts=Path(__file__).resolve().parents[1]/'scripts'
if not scripts.is_dir():scripts=Path(__file__).resolve().parent
sys.path.append(str(scripts))
from stage4_behavior_checks import evaluate


class MissingSourceScopeTests(unittest.TestCase):
    def evaluate_intents(self,intents):
        with tempfile.TemporaryDirectory() as directory:
            app=Application(Path(directory)/'business.sqlite',mode='passive',strict_runtime=True)
            try:
                family='synthetic-missing-source'
                app.create_family(family,'synthetic-uid','mock:missing-source')
                with app.store.transaction():
                    app.store.put('message','synthetic-source',family,{
                        'direction':'inbound','body':'请说明这两项尚未提供原文的规则。',
                        'interpreted':True,'intents':copy.deepcopy(intents)})
                handler=SimpleNamespace(calls=[{'job_kind':'service','native':{'tool_trace':[]}}])
                checks,_,unresolved,_=evaluate(app,[family],handler,'missing_policy',
                    {'allow_unanswered':True},{},{},[True])
                return checks,unresolved
            finally:app.close()

    def partial_intents(self):
        return [{'id':'arbitrary-source-a','requires_reply':True,'reply_operation_refs':[],
                 'partial_reply_operation_refs':['synthetic-gap-receipt']},
                {'id':'different-source-b','requires_reply':True,'reply_operation_refs':[],
                 'partial_reply_operation_refs':['synthetic-gap-receipt']}]

    def test_two_partial_replies_preserve_each_unanswered_intent(self):
        checks,unresolved=self.evaluate_intents(self.partial_intents())
        self.assertTrue(checks['all_missing_source_intents_preserved'])
        self.assertTrue(checks['unmet_requirement_preserved'])
        self.assertEqual(unresolved,['synthetic-source'])

    def test_one_final_reply_cannot_hide_behind_another_unresolved_intent(self):
        intents=self.partial_intents();intents[0]['reply_operation_refs']=['synthetic-final-receipt']
        checks,unresolved=self.evaluate_intents(intents)
        self.assertTrue(checks['unmet_requirement_preserved'])  # Old bool check still passes.
        self.assertTrue(unresolved)
        self.assertFalse(checks['all_missing_source_intents_preserved'])

    def test_requires_reply_false_cannot_silently_drop_missing_source(self):
        intents=self.partial_intents();intents[0]['requires_reply']=False
        checks,unresolved=self.evaluate_intents(intents)
        self.assertTrue(unresolved)
        self.assertTrue(checks['unmet_requirement_preserved'])
        self.assertFalse(checks['all_missing_source_intents_preserved'])

    def test_interpreted_message_without_intents_is_not_coverage(self):
        checks,unresolved=self.evaluate_intents([])
        self.assertFalse(checks['all_missing_source_intents_preserved'])
        self.assertFalse(checks['unmet_requirement_preserved'])
        self.assertEqual(unresolved,[])


if __name__=='__main__':unittest.main()
