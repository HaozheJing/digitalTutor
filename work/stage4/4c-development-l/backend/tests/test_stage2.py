"""Cross-process-style leases, durable outbox boundaries and scheduler behavior."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
import test_stage1 as baseline

from digital_tutor.application import Application, Clock
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue, set_enabled
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import dispatch, reconcile, InjectedCrash
from digital_tutor.scheduler import Scheduler, Worker
from digital_tutor.send_policy import ContactPolicy
from digital_tutor.wechat import prepare_message


class Stage2Tests(baseline.CoreTests):
    def setUp(self):
        super().setUp()
        self.app.store.connection.execute("DELETE FROM families WHERE id='other'")

    def test_unique_daily_and_no_historical_catchup(self):
        self.app.clock=Clock('2026-10-03T07:59:59+08:00')
        scheduler=Scheduler(self.app)
        self.assertEqual(scheduler.tick()['created'],0)
        self.app.clock=Clock('2026-10-03T08:00:00+08:00')
        self.assertEqual(scheduler.tick()['created'],1)
        self.assertEqual(scheduler.tick()['created'],0)
        self.app.clock=Clock('2026-10-08T08:00:00+08:00')
        self.assertEqual(scheduler.tick()['created'],1)
        claim=JobQueue(self.app).claim('w')
        self.assertEqual(claim['job']['business_date'],'2026-10-08')
        self.assertEqual(len(self.app.store.list('job','f')),2)

    def test_family_serial_claim_and_lease_fencing(self):
        msg=self.incoming(False); self.incoming(False)
        queue=JobQueue(self.app); first=queue.claim('w1',lease_seconds=1)
        token=queue.session(first)
        self.assertIsNone(queue.claim('w2'))
        self.app.clock.fixed+=timedelta(seconds=2)
        second=queue.claim('w2')
        self.assertEqual(first['job']['id'],second['job']['id'])
        self.assertNotEqual(first['lease']['token'],second['lease']['token'])
        self.call(token,'message.interpret',{'request_id':self.key(),'message_id':msg['id'],'expected_version':1,
                  'intents':[{'intent_id':'old','text':'旧运行'}]},'LEASE_LOST')
        with self.assertRaises(BusinessError):
            queue.finish(first)

    def test_reply_has_priority_and_does_not_call_planner(self):
        Scheduler(self.app).tick(); self.incoming(False)
        seen=[]
        def handler(job,tools):
            seen.append(job['kind'])
            return {'wait':True,'reason':'test inspection'}
        Worker(self.app,handler).run_once()
        self.assertEqual(seen,['service'])

    def test_idle_and_waiting_do_not_reinvoke_model(self):
        self.incoming(False); calls=[]
        worker=Worker(self.app,lambda job,tools:calls.append(job['id']) or {'wait':True})
        self.assertTrue(worker.run_once()['ran'])
        for _ in range(3):
            self.assertFalse(worker.run_once()['ran'])
        self.assertEqual(len(calls),1)
        self.incoming(False)
        self.assertTrue(worker.run_once()['ran'])

    def test_prepared_send_new_pause_cancels_without_transport(self):
        eid=self.create()['a']; m=self.incoming()
        prepared=prepare_message(self.app,self.service,self.send_event(eid))['data']
        self.pause(m)
        result=dispatch(self.app,'f',prepared['id'])
        self.assertEqual(result['stage'],'cancelled'); self.assertEqual(result['cancel_reason'],'CONTROL_ACTIVE')
        self.assertEqual(channel_for(self.app).count(),0)

    def test_cutoff_and_stop_cancel_unsent_but_allow_reconciliation(self):
        m=self.incoming(); prepared=prepare_message(self.app,self.service,self.reply(m))['data']
        self.app.clock=Clock('2026-10-03T21:00:00+08:00')
        result=dispatch(self.app,'f',prepared['id'])
        self.assertEqual(result['cancel_reason'],'WINDOW_CLOSED')
        self.app.clock=Clock('2026-10-04T09:00:00+08:00')
        second=self.incoming(); prepared=prepare_message(self.app,self.service,self.reply(second))['data']
        set_enabled(self.app,False)
        self.assertEqual(dispatch(self.app,'f',prepared['id'])['cancel_reason'],'RUNTIME_DISABLED')
        self.assertIsNone(JobQueue(self.app).claim('disabled'))

    def test_crash_after_external_effect_restarts_by_query_without_resend(self):
        m=self.incoming(); op=prepare_message(self.app,self.service,self.reply(m))['data']
        with self.assertRaises(InjectedCrash):
            dispatch(self.app,'f',op['id'],failpoint='after_transport')
        self.assertEqual(channel_for(self.app).count(),1)
        self.assertEqual(self.app.store.get('operation',op['id'],'f')['stage'],'dispatching')
        self.app.close(); self.app=Application(self.path,Clock('2026-10-03T22:00:00+08:00'))
        set_enabled(self.app,False)
        restored=reconcile(self.app,'f',op['id'])
        self.assertEqual(restored['stage'],'sent')
        self.assertEqual(channel_for(self.app).count(),1)
        self.assertEqual(len(self.app.state(self.service)['messages']),2)
        self.assertEqual(reconcile(self.app,'f',op['id'])['version'],restored['version'])

    def test_crash_before_external_effect_stays_unknown_not_resent(self):
        op=prepare_message(self.app,self.service,self.reply(self.incoming()))['data']
        with self.assertRaises(InjectedCrash):
            dispatch(self.app,'f',op['id'],failpoint='before_transport')
        restored=reconcile(self.app,'f',op['id'])
        self.assertEqual(restored['stage'],'unknown'); self.assertTrue(restored['reconcile_required'])
        self.assertEqual(channel_for(self.app).count(),0)
        self.assertEqual(dispatch(self.app,'f',op['id'])['stage'],'unknown')

    def test_unknown_and_queued_reserve_contact_quota(self):
        self.app.contact_policy=ContactPolicy(proactive_per_day=1)
        ids=self.create([self.goal('a'),self.goal('b')])
        self.app.mock_send_outcome='unknown'
        self.call(self.service,'wechat.send_message',self.send_event(ids['a']))
        self.call(self.service,'wechat.send_message',self.send_event(ids['b']),'CONTACT_LIMIT')
        self.call(self.service,'wechat.send_message',self.reply(self.incoming()))

    def test_review_is_internal_and_cannot_send(self):
        eid=self.create()['a']; msg=self.incoming()
        with self.app.store.transaction():
            for job in self.app.store.list('job','f'):
                job['status']='succeeded'; self.app.store.put('job',job['id'],'f',job,job['version'])
            JobQueue(self.app).enqueue('f','review-test',{'kind':'review','event_id':eid})
        queue=JobQueue(self.app); claim=queue.claim('review'); token=queue.session(claim)
        self.call(token,'wechat.send_message',self.reply(msg),'NO_CONTACT_RUN')

    def test_worker_cannot_finish_unanswered_message(self):
        self.incoming(False)
        self.assertEqual(Worker(self.app,lambda job,tools:{}).run_once()['outcome'],'waiting')
        self.assertEqual(self.app.store.list('job','f')[0]['status'],'waiting')

    def test_additive_migration_retains_existing_records(self):
        msg=self.incoming()
        self.app.store.connection.execute('PRAGMA user_version=1')
        self.app.close(); self.app=Application(self.path)
        self.assertEqual(self.app.store.connection.execute('PRAGMA user_version').fetchone()[0],2)
        self.assertEqual(self.app.store.get('message',msg['id'],'f')['body'],msg['body'])


for name in baseline.CoreTests.__dict__:
    if name.startswith('test_'):
        setattr(Stage2Tests,name,None)
