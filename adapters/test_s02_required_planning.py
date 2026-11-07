import json,sqlite3,unittest
from contextlib import closing
import sql_tools as s,test_mode as tm,completion_planner as cp,service_workflow as wf
import test_completion_ledger as ledger_tests

class RequiredS02PlanningTests(unittest.TestCase):
    setUp=ledger_tests.LedgerTests.setUp
    tearDown=ledger_tests.LedgerTests.tearDown
    create_source=ledger_tests.LedgerTests.create_source
    set_context=ledger_tests.LedgerTests.set_context
    begin=ledger_tests.LedgerTests.begin
    choices=ledger_tests.LedgerTests.choices

    def workflow_begin(self,at='2025-10-17 17:25'):
        task,_=self.begin(at);wf.initialize();state=tm.state();state['service_workflow_mode']=wf.MODE
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        return task,cp.context()

    def test_s01_sent_does_not_cover_friday_completion_and_rejection_is_atomic(self):
        task,ctx=self.workflow_begin()
        with closing(sqlite3.connect(s.DB)) as c,c:
            cp.insert(c,tm.state(),'earlier-s01','s01','remind-reading','2025-10-17T06:30:00+08:00','2025-10-17T23:59:59+08:00',None,'morning invitation')
            c.execute("UPDATE service_tasks SET status='done' WHERE id='earlier-s01'")
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('s01-already-sent','读书邀请','sent','2025-10-17T06:30:00+08:00','test'))
            c.execute('INSERT INTO service_links VALUES(?,?)',('earlier-s01','s01-already-sent'))
        ctx=cp.context();item=next(x for x in ctx['candidates'] if x['skill']==cp.RULES['S02']);self.assertTrue(item['planning_requirement']['required_task'])
        bad=self.choices(ctx);bad[0]['reason']='S01已经覆盖今日完课，S02仅首日发送'
        with self.assertRaisesRegex(ValueError,'S01课前邀请'):cp.commit('bad-friday-skip',ctx['context_token'],bad)
        self.assertFalse(s.read('SELECT * FROM completion_plans'))
        good=self.choices(ctx,'S02');receipt=cp.commit('correct-friday-plan',ctx['context_token'],good)
        self.assertEqual(receipt['status'],'committed');self.assertTrue(cp.commit('correct-friday-plan',ctx['context_token'],good)['duplicate'])
        self.assertEqual(len(s.read("SELECT * FROM service_tasks WHERE skill='celebrate-lesson-completion'")),1)

    def test_real_day_pause_allows_skip_without_creating_contact(self):
        _,ctx=self.workflow_begin()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-17','parent-pause','今天不联系',s.now()))
        ctx=cp.context();item=next(x for x in ctx['candidates'] if x['skill']==cp.RULES['S02']);self.assertEqual(item['planning_requirement']['skip_basis'],'day_paused')
        self.assertEqual(cp.commit('paused-friday-skip',ctx['context_token'],self.choices(ctx))['status'],'committed')
        self.assertFalse(s.read("SELECT * FROM service_tasks WHERE skill='celebrate-lesson-completion'"))

    def test_same_event_s04_merge_still_suppresses_s02(self):
        _,ctx=self.workflow_begin('2025-10-15 17:25');item=next(x for x in ctx['candidates'] if x['skill']==cp.RULES['S02']);self.assertEqual(item['status'],'merged_into_higher_priority')
        self.assertFalse(item['planning_requirement']['required_task'])
        self.assertEqual(cp.commit('streak-covers-s02',ctx['context_token'],self.choices(ctx,'S04'))['status'],'committed')
        self.assertFalse(s.read("SELECT * FROM service_tasks WHERE skill='celebrate-lesson-completion'"))
