import datetime as dt
import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_message_hooks as base
import test_service_workflow as workflow_base
import sql_tools as s,test_mode as tm,live_runtime as live,wechat_test as w,message_hooks as h,service_hours as hours
import agent_plan as ap,service_workflow as wf

def at(value):
    state=tm.state();state['as_of']=value
    with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))

class ServiceHoursTests(unittest.TestCase):
    setUp=base.MessageHookTests.setUp
    tearDown=base.MessageHookTests.tearDown
    create_source=base.MessageHookTests.create_source
    set_context=base.MessageHookTests.set_context
    inbound=base.MessageHookTests.inbound
    queue=base.MessageHookTests.queue

    def test_exact_boundaries_and_shanghai_timezone(self):
        for stamp,expected in [('2025-10-24T06:29:59+08:00',False),('2025-10-24T06:30:00+08:00',True),('2025-10-24T21:59:59+08:00',True),('2025-10-24T22:00:00+08:00',False),('2025-10-24T14:00:00Z',False),('2025-10-25T00:20:00+08:00',False)]:
            with self.subTest(stamp=stamp):self.assertEqual(hours.is_open(stamp),expected)

    def test_night_inbound_survives_restart_without_attempt_then_batches_at_0630(self):
        at('2025-10-24T22:01:00+08:00');self.inbound('night-1');self.clock+=61
        self.assertIsNone(live.claim());self.assertEqual(s.read("SELECT status,attempts FROM events")[0],{'status':'waiting_service_hours','attempts':0})
        live.recover();self.inbound('night-2','另一个问题');self.clock+=61
        at('2025-10-25T06:29:59+08:00');self.assertIsNone(live.claim())
        at('2025-10-25T06:30:00+08:00');event=live.claim()
        self.assertEqual(event['kind'],'passive');self.assertEqual(json.loads(event['payload'])['source_message_ids'],['night-1','night-2'])
        self.assertEqual(len(s.read("SELECT * FROM events WHERE status='dispatched'")),1)
        self.assertFalse(s.read('SELECT * FROM deliveries'));self.assertFalse(s.read('SELECT * FROM test_outbox'))

    def test_night_interruption_does_not_consume_the_last_morning_attempt(self):
        at('2025-10-24T21:59:00+08:00');self.inbound();self.clock+=61;event=live.claim()
        at('2025-10-24T22:00:00+08:00')
        for recover in (lambda:live.retry(event['id']),live.recover):
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute("UPDATE events SET status='dispatched',attempts=3 WHERE id=?",(event['id'],))
            recover()
            self.assertEqual(s.read('SELECT status,attempts FROM events WHERE id=?',(event['id'],))[0],{'status':'waiting_service_hours','attempts':2})
        at('2025-10-25T06:30:00+08:00');fresh=live.claim()
        self.assertEqual(fresh['kind'],'passive');self.assertEqual(s.read('SELECT status,attempts FROM events WHERE id=?',(fresh['id'],))[0],{'status':'dispatched','attempts':3})
        self.assertFalse(s.read('SELECT * FROM test_outbox'))

    def test_model_crosses_cutoff_before_reply_no_outbox_and_no_false_finish(self):
        at('2025-10-24T21:59:00+08:00');self.inbound();self.clock+=61;event=live.claim();token=h.begin('event',event['id'])['token']
        at('2025-10-24T22:00:00+08:00')
        with h.authority(token):self.assertEqual(s.mock_reply(event['id'],'夜间旧正文')['status'],'deferred')
        self.assertEqual(s.finish_event(event['id'],'2025-10-24')['status'],'deferred')
        import business
        business.initialize()
        self.assertEqual(business.finish(business.token(event['id']),'2025-10-24')['status'],'deferred')
        self.assertFalse(s.read('SELECT * FROM deliveries'));self.assertFalse(s.read('SELECT * FROM test_outbox'))
        self.assertEqual(s.read('SELECT status FROM events WHERE id=?',(event['id'],))[0]['status'],'waiting_service_hours')

    def test_queued_unsent_reply_is_rebuilt_next_morning_not_replayed(self):
        at('2025-10-24T21:59:00+08:00');self.inbound();self.clock+=61;event=live.claim();token=h.begin('event',event['id'])['token'];key='reply:'+event['id']
        with h.authority(token),closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,'昨天旧正文','queued',s.now(),'test_wechat_pending'));w.queue_text(c,key,'昨天旧正文')
        at('2025-10-24T22:00:00+08:00')
        with patch.object(w,'http_send',side_effect=AssertionError('no night send')):w.flush()
        self.assertEqual(s.read('SELECT status FROM deliveries WHERE action_key=?',(key,))[0]['status'],'superseded')
        self.assertEqual(len(s.read("SELECT * FROM events WHERE status='waiting_service_hours'")),1)
        live.recover();at('2025-10-25T06:30:00+08:00');fresh=live.claim();self.assertNotEqual(fresh['id'],event['id'])
        token=h.begin('event',fresh['id'])['token']
        with h.authority(token),patch.object(w,'http_send',return_value={'errcode':0,'requestId':'fresh-morning'}) as send:
            result=s.mock_reply(fresh['id'],'早上重新读取后回复')
        self.assertEqual(result['status'],'accepted');self.assertEqual(send.call_count,1);self.assertEqual(send.call_args.args[3],'早上重新读取后回复')
        self.assertEqual(s.read('SELECT status FROM deliveries WHERE action_key=?',(key,))[0]['status'],'superseded')

    def test_cutoff_between_parts_preserves_first_receipt_without_replay(self):
        at('2025-10-24T21:59:59+08:00');self.queue(body='先说第一段。\n再说第二段。')
        def send(*args):
            at('2025-10-24T22:00:00+08:00');return {'errcode':0,'requestId':'first-part'}
        with patch.object(w,'http_send',side_effect=send) as transport:w.flush();self.assertEqual(transport.call_count,1)
        self.assertEqual(len(s.read('SELECT * FROM test_outbox')),1);self.assertEqual(s.read('SELECT status FROM test_outbox')[0]['status'],'accepted')
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'blocked')
        at('2025-10-25T06:30:00+08:00')
        with patch.object(w,'http_send',side_effect=AssertionError('no replay')):w.flush()

    def test_real_clock_guard_does_not_use_stale_turn_business_time(self):
        state=tm.state();state.update(frozen=False,as_of='2025-10-24T21:55:00+08:00')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2025,10,24,22,1,tzinfo=hours.TZ)
        with patch.object(hours.dt,'datetime',Clock):self.assertFalse(hours.is_open())

    def test_late_completion_window_is_expired_not_shifted(self):
        item={'rule':'welcome-back-to-reading','status':'eligible','time_window':{'earliest':'2025-10-25T00:20:00+08:00','latest':'2025-10-25T23:59:59+08:00'}}
        hours.limit_candidates([item]);self.assertEqual(item['status'],'expired');self.assertEqual(item['time_window']['earliest'],'2025-10-25T00:20:00+08:00')

class ProactiveHoursTests(unittest.TestCase):
    setUp=workflow_base.WorkflowTests.setUp
    tearDown=workflow_base.WorkflowTests.tearDown
    create_source=workflow_base.WorkflowTests.create_source
    set_context=workflow_base.WorkflowTests.set_context
    begin_workflow=workflow_base.WorkflowTests.begin_workflow
    plan=workflow_base.WorkflowTests.plan
    stop_plan=workflow_base.WorkflowTests.stop_plan
    new_task=workflow_base.WorkflowTests.new_task
    start_action=workflow_base.WorkflowTests.start_action

    def test_simulated_highlight_contact_retry_cannot_cross_closing_time(self):
        import proactive
        _,ctx=self.begin_workflow('2025-10-28 21:30')
        item=next(x for x in ctx['catalog'] if x['kind']=='highlight' and x['skill']=='celebrate-important-day')['opportunities'][0]
        self.assertEqual(item['time_window']['latest'],'2025-10-28T21:59:59+08:00')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('prior-contact','prior','sent','2025-10-28T21:15:00+08:00','test_wechat'))
            result=proactive.contact_gate(c,[item],hours.stamp(ctx['business_time']))
        self.assertEqual(result['status'],'skipped')
        self.assertFalse(s.read('SELECT * FROM proactive_pending'))

    def test_running_unsent_task_expires_at_22_and_never_resumes_next_day(self):
        tid=self.new_task();self.start_action(tid);at('2025-10-13T22:00:00+08:00');hours.tick()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(tid,))[0]['status'],'expired')
        self.assertEqual(s.read('SELECT expires_at FROM service_tasks WHERE id=?',(tid,))[0]['expires_at'],'2025-10-13T21:59:59+08:00')
        self.assertIsNone(ap.active());self.assertIsNone(ap.claim());self.assertEqual(ap.settle(tid)['status'],'already_finished')
        wf.recover();at('2025-10-14T06:30:00+08:00');hours.tick()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(tid,))[0]['status'],'expired')
        self.assertFalse(s.read('SELECT * FROM deliveries'))

if __name__=='__main__':unittest.main()
