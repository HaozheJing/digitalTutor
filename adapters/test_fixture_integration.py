import datetime as dt
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import test_live as fixture
import test_mode as tm
import fixture_query as fq
import sql_tools as s
import proactive as p
import business as b
import wechat_test as w
import io


def confirm_outbox():
    for row in s.read('SELECT id,request_id FROM test_outbox'):
        with closing(sqlite3.connect(s.DB)) as c,c:
            w.observe(c,{'externalRequestId':row['id'],'sendCode':0,'messageId':'confirmed-'+row['id']})


class FixtureIntegrationTests(unittest.TestCase):
    create_source=fixture.LiveTests.create_source
    set_context=fixture.LiveTests.set_context
    tearDown=fixture.LiveTests.tearDown
    def setUp(self):
        fixture.LiveTests.setUp(self);b.initialize();w.initialize()
        file=Path(__file__).resolve().parents[1]/'runtime/fixtures/mock_uid_00001_first_month.json'
        if not file.exists():
            self.skipTest('Private first-month fixture not installed')
        # Each test owns a writable copy; mutation must never poison later cases.
        owned=self.root/'fixtures'/file.name;owned.parent.mkdir();owned.write_bytes(file.read_bytes())
        self.patch=patch.object(fq,'FILE',owned);self.patch.start();self.addCleanup(self.patch.stop)
        self.cfg={'route':{'orgId':'o','imBotId':'b','chatId':'c','imContactId':'i'},
                  'provider_config':str(self.root/'provider.json'),'after_callback_id':0}
        self.provider={'allowed_routes':[self.cfg['route']],'expected_org_id':'o',
                       'database':str(self.root/'callbacks.sqlite')}
        (self.root/'provider.json').write_text(json.dumps(self.provider))
        with closing(sqlite3.connect(self.provider['database'])) as c,c:
            c.execute('CREATE TABLE callbacks(id INTEGER PRIMARY KEY,kind TEXT,body_json TEXT,first_received_at TEXT,status TEXT)')
        self.configpatch=patch.object(w,'config',lambda:self.cfg);self.configpatch.start();self.addCleanup(self.configpatch.stop)
        tm.activate('2025-10-24 20:30')

    def test_workbench_forecast_does_not_change_clock_or_leak_into_learning(self):
        import workbench
        before=tm.state()
        value=workbench.view()
        self.assertEqual(value['summary']['completed'],9)  # Reviewed fixture: lessons 1-9 complete, lesson 10 still pending.
        self.assertEqual(len(value['calendar']),35)
        self.assertEqual(len(value['scenarios']),13)
        self.assertEqual(value['highlights'],[])
        self.assertEqual(tm.state(),before)
        self.assertNotIn('scenarios',tm.snapshot())

    def test_learning_overview_uses_only_visible_completion(self):
        import workbench
        for at,expected in [('2025-10-13 06:30','not_started'),('2025-10-13 20:30','completed'),
                            ('2025-10-19 20:30','no_lesson'),('2025-10-24 20:30','in_progress'),('2025-10-25 00:00','no_lesson')]:
            with self.subTest(at=at):
                snap=fq.query(at.replace(' ','T')+':00+08:00')
                value=workbench.learning_overview(snap)
                self.assertEqual(value['today']['status'],expected)
                self.assertTrue(all(r['scheduled_date']<=at[:10] for r in value['overdue_lessons']))
                self.assertEqual(value['profile']['uid'],'00001')
        snap=fq.query('2025-10-13T06:30:00+08:00');snap['lessons_as_of']=[]
        self.assertEqual(workbench.learning_overview(snap)['today']['status'],'unknown')

    def test_workbench_shared_highlight_sender_and_receipts(self):
        import workbench
        tm.activate('2025-11-07')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'probe'}) as send:
            result=w.trigger_highlights()
            self.assertEqual(result['status'],'accepted')
            self.assertEqual(send.call_count,2)  # Current fixture: one caption and one original image.
            asset=next(h for h in tm.fixture_data()['highlight_assets'] if h['id']=='highlight-00001-02')
            self.assertEqual([x.args[3] for x in send.call_args_list],[asset['share_txt'],asset['img_url']])
            self.assertTrue(all(h['already_triggered'] for h in workbench.view()['highlights']))
            self.assertEqual(w.trigger_highlights()['status'],'skipped')
            self.assertEqual(send.call_count,2)

    def test_automatic_highlights_due_once_and_manual_mode(self):
        import test_scheduler as scheduler
        tm.activate('2025-10-13 18:59',True)
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'r'}) as http:
            scheduler.tick()
            http.assert_not_called()
            self.assertEqual(scheduler.plan()['items'][-1]['status'],'not_due')
            tm.activate('2025-10-13 19:00',True)
            self.assertEqual(scheduler.tick()['status'],'accepted')
            self.assertEqual(http.call_count,2)
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute('UPDATE test_checks SET wall_at=0')
            scheduler.tick()
            self.assertEqual(http.call_count,2)
            self.assertEqual(scheduler.plan()['items'][-1]['status'],'accepted')
            before=tm.state()
            with self.assertRaises(ValueError):tm.activate('2025-10-13 19:00',False)
            self.assertEqual(tm.state(),before)
            confirm_outbox()
            tm.activate('2025-10-13 19:00',False)
            self.assertEqual(scheduler.tick()['status'],'manual')
            self.assertEqual(http.call_count,2)

    def test_automatic_quiet_window_uses_duration_not_wall_year(self):
        import test_scheduler as scheduler
        import time
        tm.activate('2025-10-13 20:30',True)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO task_state VALUES(?,?,?)',
                      ('chat-gate','quiet',json.dumps({'active':False,'until':time.time()+120})))
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'r'}) as http:
            result=scheduler.tick()
            self.assertEqual(result['status'],'deferred')
            self.assertTrue(result['retry_at'].startswith('2025-10-13'))
            http.assert_not_called()
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute('UPDATE task_state SET payload=? WHERE id=?',(json.dumps({'active':False,'until':0}),'chat-gate'))
                c.execute('UPDATE test_checks SET wall_at=0')
            self.assertEqual(scheduler.tick()['status'],'accepted')
            self.assertEqual(http.call_count,2)

    def test_automatic_unknown_does_not_retry_and_pause_is_respected(self):
        import test_scheduler as scheduler
        tm.activate('2025-10-13 20:30',True)
        with patch.object(w,'http_send',side_effect=TimeoutError) as http:
            self.assertEqual(scheduler.tick()['status'],'unknown')
            self.assertEqual(http.call_count,1)
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute('UPDATE test_checks SET wall_at=0')
            scheduler.tick()
            self.assertEqual(http.call_count,1)
            self.assertEqual(scheduler.plan()['items'][-1]['status'],'unknown')
        before=tm.state()
        with self.assertRaises(ValueError):tm.activate('2025-10-13 20:30',True)
        self.assertEqual(tm.state(),before)
        confirm_outbox()  # A late successful receipt resolves the original uncertain operation.
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'partial')
        with self.assertRaises(ValueError):tm.activate('2025-10-15 20:30',True)

    def test_automatic_pause_is_respected_in_fresh_run(self):
        import test_scheduler as scheduler
        tm.activate('2025-10-15 20:30',True)
        scheduler.initialize()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('UPDATE test_checks SET wall_at=0')
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-15','m','不要联系',s.now()))
        with patch.object(w,'http_send') as http:
            self.assertEqual(scheduler.tick()['status'],'blocked')
            http.assert_not_called()

    def test_observation_actual_results_redaction_and_history_isolation(self):
        import observability as audit
        import workbench
        old=tm.state()
        audit.invoke('learning_query',{'day':'2025-10-24'},
                     lambda:{'completed':6,'api_key':'secret','url':'https://example.invalid?token=hidden'})
        with self.assertRaises(ValueError):
            audit.invoke('bad_query',{},lambda:(_ for _ in ()).throw(ValueError('secret')))
        rows=audit.records(old['run_id'])
        self.assertEqual(rows[0]['status'],'error')
        self.assertEqual(rows[1]['output']['completed'],6)
        self.assertNotIn('secret',json.dumps(rows))
        self.assertNotIn('token=hidden',json.dumps(rows))
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)',('one','user','侨侨还欠哪几课？',s.now(),'test','hash'))
        new=tm.activate('2025-10-13',False)
        self.assertEqual(workbench.inspection()['chat'],[])
        self.assertEqual(workbench.inspection()['tools'],[])
        past=workbench.archived(Path(new['archive']).stem)
        self.assertEqual(past['chat'][0]['body'],'侨侨还欠哪几课？')
        self.assertEqual(len(past['tools']),2)
        # Old archives made before native image support have no parts table.
        with closing(sqlite3.connect(new['archive'])) as c,c:
            c.execute('DROP TABLE test_outbox_parts')
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('old-reply','旧版回复','sent',s.now(),'test'))
        self.assertEqual(len(workbench.archived(Path(new['archive']).stem)['chat']),2)
        self.assertEqual(tm.state()['run_id'],new['run_id'])
        with self.assertRaises(ValueError):workbench.archived('../live')

    def test_plan_previews_do_not_advance_or_expose_future_outcomes_to_model(self):
        import test_scheduler as scheduler
        tm.activate('2025-10-13 16:00',False)
        before=tm.state()
        preview=scheduler.plan('2025-11-07')
        self.assertEqual(len([x for x in preview['items'] if x['kind']=='highlight']),1)
        self.assertEqual([x['highlight_id'] for x in preview['items'] if x['kind']=='highlight'],['highlight-00001-02'])
        self.assertTrue(all(x['evidence']=='到点后读取事实' for x in preview['items'] if x['kind']=='highlight'))
        self.assertEqual(tm.state(),before)
        self.assertNotIn('第一本书',json.dumps(tm.snapshot()['available_highlights'],ensure_ascii=False))

    def test_historical_projection_and_no_future_quiz(self):
        x=b.context('2025-10-24')
        self.assertEqual(x['learning']['summary_as_of']['completed'],9)
        self.assertEqual([r['lesson_id'] for r in x['learning']['lessons_as_of'] if r['status']=='completed'],['L2-%03d'%i for i in range(1,10)])
        self.assertEqual(x['identity']['child_nickname'],'侨侨')
        self.assertEqual(s.check('2025-10-24')['status'],'not_eligible')  # Legacy overdue check has no intraday deadline.
        import agent_plan
        agent_plan.enable()
        self.assertEqual(s.check('2025-10-24')['status'],'eligible')  # Current S01 uses incomplete current-day work.
        self.assertEqual(s.check('2025-10-25')['status'],'unknown')
        self.assertTrue(all(q['submitted_at']<='2025-10-24T20:30:00+08:00' for q in x['learning']['quiz_attempts']))
        tm.activate('2025-11-16')
        self.assertEqual(tm.snapshot()['summary_as_of']['completed'],19)  # Reviewed low-frequency weeks retain six incomplete lessons.
        self.assertEqual(len([r for r in tm.snapshot()['lessons_as_of'] if r['status']!='completed']),6)

    def test_switch_archives_old_memory_receipts_and_rejects_invalid(self):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO deliveries VALUES('old','future reply','mock_sent',?,'mock')",(s.now(),))
        before=tm.state()
        with self.assertRaises(ValueError):tm.activate('2025-12-01')
        self.assertEqual(tm.state(),before)
        value=tm.activate('2025-10-13 16:00')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])
        with closing(sqlite3.connect(value['archive'])) as c:
            self.assertEqual(c.execute('SELECT body FROM deliveries').fetchone()[0],'future reply')

    def test_exact_highlight_cutoff_and_duplicates(self):
        import host_highlights as host
        tm.activate('2025-11-07 20:14')
        self.assertEqual(p.refresh('2025-11-07'),[])
        tm.activate('2025-11-07 20:15')
        rows=p.refresh('2025-11-07');self.assertEqual(len(rows),1)
        self.assertEqual(p.deliver([rows[0]['id']],'模型改写',manual_test=True)['status'],'blocked')
        result=host.queue();self.assertEqual(result['status'],'queued')
        self.assertTrue(host.queue()['items'][0]['duplicate'])
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'r'}):w.flush()
        confirm_outbox()
        tm.advance('2025-11-07 20:30')
        self.assertEqual(len(p.refresh('2025-11-07')),1)

    def test_no_second_week_or_21_day_false_highlight(self):
        tm.activate('2025-10-26')
        self.assertEqual(p.refresh('2025-10-26'),[])
        tm.activate('2025-11-02')
        self.assertTrue(all('21' not in x['title'] for x in p.refresh('2025-11-02')))

    def test_transport_timeout_never_blindly_resends(self):
        with patch.object(w,'http_send',side_effect=TimeoutError) as http:
            self.assertEqual(w.send('key','hello')['status'],'unknown')
            self.assertTrue(w.send('key','hello')['duplicate'])
            self.assertEqual(http.call_count,1)
        with self.assertRaises(ValueError):w.send('key','changed')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO events(id,kind,payload,status,attempts) VALUES('timeout','passive','{}','pending',0)")
        with patch.object(w,'http_send',side_effect=TimeoutError):
            self.assertEqual(s.mock_reply('timeout','测试回复')['status'],'unknown')
        with self.assertRaises(ValueError):b.finish('timeout','2025-10-24')

    def test_callback_identity_auth_dedupe_and_control(self):
        base={**self.cfg['route'],'isSelf':False,'messageType':7,
              'messageId':'m1','payload':{'text':'今天欠了几课？'}}
        variants=[{**base,'imContactId':'other'},base,base,
                  {**base,'messageId':'m2','payload':{'text':'测试日期 2025-10-15 20:30'}}]
        with closing(sqlite3.connect(self.provider['database'])) as c,c:
            for i,x in enumerate(variants,1):
                c.execute('INSERT INTO callbacks VALUES(?,?,?,?,?)',
                          (i,'message',json.dumps(x),'2026-10-20T00:00:00+00:00','received_token_verified'))
        self.assertTrue(w.receive()['ingested'])
        control=w.receive()
        self.assertEqual(control['callback_id'],4)
        self.assertEqual(len(s.read('SELECT * FROM messages')),1)
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'r'}):
            before=tm.state();self.assertFalse(w.control(control)['changed']);self.assertEqual(tm.state(),before)
            confirm_outbox()
            pending=s.read("SELECT id FROM events WHERE kind='passive' AND status='pending'")[0]['id']
            s.mock_reply(pending,'已收到你的问题。');confirm_outbox()
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE events SET status='done' WHERE id=?",(pending,))
            self.assertTrue(w.control({**control,'callback_id':5,'message_id':'m3'})['changed'])
        self.assertEqual(tm.state()['as_of'],'2025-10-15T20:30:00+08:00')
        self.assertIsNone(w.receive())

    def test_real_reply_queued_and_provider_receipt(self):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO events(id,kind,payload,status,attempts) VALUES('e','passive','{}','pending',0)")
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'r'}) as http:
            self.assertEqual(s.mock_reply('e','侨侨，今天还有4课未完成。')['status'],'accepted')
            w.flush();w.flush();self.assertEqual(http.call_count,1)
        row=s.read('SELECT * FROM test_outbox')[0]
        with closing(sqlite3.connect(s.DB)) as c,c:
            w.observe(c,{'externalRequestId':row['id'],'requestId':'r','sendCode':0,'messageId':'sent-id'})
        self.assertEqual(s.read('SELECT status FROM test_outbox')[0]['status'],'sent')

    def test_native_image_request_and_type_identity(self):
        url='https://coolcdn.igetcool.com/p/test.png'
        response=io.BytesIO(b'{"errcode":0,"requestId":"image-request"}')
        with patch.object(w,'build_opener') as opener:
            opener.return_value.open.return_value=response
            w.http_send({'api_base_url':'https://provider.example','expected_token':'test'},
                        self.cfg['route'],'image-key',url,6)
            request=opener.return_value.open.call_args.args[0]
            payload=json.loads(request.data)
            self.assertEqual(payload['messageType'],6)
            self.assertEqual(payload['payload'],{'url':url})
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'r'}) as http:
            w.send('image-key',url,6);w.send('image-key',url,6)
            self.assertEqual(http.call_count,1)
            with self.assertRaises(ValueError):w.send('image-key',url,7)
            with self.assertRaises(ValueError):w.send('unsafe','http://127.0.0.1/p.png',6)

    def test_model_image_reply_only_current_assets(self):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO events(id,kind,payload,status,attempts) VALUES('image-request','passive','{}','pending',0)")
        with patch.object(w,'http_send') as http:
            with self.assertRaises(ValueError):
                w.reply_with_highlights('image-request','请看图片',['highlight-00001-11'])
            http.assert_not_called()
        with self.assertRaises(ValueError):tm.activate('2025-11-07 20:30')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'unavailable-reply'}):
            s.mock_reply('image-request','当前没有可发送的这张图片。')
        confirm_outbox()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE events SET status='done' WHERE id='image-request'")
        reset=tm.activate('2025-11-07 20:30')
        self.assertEqual(s.read('SELECT * FROM test_outbox_parts'),[])
        self.assertEqual(s.read('SELECT * FROM service_hours_drafts'),[])
        with closing(sqlite3.connect(reset['archive'])) as archived:
            self.assertEqual(archived.execute('SELECT COUNT(*) FROM test_outbox_parts').fetchone()[0],1)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO events(id,kind,payload,status,attempts) VALUES('image-request','passive','{}','pending',0)")
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'r'}) as http:
            self.assertEqual(w.reply_with_highlights('image-request','请看图片',['highlight-00001-02'])['status'],'accepted')
            w.reply_with_highlights('image-request','请看图片',['highlight-00001-02'])
            self.assertEqual(http.call_count,2)
            self.assertEqual(http.call_args.args[4],6)
            with self.assertRaises(ValueError):
                w.reply_with_highlights('image-request','请看另一张',['highlight-00001-11'])

    def test_highlight_bundle_partial_receipts_and_replay(self):
        tm.activate('2025-11-07 20:30')
        candidates=p.refresh('2025-11-07')
        self.assertEqual(p.deliver([x['id'] for x in candidates],'模型改写',manual_test=True)['status'],'blocked')
        import host_highlights as host
        result=host.queue();self.assertEqual(result['status'],'queued')
        with patch.object(w,'http_send',side_effect=[
            {'errcode':0,'requestId':'text'}, TimeoutError]) as http:
            self.assertEqual(w.flush()[0]['status'],'partial')
            w.flush()
            self.assertEqual(http.call_count,2)
        rows=s.read('SELECT id,message_type,status,request_id FROM test_outbox ORDER BY rowid')
        self.assertEqual([x['message_type'] for x in rows],[7,6])
        self.assertNotIn('https://',s.read('SELECT body FROM deliveries')[0]['body'])
        for row in rows[:1]:
            with closing(sqlite3.connect(s.DB)) as c,c:
                w.observe(c,{'externalRequestId':row['id'],'requestId':row['request_id'],
                             'sendCode':0,'messageId':'provider-'+row['id']})
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'partial')
        with closing(sqlite3.connect(s.DB)) as c,c:
            w.observe(c,{'externalRequestId':rows[1]['id'],'requestId':'late-image',
                         'sendCode':0,'messageId':'late-confirmed'})
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'sent')
        self.assertTrue(host.queue()['items'][0]['duplicate'])


if __name__=='__main__':
    unittest.main()
