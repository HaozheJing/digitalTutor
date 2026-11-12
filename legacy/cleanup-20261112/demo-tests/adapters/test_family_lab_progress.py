import sqlite3,tempfile,unittest
from pathlib import Path
from family_lab import Lab
from family_lab_server import Workbench
from family_lab_progress import activities

class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.lab=Lab(self.root/'control.sqlite');self.lab.initialize()
        fs=[dict(family_id='F0'+str(i),student='student'+str(i),workspace=str(self.root/str(i))) for i in range(1,6)]
        self.run=self.lab.create_run(fs,'2025-10-13T06:00:00+08:00')
        self.w=Workbench.__new__(Workbench);self.w.lab=self.lab
        for f in fs:
            db=Path(f['workspace'])/'runtime/live.sqlite';db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as c:c.execute('CREATE TABLE service_tasks(id TEXT,kind TEXT,title TEXT,skill TEXT,status TEXT,due_at TEXT,expires_at TEXT,not_before_wall REAL)')
        self.sync()
    def sync(self):
        for f in self.lab.snapshot()['families']:self.lab.acknowledge(self.run,f['family_id'],f['inbox_seq'],self.lab.current()['epoch'])
    def plan(self,fid='F01',due='2025-10-13T06:30:00+08:00'):
        db=Path(self.w.family(fid)['workspace'])/'runtime/live.sqlite'
        with sqlite3.connect(db) as c:c.execute('INSERT INTO service_tasks VALUES(?,?,?,?,?,?,?,NULL)',('plan','plan','daily','plan-service-day','planned',due,'2025-10-13T21:59:59+08:00'))
    def action(self):return self.w.action('next-event',{'run_id':self.run,'family_id':'F01'})
    def test_preopening_targets_today_and_starts_execution(self):
        v=self.action()
        self.assertEqual(v['target'],'2025-10-13T06:30:00+08:00')
        self.assertEqual(self.lab.current()['paused'],0)
        self.assertEqual(self.lab.current()['clock_mode'],'frozen')
    def test_paused_previous_transition_continues_without_skipping(self):
        old=self.lab.advance(self.run,'2025-10-13T06:30:00+08:00');self.sync();self.plan()
        v=self.action();again=self.action()
        self.assertEqual(v['transition_id'],old);self.assertEqual(again['transition_id'],old)
        self.assertEqual(self.lab.current()['paused'],0)
        self.assertEqual(self.lab.current()['as_of'],'2025-10-13T06:30:00+08:00')
        self.assertEqual(len(self.lab.read('SELECT * FROM transitions')),1)
    def test_due_current_planner_runs_before_any_later_event(self):
        self.plan(due='2025-10-13T06:00:00+08:00')
        v=self.action();self.assertTrue(v['continued'])
        self.assertEqual(v['target'],'2025-10-13T06:00:00+08:00')
        self.assertEqual(self.lab.current()['paused'],0)
    def test_progress_shows_paused_due_then_model_queue_and_running(self):
        self.plan(due='2025-10-13T06:00:00+08:00')
        a=activities(self.lab,self.lab.snapshot())[0]
        self.assertEqual(a['phase'],'experiment_paused');self.assertEqual(a['due_count'],1)
        self.lab.control(self.run,'F01','experiment_pause',False)
        request=self.lab.model_enqueue(self.run,'F01','plan','background','test-model')
        a=activities(self.lab,self.lab.snapshot())[0];self.assertEqual(a['phase'],'model_queue')
        self.assertIn('制定当日服务计划',a['summary'])
        self.assertTrue(self.lab.model_claim(request))
        a=activities(self.lab,self.lab.snapshot())[0];self.assertEqual(a['phase'],'model');self.assertIsNotNone(a['elapsed_seconds'])
    def test_pending_model_prevents_false_settled(self):
        v=self.action();self.sync()
        self.lab.model_enqueue(self.run,'F01','plan','background','test-model')
        status=self.lab.transition_status(v['transition_id'])
        self.assertEqual(status['status'],'processing');self.assertEqual(status['pending_models'],1)
    def test_pause_and_takeover_remain_family_scoped(self):
        self.plan(due='2025-10-13T06:00:00+08:00');self.plan('F02',due='2025-10-13T06:00:00+08:00')
        self.lab.control(self.run,'F01','takeover',True)
        self.action();self.sync()
        states=activities(self.lab,self.lab.snapshot())
        self.assertEqual(states[0]['phase'],'human');self.assertEqual(states[0]['due_count'],0)
        self.assertEqual(states[1]['phase'],'scheduled');self.assertEqual(states[1]['due_count'],1)

if __name__=='__main__':unittest.main()
