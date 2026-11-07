import json, unittest
from copy import deepcopy
from completion_model_view import for_model

class CompletionModelViewTests(unittest.TestCase):
    def test_long_audit_history_does_not_duplicate_current_facts_or_parent_feedback(self):
        learning={'lessons':[{'id':'L2-013','status':'started'}], 'profile':{'guardian_role':'mother'}}
        feedback={'kind':'family_feedback','at':'2025-10-27','body':{'quote':'暂停到下周','action':'pause','source_message_id':'parent-1'}}
        planned={'kind':'planned','at':'2025-10-26','body':{'reason':'历史模型判断'*5000,'task_id':'old-task','business_at':'2025-10-26T19:00:00+08:00'}}
        case={'status':'paused','goals':[{'lesson_id':'L2-013','completed_event':None}], 'tasks':[{'id':'old-task','status':'done','result':'sent-receipt'}], 'timeline':[planned,feedback], 'history':[deepcopy(planned),deepcopy(feedback)]}
        value={'learning':learning,'workflow_facts':{'learning':deepcopy(learning),'constraints':[{'scope':'all','ends_at':'2025-11-03'}]},'candidates':[{'status':'eligible','event_id':'completion-1'}],'ledger':{'cases':[case],'constraints':[{'status':'paused'}],'plans':[{'event_id':'old-event','body':{'decisions':[{'code':'S03','decision':'skip','reason':'旧判断'*2000}]}}]},'context_token':'authoritative-full-state-digest'}
        original=deepcopy(value);view=for_model(value)
        self.assertEqual(value,original)
        self.assertEqual(view['learning'],learning)
        self.assertEqual(view['candidates'],value['candidates'])
        self.assertEqual(view['workflow_facts']['constraints'],value['workflow_facts']['constraints'])
        self.assertEqual(view['ledger']['constraints'],value['ledger']['constraints'])
        self.assertEqual(view['ledger']['cases'][0]['timeline'][1],feedback)
        self.assertEqual(view['ledger']['cases'][0]['goals'],case['goals'])
        self.assertEqual(view['ledger']['cases'][0]['tasks'],case['tasks'])
        self.assertEqual(view['context_token'],value['context_token'])
        self.assertEqual(view['ledger']['plans'][0]['decisions'][0]['decision'],'skip')
        self.assertLess(len(json.dumps(view)),len(json.dumps(value))//10)

    def test_distinct_learning_and_dialogue_amendments_are_not_removed(self):
        amendment={'kind':'planned','body':{'reason':'妈妈说周日十点','source':{'owner':{'type':'dialogue'}}}}
        value={'learning':{'as_of':'new'},'workflow_facts':{'learning':{'as_of':'old'}},'ledger':{'cases':[{'timeline':[amendment],'history':[{'kind':'other','body':{'quote':'保留独立原话'}}]}]},'workflow_versions':[{'id':'active','status':'planned','revision':2,'goal':'家长已确认周日','source':{'message':'parent-1'}},{'id':'old','status':'done','revision':1,'result':'sent','dedupe_key':'old-matter','reason':'过去模型判断'}]}
        view=for_model(value)
        self.assertEqual(view['workflow_facts']['learning'],value['workflow_facts']['learning'])
        self.assertEqual(view['ledger']['cases'],value['ledger']['cases'])
        self.assertEqual(view['workflow_versions'][0],value['workflow_versions'][0])
        for k in ('id','status','revision','result','dedupe_key'):self.assertEqual(view['workflow_versions'][1][k],value['workflow_versions'][1][k])
