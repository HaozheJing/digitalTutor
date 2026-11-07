import hashlib
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as fixtures
import sql_tools as s
import live_runtime as live
import message_hooks as h
import wechat_test as w
import business
import test_mode as tm


class MessageHookTests(unittest.TestCase):
    create_source = fixtures.FixtureIntegrationTests.create_source
    set_context = fixtures.FixtureIntegrationTests.set_context
    tearDown = fixtures.FixtureIntegrationTests.tearDown

    def setUp(self):
        fixtures.FixtureIntegrationTests.setUp(self)
        self.clock = 1000.
        for target, value in [(h,'enabled'), (h.time,'time')]:
            p = patch.object(target,value,side_effect=(lambda: True) if value=='enabled' else (lambda: self.clock))
            p.start(); self.addCleanup(p.stop)

    def inbound(self, ident='one', body='你好'):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,1)',(ident,'user',body,s.now(),'test','hash'))
            live.enqueue(c,'inbound:'+ident,'passive',{'source_message_id':ident})

    def queue(self, key='draft', body='你好', owner='event'):
        self.token = h.begin(owner,'owner')['token']
        with h.authority(self.token),closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,body,'queued',s.now(),'test_wechat_pending'))
            w.queue_text(c,key,body)
        return self.token

    def test_real_wall_debounce_last_unique_and_batch(self):
        business_time=tm.state()['as_of']
        self.inbound();self.clock+=40;self.inbound('two','还有一个问题')
        self.clock+=30;self.inbound('two','duplicate')
        self.assertIsNone(live.claim())
        self.clock=1100
        event=live.claim();self.assertEqual(json.loads(event['payload'])['source_message_ids'],['one','two'])
        self.assertEqual(tm.state()['as_of'],business_time)
        self.assertEqual(len(s.read("SELECT * FROM events WHERE status='dispatched'")),1)
        self.assertIn('还有一个问题',business.task(event['id'])['prompt'])

    def test_restart_preserves_deadline_and_batch_identity(self):
        self.inbound();self.clock+=59;live.recover();self.assertIsNone(live.claim())
        self.clock+=1;first=live.claim();live.recover();again=live.claim()
        self.assertEqual(first['id'],again['id'])
        self.assertEqual(len(s.read('SELECT * FROM message_hook_inbound')),1)

    def test_verified_callback_arrival_not_business_or_poll_time(self):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO messages VALUES('late','user','你好','old','test','hash',1)")
            live.enqueue(c,'inbound:late','passive',{'source_message_id':'late'},received_at='1970-01-01T00:16:10+00:00')
        self.clock=1029;self.assertIsNone(live.claim())
        self.clock=1030;self.assertIsNotNone(live.claim())

    def test_new_message_invalidates_generation_even_after_quiet(self):
        token=self.queue();self.inbound();self.clock+=60
        with self.assertRaises(h.StaleGeneration),h.authority(token):pass
        with patch.object(w,'http_send') as http:
            self.assertEqual(w.flush()[0]['status'],'replan_required');http.assert_not_called()
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'superseded')

    def test_no_token_fail_closed_and_queue_rollback(self):
        with self.assertRaises(h.StaleGeneration),closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO deliveries VALUES('missing','body','queued','at','test_wechat_pending')")
            w.queue_text(c,'missing','body')
        self.assertFalse(s.read("SELECT * FROM deliveries WHERE action_key='missing'"))

    def test_unpolled_verified_callback_checked_before_http(self):
        self.queue()
        p={**self.cfg['route'],'messageId':'fresh','messageType':7,'isSelf':False,'payload':{'text':'请等一下'}}
        with closing(sqlite3.connect(self.provider['database'])) as c,c:
            c.execute('INSERT INTO callbacks VALUES(?,?,?,?,?)',(1,'message',json.dumps(p),s.now(),'received_token_verified'))
        with patch.object(w,'http_send') as http:
            self.assertEqual(w.flush()[0]['status'],'replan_required');http.assert_not_called()
        self.assertTrue(s.read("SELECT * FROM messages WHERE source_id='wechat:fresh'"))

    def test_nontext_invalidates_without_inventing_image_content(self):
        token=self.queue()
        p={**self.cfg['route'],'messageId':'image','messageType':6,'isSelf':False,'payload':{'url':'https://invalid.example/image.png'}}
        with closing(sqlite3.connect(self.provider['database'])) as c,c:
            c.execute('INSERT INTO callbacks VALUES(?,?,?,?,?)',(1,'message',json.dumps(p),s.now(),'received_token_verified'))
        with patch.object(w,'http_send') as http:
            self.assertEqual(w.flush()[0]['status'],'replan_required');http.assert_not_called()
        self.assertIn('内容尚未解析',s.read("SELECT body FROM messages WHERE source_id='wechat:image'")[0]['body'])

    def test_picture_callback_registers_original_asset_without_network_and_deduplicates(self):
        import image_inputs
        self.queue()
        p={**self.cfg['route'],'messageId':'picture','messageType':6,'isSelf':False,
           'payload':{'url':'https://area-k.tos-cn-beijing.volces.com/original','text':'invented OCR'}}
        with closing(sqlite3.connect(self.provider['database'])) as c,c:
            c.execute('INSERT INTO callbacks VALUES(?,?,?,?,?)',(1,'message',json.dumps(p),s.now(),'received_token_verified'))
        with patch.object(image_inputs,'fetch',side_effect=AssertionError('no image network inside callback transaction')):
            w.receive();w.receive()
        rows=s.read('SELECT id,source_id,locator,result FROM image_inputs')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source_id'],'wechat:picture');self.assertIsNone(rows[0]['result'])
        body=s.read("SELECT body FROM messages WHERE source_id='wechat:picture'")[0]['body']
        self.assertIn(rows[0]['id'],body);self.assertNotIn('invented OCR',body)

    def test_voice_arrival_interrupts_old_reply_before_audio_network(self):
        import voice_understanding as voice
        self.queue()
        p={**self.cfg['route'],'messageId':'voice','messageType':2,'isSelf':False,
           'payload':{'voiceUrl':'https://area-k.tos-cn-beijing.volces.com/audio.mp3','text':'invented platform transcript'}}
        with closing(sqlite3.connect(self.provider['database'])) as c,c:
            c.execute('INSERT INTO callbacks VALUES(?,?,?,?,?)',(1,'message',json.dumps(p),s.now(),'received_token_verified'))
        with patch.object(w,'http_send') as http,patch.object(voice,'fetch_audio') as fetch:
            self.assertEqual(w.flush()[0]['status'],'replan_required');http.assert_not_called();fetch.assert_not_called()
        message=s.read("SELECT body FROM messages WHERE source_id='wechat:voice'")[0]['body']
        self.assertIn('understand_audio',message);self.assertNotIn('invented',message)
        self.assertEqual(len(s.read('SELECT * FROM voice_inputs')),1)
        self.clock+=60;event=live.claim()
        with self.assertRaises(__import__('agent_plan').ServiceValidationError):voice.guard_reply(event['id'])
        w.receive();self.assertEqual(len(s.read('SELECT * FROM voice_inputs')),1)

    def test_preflight_retains_control_and_reads_messages_behind_it(self):
        self.queue()
        with closing(sqlite3.connect(self.provider['database'])) as c,c:
            for i,text in [(1,'测试状态'),(2,'等等还有一句')]:
                p={**self.cfg['route'],'messageId':str(i),'messageType':7,'isSelf':False,'payload':{'text':text}}
                c.execute('INSERT INTO callbacks VALUES(?,?,?,?,?)',(i,'message',json.dumps(p),s.now(),'received_token_verified'))
        with patch.object(w,'http_send') as http:
            self.assertEqual(w.flush()[0]['status'],'replan_required');http.assert_not_called()
        self.assertEqual(w.receive()['text'],'测试状态')
        self.assertTrue(s.read("SELECT 1 FROM messages WHERE source_id='wechat:2'"))

    def test_restart_after_batch_accepted_before_finish_does_not_send_twice(self):
        self.inbound();self.clock+=60;event=live.claim()
        token=h.begin('event',event['id'])['token']
        with h.authority(token),patch.object(w,'http_send',return_value={'errcode':0,'requestId':'once'}) as http:
            s.mock_reply(event['id'],'收到')
            live.recover();again=live.claim()
            self.assertIsNone(again)
            s.mock_reply(event['id'],'收到')
            self.assertEqual(http.call_count,1)
            self.assertEqual(s.read('SELECT status FROM events WHERE id=?',(event['id'],))[0]['status'],'awaiting_delivery')
        row=s.read('SELECT * FROM test_outbox')[0]
        with closing(sqlite3.connect(s.DB)) as c,c:w.observe(c,{'externalRequestId':row['id'],'sendCode':0,'messageId':'real-final'})
        self.assertEqual(s.read('SELECT status FROM events WHERE id=?',(event['id'],))[0]['status'],'done')

    def test_missing_last_mile_authority_stops_unregistered_delivery(self):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO deliveries VALUES('unregistered','old draft','queued','at','test_wechat_pending')")
        with patch.object(w,'http_send') as http:
            self.assertEqual(w.flush()[0]['status'],'replan_required');http.assert_not_called()

    def test_partial_stops_remaining_and_preserves_late_receipt(self):
        self.queue(body=('先说第一段。'*16)+'\n\n'+('再说第二段。'*16))
        def send(*args):
            self.inbound('collision');return {'errcode':0,'requestId':'accepted-one'}
        with patch.object(w,'http_send',side_effect=send) as http:
            self.assertEqual(w.flush()[0]['status'],'replan_required');self.assertEqual(http.call_count,1)
            self.assertEqual(w.flush(),[])
        row=s.read('SELECT * FROM test_outbox')[0]
        with closing(sqlite3.connect(s.DB)) as c,c:w.observe(c,{'externalRequestId':row['id'],'sendCode':0,'messageId':'true-final'})
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'partial')
        self.clock+=60
        self.assertIn('先说第一段',h.begin('event','new')['prompt'])
        self.assertIn('sent',h.begin('event','newer')['prompt'])

    def test_unknown_reservation_never_reissued_after_restart(self):
        self.queue()
        with patch.object(w,'http_send',side_effect=TimeoutError) as http:
            self.assertEqual(w.flush()[0]['status'],'unknown')
            live.recover()
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET source='test_wechat_pending'")
            self.assertEqual(w.flush()[0]['status'],'unknown');self.assertEqual(http.call_count,1)

    def test_unknown_reply_not_replayed_inside_a_new_message_batch(self):
        self.inbound();self.clock+=60;event=live.claim()
        token=h.begin('event',event['id'])['token']
        with h.authority(token),patch.object(w,'http_send',side_effect=TimeoutError):s.mock_reply(event['id'],'原回复')
        self.assertIsNone(live.claim())
        self.inbound('new-question');self.clock+=60;fresh=live.claim()
        self.assertEqual(json.loads(fresh['payload'])['source_message_ids'],['new-question'])
        prompt=h.begin('event',fresh['id'])['prompt']
        self.assertIn('原回复',prompt);self.assertIn('unknown',prompt)

    def test_reservation_and_part_link_exist_before_network_and_early_receipt_wins(self):
        self.queue()
        def send(cfg,route,ident,body):
            with closing(sqlite3.connect(s.DB)) as c,c:
                self.assertTrue(c.execute('SELECT 1 FROM test_outbox_parts WHERE operation_id=?',(ident,)).fetchone())
                w.observe(c,{'externalRequestId':ident,'sendCode':0,'messageId':'before-http-return'})
            return {'errcode':0,'requestId':'accepted-after-final'}
        with patch.object(w,'http_send',side_effect=send):self.assertEqual(w.flush()[0]['status'],'sent')

    def test_full_delivery_waits_for_final_receipt(self):
        self.queue()
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'ok'}):self.assertEqual(w.flush()[0]['status'],'accepted')
        row=s.read('SELECT * FROM test_outbox')[0]
        with closing(sqlite3.connect(s.DB)) as c,c:w.observe(c,{'externalRequestId':row['id'],'sendCode':0,'messageId':'final'})
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'sent')

    def test_crash_between_http_reservation_and_part_link_not_retired(self):
        token=self.queue()
        ident='dsh-'+hashlib.sha256((tm.state()['run_id']+':draft').encode()).hexdigest()[:40]
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO test_outbox VALUES(?,?,?,?,?,?,?,?)',(ident,'你好','unknown',None,None,s.now(),s.now(),7))
        self.inbound();h.interrupt(token,'draft')
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'blocked')

    def test_finished_batch_excludes_handled_inputs(self):
        self.inbound();self.clock+=60;event=live.claim()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE events SET status='done' WHERE id=?",(event['id'],));h.handled(c,event['id'])
        self.inbound('new');self.clock+=60
        self.assertEqual(json.loads(live.claim()['payload'])['source_message_ids'],['new'])

    def test_enable_preserves_historical_completed_event_and_timestamp(self):
        with closing(sqlite3.connect(s.DB)) as c,c:
            live.enqueue(c,'history','passive',{'source_message_id':'historical'})
            c.execute("UPDATE events SET status='done',completed_at='original-time' WHERE id='history'")
            c.execute("INSERT INTO deliveries VALUES('reply:history','old','sent','old','test_wechat')")
            h.delivery_status(c,'reply:history','accepted')
        live.claim()
        row=s.read("SELECT status,completed_at FROM events WHERE id='history'")[0]
        self.assertEqual(row,{'status':'done','completed_at':'original-time'})

    def test_operator_resolution_does_not_reopen_in_next_batch(self):
        import operator_alerts as alerts
        self.inbound();self.clock+=60;event=live.claim()
        alert=alerts.request_alert('需人工核对',event_id=event['id'])['alert']
        with patch.object(alerts,'_writable'):
            alerts.dismiss(tm.state()['run_id'],alert['id'],alert['revision'],'已人工处理，无需回复')
        self.inbound('later');self.clock+=60
        self.assertEqual(json.loads(live.claim()['payload'])['source_message_ids'],['later'])

    def task(self):
        import agent_plan as ap
        ap.initialize()
        task=ap.insert_task('s01','提醒','remind-reading',tm.state()['as_of'],'test',ident='owner')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='running',attempts=1 WHERE id='owner'")
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task','\"owner\"')")
        return task

    def test_stale_proactive_keeps_original_task_and_retires_unsent_draft(self):
        self.task();token=self.queue(owner='task')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("INSERT INTO service_links VALUES('owner','draft')")
        self.inbound()
        with patch.object(w,'http_send') as http:w.flush();http.assert_not_called()
        self.assertEqual(s.read("SELECT status FROM service_tasks WHERE id='owner'")[0]['status'],'deferred')
        self.assertEqual(len(s.read('SELECT * FROM service_tasks')),1)
        self.assertEqual(len(s.read('SELECT * FROM service_links')),1)
        self.clock+=60;fresh=h.begin('task','owner')['token']
        with h.authority(fresh),closing(sqlite3.connect(s.DB)) as c,c:
            key=h.draft_key(c,'draft');self.assertNotEqual(key,'draft')
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,'新内容','unknown',s.now(),'test_wechat'))
        newer=h.begin('task','owner')['token']
        with h.authority(newer),closing(sqlite3.connect(s.DB)) as c:
            self.assertEqual(h.draft_key(c,'draft'),key,'Unknown original may never be replaced by a new send')

    def test_passive_fulfillment_interruption_releases_only_never_issued_task(self):
        self.task();self.queue(owner='event')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='awaiting_delivery' WHERE id='owner'")
            c.execute("INSERT INTO service_links VALUES('owner','draft')")
        self.inbound()
        with patch.object(w,'http_send') as http:w.flush();http.assert_not_called()
        self.assertEqual(s.read("SELECT status FROM service_tasks WHERE id='owner'")[0]['status'],'deferred')

    def test_mcp_returns_controlled_replan_receipt_for_stale_token(self):
        import mcp_server
        token=h.begin('event','old')['token'];self.inbound()
        result=mcp_server.business_call('reply_to_message',{'event_id':'old','body':'stale','generation_token':token})
        self.assertEqual(result['status'],'replan_required')
        self.assertFalse(s.read('SELECT * FROM test_outbox'))


if __name__=='__main__':unittest.main()
