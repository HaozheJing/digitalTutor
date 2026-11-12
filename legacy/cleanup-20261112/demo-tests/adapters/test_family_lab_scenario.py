import json,sqlite3,tempfile,unittest
from pathlib import Path
from family_lab import Lab,encode
from family_lab_scenario import build_schedule,scenario_key,publish_due,next_time,DONE

class LearningScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        self.lab=Lab(root/'control.sqlite');self.lab.initialize()
        self.families=[dict(family_id='F0'+str(i),student=str(i),workspace=str(root/str(i))) for i in range(1,6)]
        for f in self.families:
            path=Path(f['workspace'])/'runtime/fixtures/mock_uid_00001_first_month.json';path.parent.mkdir(parents=True)
            path.write_text(encode({'enrollments':[{'id':'enrollment-'+f['family_id']+'-L2'}],'learning_daily':[{'lesson_no':i,'id':'L2-'+str(i).zfill(3),'date':'2025-10-'+str(12+i),'completed_at':None} for i in range(1,6)]}))
        self.run=self.lab.create_run(self.families,'2025-10-13T06:30:00+08:00')
        self.schedule=build_schedule(self.families)
        with self.lab.transaction() as c:c.execute('INSERT INTO settings VALUES(?,?)',(scenario_key(self.run),encode(self.schedule)))
        self.lab.control(self.run,'F01','experiment_pause',False)
    def at(self,stamp):
        with self.lab.transaction() as c:c.execute('UPDATE runs SET as_of=? WHERE id=?',(stamp,self.run))
    def publish(self,fid='F01'):
        return publish_due(self.lab,{'run_id':self.run,'family_id':fid},self.lab.current()['as_of'])
    def test_three_high_completion_two_low(self):
        self.assertEqual([len(DONE[f['family_id']]) for f in self.families],[5,5,4,1,1])
        self.assertEqual(len(self.schedule['events']),16)
        self.assertEqual(next_time(self.lab,self.lab.current()),'2025-10-13T17:20:00+08:00')
    def test_future_hidden_and_idempotent_family_specific_ingress(self):
        self.assertEqual(self.publish(),0)
        self.assertEqual(self.lab.read('SELECT COUNT(*) n FROM ingress')[0]['n'],0)
        self.at('2025-10-13T17:20:00+08:00')
        self.assertEqual(self.publish(),1);self.assertEqual(self.publish(),0);self.assertEqual(self.publish('F02'),0)
        rows=self.lab.read('SELECT family_id,body,occurred_at FROM ingress')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['family_id'],'F01')
        self.assertEqual(json.loads(rows[0]['body'])['lesson_id'],'L2-001')
        self.assertEqual(rows[0]['occurred_at'],self.lab.current()['as_of'])
    def test_pause_and_prior_manual_completion_preserved(self):
        self.at('2025-10-13T18:00:00+08:00');self.lab.control(self.run,'F01','experiment_pause',True)
        self.assertEqual(self.publish(),0)
        self.lab.control(self.run,'F01','experiment_pause',False)
        payload=dict(self.schedule['events'][0]['payload']);payload.update(event_id='manual-first',completion_id='manual-first',completed_at='2025-10-13T17:00:00+08:00')
        self.lab.ingest(self.run,'F01','manual-first','completion',payload,payload['completed_at'])
        self.assertEqual(self.publish(),0)
        self.assertEqual(self.lab.read('SELECT COUNT(*) n FROM ingress')[0]['n'],1)
    def test_completed_baseline_is_not_scheduled_again(self):
        f=self.families[0];path=Path(f['workspace'])/'runtime/fixtures/mock_uid_00001_first_month.json'
        data=json.loads(path.read_text());data['learning_daily'][0]['completed_at']='2025-10-13T17:20:00+08:00';path.write_text(encode(data))
        self.assertEqual(len(build_schedule(self.families)['events']),15)

if __name__=='__main__':unittest.main()
