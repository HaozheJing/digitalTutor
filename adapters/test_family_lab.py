import concurrent.futures
import tempfile
import threading
import time
import unittest
import json
import sqlite3
from pathlib import Path
from family_lab import Lab,encode


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.lab=Lab(Path(self.tmp.name)/'control.sqlite');self.lab.initialize()
        self.families=[{'family_id':str(i),'student':'student-'+str(i),'workspace':str(Path(self.tmp.name)/str(i))} for i in range(5)]
        self.run=self.lab.create_run(self.families,'2025-10-24T17:30:00+08:00')
        self.lab.control(self.run,'0','experiment_pause',False)
        self.sync()

    def sync(self):
        epoch=self.lab.current()['epoch']
        for f in self.lab.snapshot()['families']:self.lab.acknowledge(self.run,f['family_id'],f['inbox_seq'],epoch)

    def turn(self,f='0',token=None):
        token=token or 'token-'+f
        self.lab.begin(self.run,f,token,'event','event-'+token)
        return token

    def send(self,f='0',op=None,ordinal=0,group='g',token=None,kind='ai'):
        return self.lab.enqueue(self.run,f,op or 'op-'+f,group,ordinal,'hello-'+f,7,token or 'token-'+f,kind)

    def test_five_distinct_scopes(self):
        for i in range(5):self.turn(str(i))
        with self.assertRaisesRegex(ValueError,'already_active'):self.turn('0','other')
        self.assertEqual(sum(f['busy'] for f in self.lab.snapshot()['families']),5)

    def test_closed_issue_is_not_reopened_by_old_projection(self):
        first=self.lab.case(self.run,'0','parent',['a'],'call parent');self.lab.close_case(first)
        self.assertEqual(self.lab.case(self.run,'0','parent',['a'],'old'),first)
        self.assertEqual(self.lab.read('SELECT status FROM cases WHERE id=?',(first,))[0]['status'],'closed')
        second=self.lab.case(self.run,'0','parent',['b'],'new episode')
        self.assertNotEqual(first,second)

    def test_new_message_after_close_while_held_returns_to_queue(self):
        old=self.lab.case(self.run,'0','parent',['a'])
        self.lab.control(self.run,'0','takeover');self.lab.close_case(old)
        self.lab.ingest(self.run,'0','later','message',{'text':'another question'})
        current=self.lab.read("SELECT * FROM cases WHERE status='open'")
        self.assertEqual(len(current),1);first=current[0]['first_wait']
        self.lab.ingest(self.run,'0','again','message',{'text':'more context'})
        current=self.lab.read("SELECT * FROM cases WHERE status='open'")
        self.assertEqual(len(current),1);self.assertEqual(current[0]['first_wait'],first)
        self.assertEqual(self.lab.read('SELECT status FROM cases WHERE id=?',(old,))[0]['status'],'closed')
        self.assertEqual(self.lab.snapshot()['families'][0]['held'],1)

    def test_close_reviews_current_messages_and_preserves_later_input(self):
        case=self.lab.case(self.run,'0','human',['alert'])
        self.lab.control(self.run,'0','takeover')
        first=self.lab.ingest(self.run,'0','first','message',{'text':'teacher please'})
        with self.assertRaisesRegex(ValueError,'收到新消息'):
            self.lab.close_case(case,0)
        self.assertEqual(self.lab.read('SELECT status FROM cases WHERE id=?',(case,))[0]['status'],'open')
        self.lab.close_case(case,first['seq'])
        second=self.lab.ingest(self.run,'0','second','message',{'text':'new question'})
        key='human_handled:'+self.run+':0'
        self.assertEqual(int(self.lab.read('SELECT value FROM settings WHERE key=?',(key,))[0]['value']),first['seq'])
        self.assertGreater(second['seq'],first['seq'])
        self.assertEqual(self.lab.snapshot()['families'][0]['held'],1)

    def test_teacher_projection_retires_old_batch_without_losing_new_input(self):
        from family_lab_bridge import project_human_handled
        first=self.lab.ingest(self.run,'0','first','message',{'text':'old'})
        case=self.lab.case(self.run,'0','human',[first['id']]);self.lab.close_case(case,first['seq'])
        second=self.lab.ingest(self.run,'0','second','message',{'text':'new'})
        with sqlite3.connect(':memory:') as c:
            c.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE events(id TEXT,kind TEXT,payload TEXT,status TEXT,completed_at TEXT);')
            for eid,refs,status in [('old',[first['id']],'pending'),('mixed',[first['id'],second['id']],'pending'),('new',[second['id']],'pending'),('issued',[first['id']],'awaiting_delivery')]:
                c.execute('INSERT INTO events VALUES(?,?,?,?,NULL)',(eid,'passive',encode({'source_message_ids':refs}),status))
            cfg={'run_id':self.run,'family_id':'0'}
            project_human_handled(c,self.lab,cfg);project_human_handled(c,self.lab,cfg)
            self.assertEqual(dict(c.execute('SELECT id,status FROM events')),{'old':'human_handled','mixed':'superseded','new':'pending','issued':'awaiting_delivery'})
            self.assertEqual(c.execute('SELECT source_id,handled FROM message_hook_inbound').fetchall(),[(first['id'],1)])
            project_human_handled(c,self.lab,{'run_id':self.run,'family_id':'1'})
            self.assertEqual(c.execute('SELECT COUNT(*) FROM message_hook_inbound').fetchone()[0],1)

    def test_public_cost_time_and_missing_usage(self):
        import datetime as dt
        from family_lab_cost import estimate
        usage={'prompt_tokens':1000000,'completion_tokens':1000000,'prompt_tokens_details':{'cached_tokens':500000}}
        peak=dt.datetime.fromisoformat('2026-10-30T10:00:00+08:00').timestamp()
        night=dt.datetime.fromisoformat('2026-10-30T02:00:00+08:00').timestamp()
        self.assertAlmostEqual(estimate('deepseek-v4.1-flash-economy',usage,peak)[0],7.216)
        self.assertAlmostEqual(estimate('deepseek-v4.1-flash-economy',usage,night)[0],3.608)
        self.assertIsNone(estimate('unknown',usage,peak)[0]);self.assertIsNone(estimate('deepseek-v4-pro',None,peak)[0])

    def test_receipt_before_http_response_is_not_downgraded(self):
        self.turn();self.send();part=self.lab.claim_send(min_interval=0)
        self.lab.receipt('early',part['id'],'sent',part['route'],'request','message')
        self.lab.transport_result(part['id'],'accepted','request')
        self.assertEqual(self.lab.read('SELECT status FROM outbox WHERE id=?',(part['id'],))[0]['status'],'sent')

    def test_new_fact_fences_old_generated_text(self):
        self.turn();self.send();self.lab.ingest(self.run,'0','new-learning','completion',{'lesson_id':'L2-004'})
        self.assertIsNone(self.lab.claim_send(min_interval=0))
        self.assertEqual(self.lab.read("SELECT status FROM outbox WHERE id='op-0'")[0]['status'],'cancelled')

    def test_accepted_timeout_keeps_unknown_and_late_receipt(self):
        self.turn();self.send();part=self.lab.claim_send(min_interval=0)
        self.lab.transport_result(part['id'],'accepted','r')
        self.assertEqual(self.lab.receipt_timeouts(time.time()+121),1)
        self.assertEqual(self.lab.read('SELECT status FROM outbox WHERE id=?',(part['id'],))[0]['status'],'unknown')
        self.lab.receipt('late',part['id'],'sent',part['route'],'r','m')
        self.assertEqual(self.lab.read('SELECT status FROM outbox WHERE id=?',(part['id'],))[0]['status'],'sent')

    def test_failed_first_part_cancels_following_part(self):
        self.turn();self.send(op='first');self.send(op='second',ordinal=100)
        part=self.lab.claim_send(min_interval=0);self.lab.transport_result(part['id'],'failed')
        self.assertIsNone(self.lab.claim_send(min_interval=0))
        self.assertEqual(self.lab.read("SELECT status FROM outbox WHERE id='second'")[0]['status'],'cancelled')

    def test_settled_transition_does_not_reopen_on_later_chat(self):
        t=self.lab.advance(self.run,'2025-10-24T18:00:00+08:00');self.sync()
        self.assertEqual(self.lab.transition_status(t)['status'],'settled')
        self.turn();self.assertEqual(self.lab.transition_status(t)['status'],'settled')

    def test_model_limit_and_interactive_reserve(self):
        requests=[self.lab.model_enqueue(self.run,str(i),'t','background','m') for i in range(4)]
        self.assertTrue(self.lab.model_claim(requests[0]));self.assertTrue(self.lab.model_claim(requests[1]))
        self.assertFalse(self.lab.model_claim(requests[2]))
        interactive=self.lab.model_enqueue(self.run,'4','t','interactive','m')
        self.assertTrue(self.lab.model_claim(interactive));self.assertFalse(self.lab.model_claim(requests[3]))
        self.lab.model_finish(requests[0],{'input_tokens':1})
        self.assertTrue(self.lab.model_claim(requests[2]))

    def test_model_single_family_and_usage_missing(self):
        a=self.lab.model_enqueue(self.run,'0','t','interactive','m');b=self.lab.model_enqueue(self.run,'0','t','interactive','m')
        self.assertTrue(self.lab.model_claim(a));self.assertFalse(self.lab.model_claim(b))
        self.lab.model_finish(a,error='timeout');self.assertTrue(self.lab.model_claim(b))
        self.assertIsNone(self.lab.read('SELECT usage FROM model_attempts WHERE id=?',(a,))[0]['usage'])

    def test_model_fairness(self):
        a=self.lab.model_enqueue(self.run,'0','t','background','m');self.assertTrue(self.lab.model_claim(a));self.lab.model_finish(a,{})
        b=self.lab.model_enqueue(self.run,'0','t','background','m');d=self.lab.model_enqueue(self.run,'1','t','background','m')
        self.assertFalse(self.lab.model_claim(b));self.assertTrue(self.lab.model_claim(d))

    def test_concurrent_model_claims_cap(self):
        requests=[self.lab.model_enqueue(self.run,str(i),'t','interactive','m') for i in range(5)]
        barrier=threading.Barrier(5)
        def claim(r):barrier.wait();return self.lab.model_claim(r)
        with concurrent.futures.ThreadPoolExecutor(5) as pool:results=list(pool.map(claim,requests))
        self.assertLessEqual(sum(results),3)
        self.assertGreaterEqual(sum(results),1)

    def test_send_one_http_and_receipt_releases_other_family(self):
        for f in ('0','1'):self.turn(f);self.send(f)
        first=self.lab.claim_send(min_interval=0);self.assertIsNotNone(first)
        self.assertIsNone(self.lab.claim_send(min_interval=0))
        self.lab.transport_result(first['id'],'accepted','provider1')
        second=self.lab.claim_send(min_interval=0)
        self.assertNotEqual(second['family_id'],first['family_id'])

    def test_each_part_requires_prior_sent(self):
        self.turn();self.send();self.send(op='part2',ordinal=1)
        a=self.lab.claim_send(min_interval=0);self.lab.transport_result(a['id'],'accepted','r')
        self.assertIsNone(self.lab.claim_send(min_interval=0))
        self.lab.receipt('receipt',a['id'],'sent',a['route'],'r','message')
        self.assertEqual(self.lab.claim_send(min_interval=0)['id'],'part2')

    def test_unknown_never_retried_other_family_progresses(self):
        for f in ('0','1'):self.turn(f);self.send(f)
        a=self.lab.claim_send(min_interval=0);self.lab.transport_result(a['id'],'unknown')
        b=self.lab.claim_send(min_interval=0);self.assertNotEqual(a['family_id'],b['family_id'])
        self.lab.transport_result(b['id'],'failed')
        self.assertIsNone(self.lab.claim_send(min_interval=0))

    def test_takeover_before_submit_blocks(self):
        self.turn();self.send();self.lab.control(self.run,'0','takeover')
        self.assertIsNone(self.lab.claim_send(min_interval=0))
        self.assertEqual(self.lab.snapshot()['outbox'][0]['status'],'cancelled')

    def test_takeover_after_submit_cannot_recall(self):
        self.turn();self.send();a=self.lab.claim_send(min_interval=0)
        self.lab.control(self.run,'0','takeover');self.lab.transport_result(a['id'],'accepted','r')
        self.lab.receipt('receipt',a['id'],'sent',a['route'],'r','m')
        self.assertEqual(self.lab.snapshot()['outbox'][0]['status'],'sent')

    def test_new_input_revokes_old_generation(self):
        self.turn();self.send();self.lab.ingest(self.run,'0','m1','message',{'text':'new'})
        self.assertIsNone(self.lab.claim_send(min_interval=0))
        with self.assertRaisesRegex(ValueError,'revoked'):self.send(op='new')

    def test_message_dedup_and_scope(self):
        a=self.lab.ingest(self.run,'0','same','message',{'text':'one'})
        self.assertEqual(a,self.lab.ingest(self.run,'0','same','message',{'text':'one'}))
        b=self.lab.ingest(self.run,'1','same','message',{'text':'one'});self.assertNotEqual(a['id'],b['id'])
        with self.assertRaisesRegex(ValueError,'immutable'):self.lab.ingest(self.run,'0','same','message',{'text':'changed'})

    def test_future_facts_rejected(self):
        with self.assertRaisesRegex(ValueError,'future'):self.lab.ingest(self.run,'0','future','completion',{},'2025-10-25T01:00:00+08:00')

    def test_address_switch_and_book_title_queue_without_automatic_takeover(self):
        from unittest.mock import patch
        import family_lab_bridge as bridge
        db=Path(self.tmp.name)/'bridge.sqlite'
        with sqlite3.connect(db) as c:
            c.executescript('CREATE TABLE service_tasks(id TEXT,skill TEXT,expires_at TEXT,candidate_id TEXT); CREATE TABLE service_links(action_key TEXT,task_id TEXT);')
        bodies=['安安妈妈，晚上好呀，这两天阅读时间方便吗？','子涵，这两天《了不起的狐狸爸爸》的两节课还没读，想问问是时间不好安排，还是读起来有点吃力呀？']
        with patch.object(bridge,'connection',return_value=({'run_id':self.run,'family_id':'0'},self.lab)),patch.object(bridge.s,'DB',db),patch('test_mode.state',return_value={'as_of':self.lab.current()['as_of']}):
            for i,body in enumerate(bodies):
                self.assertTrue(bridge.enqueue('address-'+str(i),'address-'+str(i),body,7)['queued'])
            self.assertEqual(self.lab.read('SELECT COUNT(*) n FROM cases')[0]['n'],0)
            self.assertEqual(self.lab.snapshot()['families'][0]['held'],0)
            self.assertEqual([x['body'] for x in self.lab.read('SELECT body FROM outbox ORDER BY created')],bodies)
            self.lab.control(self.run,'0','takeover',True,'teacher_workbench')
            with self.assertRaisesRegex(ValueError,'host_send_blocked'):
                bridge.enqueue('after-hold','after-hold','新的回复',7)

    def test_clock_waits_for_queued_delivery_and_previous_transition(self):
        self.turn();self.send();self.lab.finish_turn(self.run,'0')
        with self.assertRaisesRegex(ValueError,'receipts'):self.lab.advance(self.run,'2025-10-24T18:00:00+08:00')
        a=self.lab.claim_send(min_interval=0);self.lab.transport_result(a['id'],'failed')
        t=self.lab.advance(self.run,'2025-10-24T18:00:00+08:00')
        with self.assertRaisesRegex(ValueError,'previous_transition'):self.lab.advance(self.run,'2025-10-24T19:00:00+08:00')
        self.sync();self.lab.advance(self.run,'2025-10-24T19:00:00+08:00')
        self.assertEqual(self.lab.transition_status(t)['status'],'settled')

    def test_clock_waits_for_models_turns_receipts_and_inbox(self):
        self.turn()
        with self.assertRaisesRegex(ValueError,'turns'):self.lab.advance(self.run,'2025-10-24T18:00:00+08:00')
        self.lab.finish_turn(self.run,'0');m=self.lab.model_enqueue(self.run,'0','t','interactive','m')
        with self.assertRaisesRegex(ValueError,'models'):self.lab.advance(self.run,'2025-10-24T18:00:00+08:00')
        self.lab.model_finish(m,error='cancelled');self.lab.ingest(self.run,'1','in','message',{})
        with self.assertRaisesRegex(ValueError,'ingress'):self.lab.advance(self.run,'2025-10-24T18:00:00+08:00')

    def test_clock_sync_all_five(self):
        t=self.lab.advance(self.run,'2025-10-24T18:00:00+08:00')
        self.assertEqual(len(self.lab.transition_status(t)['waiting_families']),5)
        self.sync();self.assertEqual(self.lab.transition_status(t)['status'],'settled')
        with self.assertRaisesRegex(ValueError,'invalid'):self.lab.advance(self.run,'2025-10-24T17:00:00+08:00')

    def test_partial_receipt_and_duplicate_callback(self):
        self.turn();self.send();a=self.lab.claim_send(min_interval=0)
        self.lab.receipt('receipt',a['id'],'sent',a['route'],'r','m')
        self.lab.transport_result(a['id'],'accepted','r')
        self.lab.receipt('receipt',a['id'],'sent',a['route'],'r','m')
        self.lab.receipt('later-fail',a['id'],'failed',a['route'],'r')
        self.assertEqual(self.lab.snapshot()['outbox'][0]['status'],'sent')

    def test_wrong_route_receipt_cannot_change(self):
        self.turn();self.send();a=self.lab.claim_send(min_interval=0)
        self.assertFalse(self.lab.receipt('bad',a['id'],'sent','simulation:4',message_id='m'))
        self.assertEqual(self.lab.snapshot()['outbox'][0]['status'],'submitting')

    def test_case_dedup_drafts_and_end_distinct_resume(self):
        case=self.lab.case(self.run,'0','parent_authorization',['m1'])
        first=self.lab.snapshot()['cases'][0]['first_wait']
        self.assertEqual(case,self.lab.case(self.run,'0','parent_authorization',['m2']))
        self.assertEqual(first,self.lab.snapshot()['cases'][0]['first_wait'])
        a=self.lab.save_draft(case,'AI reference',0,ai=True)
        b=self.lab.save_draft(case,'teacher edit',a['revision'])
        self.assertEqual(self.lab.save_draft(case,'new AI',b['revision'],ai=True)['edited_body'],'teacher edit')
        self.lab.control(self.run,'0','takeover');self.lab.close_case(case)
        self.assertEqual(self.lab.snapshot()['families'][0]['held'],1)

    def test_teacher_taking_existing_case_does_not_create_second_case(self):
        from family_lab_server import Workbench
        workbench=Workbench.__new__(Workbench);workbench.lab=self.lab
        original=self.lab.case(self.run,'0','parent:S09',['evidence'])
        workbench.action('control',{'run_id':self.run,'family_id':'0','action':'takeover','value':True})
        self.assertEqual([x['id'] for x in self.lab.snapshot()['cases']],[original])

    def test_restart_preserves_unknown_sent_and_stops(self):
        self.turn();self.send();self.lab.claim_send(min_interval=0);self.lab.recover()
        self.assertEqual(self.lab.snapshot()['outbox'][0]['status'],'unknown')
        self.assertEqual(self.lab.current()['paused'],1)
        self.assertIsNone(self.lab.claim_send(min_interval=0))

    def test_archive_new_run_keeps_old_outbox(self):
        self.turn();self.send();a=self.lab.claim_send(min_interval=0);self.lab.transport_result(a['id'],'unknown')
        self.lab.finish_turn(self.run,'0');self.lab.control(self.run,'0','experiment_pause')
        self.lab.archive(self.run)
        new=self.lab.create_run(self.families,'2025-10-13T07:00:00+08:00')
        self.assertNotEqual(new,self.run);self.assertEqual(self.lab.read('SELECT status FROM outbox')[0]['status'],'unknown')

    def test_real_sent_and_unknown_are_not_replayed_in_new_round(self):
        route=dict(orgId='test-org',imBotId='test-bot',imContactId='test-contact',chatId='test-chat')
        for status in ('sent','unknown'):
            with self.subTest(status=status):
                lab=Lab(Path(self.tmp.name)/(status+'.sqlite'));lab.initialize()
                with lab.transaction() as c:c.execute("INSERT INTO settings VALUES('contact_authorized_routes',?)",(encode([route]),))
                def run():
                    rid=lab.create_run(self.families,'2025-10-24T17:30:00+08:00','wechat');lab.bind(rid,'0','北斋先生',route)
                    lab.control(rid,'0','experiment_pause',False)
                    for f in lab.snapshot()['families']:lab.acknowledge(rid,f['family_id'],0,lab.current()['epoch'])
                    return rid
                old=run();lab.begin(old,'0','first','task','task-one')
                lab.enqueue(old,'0','first-part','first-group',0,'text',7,'first','ai',semantic_key='S08:2025-10-24:0')
                part=lab.claim_send(min_interval=0,wall_at='2026-10-30T10:00:00+08:00')
                lab.transport_result(part['id'],'unknown' if status=='unknown' else 'accepted','request')
                if status=='sent':lab.receipt('actual-fixture',part['id'],'sent',part['route'],'request','message')
                lab.finish_turn(old,'0');lab.control(old,'0','experiment_pause');lab.archive(old)
                new=run();lab.begin(new,'0','second','task','task-two')
                with self.assertRaisesRegex(ValueError,'cross_run_delivery_already_reserved'):
                    lab.enqueue(new,'0','new-part','new-group',0,'text',7,'second','ai',semantic_key='S08:2025-10-24:0')
                self.assertEqual(lab.read("SELECT status FROM outbox WHERE id='first-part'")[0]['status'],status)

    def test_real_night_cannot_be_bypassed_by_business_daytime(self):
        self.lab.control(self.run,'0','experiment_pause')
        with self.lab.transaction() as c:c.execute("UPDATE runs SET mode='wechat' WHERE id=?",(self.run,))
        route=dict(orgId='test-org',imBotId='test-bot',imContactId='test-contact',chatId='test-chat')
        with self.lab.transaction() as c:c.execute("INSERT INTO settings VALUES('contact_authorized_routes',?)",(encode([route]),))
        self.lab.bind(self.run,'0','北斋先生',route)
        self.lab.control(self.run,'0','experiment_pause',False);self.sync();self.turn();self.send()
        self.assertIsNone(self.lab.claim_send(min_interval=0,wall_at='2026-10-30T23:00:00+08:00'))
        self.assertEqual(self.lab.read('SELECT status,error FROM outbox')[0],{'status':'cancelled','error':'real_night_no_late_send'})
        self.assertIsNone(self.lab.claim_send(min_interval=0,wall_at='2026-10-31T07:00:00+08:00'))


class CallbackRoutingTests(unittest.TestCase):
    def test_two_callbacks_isolate_and_reject_foreign_or_self_messages(self):
        from family_lab_server import Gateway
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);lab=Lab(home/'control.sqlite');lab.initialize()
            run=lab.create_run([{'family_id':str(i),'student':str(i),'workspace':str(home/str(i))} for i in range(5)],'2025-10-24T19:00:00+08:00','wechat')
            routes=[dict(orgId='test-org',imBotId='test-bot',imContactId='test-contact-'+str(i),chatId='test-chat-'+str(i)) for i in range(2)]
            with lab.transaction() as c:c.execute("INSERT INTO settings VALUES('contact_authorized_routes',?)",(encode(routes),))
            for fid,nickname in enumerate(('北斋先生','多多')):lab.bind(run,str(fid),nickname,routes[fid])
            db=home/'callbacks.sqlite'
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE callbacks(id INTEGER PRIMARY KEY,status TEXT,kind TEXT,body_json TEXT)')
                messages=[{**route,'source':0,'isSelf':False,'messageId':'same-upstream-id','payload':{'text':'family '+str(i)}} for i,route in enumerate(routes)]
                messages.extend([{**messages[0],'chatId':'wrong-chat','messageId':'foreign'},{**messages[1],'isSelf':True,'messageId':'self'},{**messages[0],'messageId':'not-verified'}])
                for i,body in enumerate(messages):c.execute('INSERT INTO callbacks VALUES(?,?,?,?)',(i+1,'unverified' if i==4 else 'received_token_verified','message',encode(body)))
            (home/'gateway.private.json').write_text(encode({'database':str(db)}))
            (home/'registered-contacts.private.json').write_text(encode(dict(zip(('北斋先生','多多'),routes))))
            with lab.transaction() as c:c.execute("INSERT INTO settings VALUES('callback_cursor','0')")
            gateway=Gateway(home);gateway.callbacks();gateway.callbacks()
            received=lab.read('SELECT family_id,body FROM ingress ORDER BY family_id')
            self.assertEqual([x['family_id'] for x in received],['0','1'])
            self.assertEqual([json.loads(x['body'])['text'] for x in received],['family 0','family 1'])
            self.assertEqual(lab.read("SELECT value FROM settings WHERE key='callback_cursor'")[0]['value'],'5')


if __name__=='__main__':unittest.main()
