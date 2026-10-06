"""No model: frozen passive gates against historical active work and queued sends."""
import unittest
from digital_tutor.application import Application, Clock
from digital_tutor.jobs import JobQueue
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import dispatch, reconcile
from digital_tutor.scheduler import Scheduler, Worker
from digital_tutor.wechat import prepare_message
import test_stage1 as baseline


class PassiveHistoricalGateTests(baseline.CoreTests):
    def test_two_passive_days_old_active_work_never_starts_or_resends(self):
        # Historical source fixture, before the mode under test is enabled.
        events=self.create([self.goal('queued'),self.goal('unknown')])
        queued=prepare_message(self.app,self.service,self.send_event(events['queued']))['data']
        self.app.mock_send_outcome='unknown'
        uncertain=prepare_message(self.app,self.service,self.send_event(events['unknown']))['data']
        self.assertEqual(dispatch(self.app,'f',uncertain['id'])['stage'],'unknown')
        with self.app.store.transaction():
            for kind in ('daily','progress','step','review'):
                JobQueue(self.app).enqueue('f','historical-'+kind,{'kind':kind})
        original_count=channel_for(self.app).count()
        self.app.close()
        self.app=Application(self.path,Clock('2026-10-04T08:00:00+08:00'),mode='passive',strict_runtime=True)
        calls=[]
        worker=Worker(self.app,lambda job,tools:calls.append(job['kind']))
        for day in ('2026-10-04','2026-10-05'):
            for hour in ('08:00:00','20:59:59','21:00:00'):
                self.app.clock=Clock(day+'T'+hour+'+08:00')
                for _ in range(20):
                    self.assertEqual(Scheduler(self.app).tick()['created'],0)
                    self.assertFalse(worker.run_once()['ran'])
        rejected=dispatch(self.app,'f',queued['id'])
        self.assertEqual(rejected['stage'],'cancelled')
        self.assertIn(rejected['cancel_reason'],{'SEND_CAPABILITY_REQUIRED','PASSIVE_MODE'})
        self.assertEqual(reconcile(self.app,'f',uncertain['id'])['stage'],'sent')
        self.assertEqual(channel_for(self.app).count(),original_count)
        self.assertEqual(calls,[])
        self.assertFalse(self.app.store.list('run_outcome','f'))
        self.app.close()
        self.app=Application(self.path,Clock('2026-10-05T09:00:00+08:00'),mode='passive',strict_runtime=True)
        self.assertFalse(Worker(self.app,lambda j,t:calls.append(j)).run_once()['ran'])
        self.assertEqual(channel_for(self.app).count(),original_count)
        self.assertEqual(calls,[])


for name in baseline.CoreTests.__dict__:
    if name.startswith('test_'):
        setattr(PassiveHistoricalGateTests,name,None)
