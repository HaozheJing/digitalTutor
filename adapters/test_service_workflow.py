import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_completion_ledger as base
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf

class WorkflowTests(unittest.TestCase):
    setUp=base.LedgerTests.setUp
    tearDown=base.LedgerTests.tearDown
    create_source=base.LedgerTests.create_source
    set_context=base.LedgerTests.set_context
    def begin_workflow(self,at='2025-10-13 06:30'):
        with patch.object(wf,'default_mode',return_value=True):tm.activate(at,False)
        ap.enable();wf.initialize()
        task=ap.claim();self.assertEqual(task['kind'],'plan')
        data=wf.context()
        return task,data

    def plan(self,data,create=False):
        choices=[{'skill':x['skill'],'decision':'skip','reason':'当前无需安排'} for x in data['catalog']]
        if create:
            con=next(x for x in data['catalog'] if x['skill']=='remind-reading')
            choices[0]={'skill':con['skill'],'decision':'create','reason':'今日有课，07点按最新状态复核','opportunity_id':con['opportunities'][0]['id'],'run_at':con['timing']['at']}
        return choices

    def stop_plan(self,tid):
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='done' WHERE id=?",(tid,))
            c.execute("DELETE FROM metadata WHERE key='active_service_task'")

    def new_task(self):
        plan,data=self.begin_workflow();wf.commit('planning-001',data['context_token'],self.plan(data,True));self.stop_plan(plan['id'])
        return s.read('SELECT * FROM service_task_versions')[0]['task_id']

    def start_action(self,tid):
        tm.advance('2025-10-13 07:00')
        state=tm.state();state['auto_highlights']=True
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        task=ap.claim();self.assertEqual(task['id'],tid);wf.prepare_execution(tid)

    def test_invalid_dialogue_evidence_is_actionable_and_never_mutates(self):
        self.begin_workflow()
        for evidence in [[{'source':'workflow_context','fact':'课程已排'}], ['fact'], {'source_message_id':'x'}, [{'source_message_id':'x','quote':''}]]:
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError,'evidence仅用于本轮对话原话'):
                wf.commit('schema-evidence-request','unused',[],evidence=evidence)
        self.assertEqual(s.read('SELECT * FROM service_workflow_commits'),[])
        self.assertEqual(s.read('SELECT * FROM service_task_versions'),[])

    def test_invalid_decisions_explain_schema_and_never_mutate(self):
        _,data=self.begin_workflow()
        for decisions in [[None],[{'decision':'schedule','reason':'later'}],[{'decision':'skip','reason':''}]]:
            with self.subTest(decisions=decisions),self.assertRaisesRegex(ValueError,r'decisions\[0\]'):
                wf.commit('invalid-decision-001',data['context_token'],decisions)
        self.assertFalse(s.read('SELECT * FROM service_workflow_commits'))
        self.assertFalse(s.read('SELECT * FROM service_task_versions'))

    def test_create_missing_reference_is_parameter_error_and_retryable(self):
        _,data=self.begin_workflow()
        for field in ('opportunity_id','run_at'):
            choices=self.plan(data,True);del choices[0][field]
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'这是参数缺失，不是业务事实未知'):
                wf.commit('missing-create-reference',data['context_token'],choices)
            self.assertFalse(s.read('SELECT * FROM service_task_versions'))
            self.assertFalse(s.read('SELECT * FROM service_workflow_commits'))
        receipt=wf.commit('missing-create-reference',data['context_token'],self.plan(data,True))
        self.assertEqual(receipt['status'],'committed')
        self.assertEqual(len(s.read('SELECT * FROM service_task_versions')),1)

    def test_candidate_guidance_matches_active_scheduling_mode(self):
        self.begin_workflow()
        import business
        current=business.candidates('2025-10-13')['scheduling']
        self.assertIn('workflow_context/commit',current)
        self.assertIn('opportunity_id',current)
        self.assertNotIn('service_schedule',current)
        with patch.object(wf,'enabled',return_value=False):
            self.assertIn('service_schedule',business.candidates('2025-10-13')['scheduling'])

    def test_missing_commit_required_fields_is_actionable_without_mutation(self):
        _,data=self.begin_workflow()
        valid={'request_id':'missing-required-fields','context_token':data['context_token'],'decisions':self.plan(data,True)}
        for field in ('request_id','context_token','decisions'):
            args={k:v for k,v in valid.items() if k!=field}
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'必须同时传request_id、context_token和decisions列表'):
                wf.commit(**args)
            self.assertFalse(s.read('SELECT * FROM service_task_versions'))
            self.assertFalse(s.read('SELECT * FROM service_workflow_commits'))
        self.assertEqual(wf.commit(**valid)['status'],'committed')

    def test_only_internal_wake_without_model_and_empty_plan(self):
        _,data=self.begin_workflow()
        import host_highlights
        with patch.object(tm,'highlight_lookup',side_effect=AssertionError('no new asset poll')):
            self.assertEqual(host_highlights.tick()['status'],'model_owned')
        self.assertEqual([x['kind'] for x in ap.tasks()],['plan'])
        result=wf.commit('empty-plan-001',data['context_token'],self.plan(data))
        self.assertEqual(result['status'],'committed');self.assertFalse(s.read('SELECT * FROM service_task_versions'))
        self.assertEqual(len(s.read('SELECT * FROM daily_plans')),1)
        self.assertTrue(wf.commit('empty-plan-001',data['context_token'],self.plan(data))['duplicate'])

    def test_s01_same_planning_wake_catches_up_without_changing_fixed_time(self):
        plan,data=self.begin_workflow('2025-10-13T06:30:05+08:00')
        choices=self.plan(data,True)
        self.assertEqual(choices[0]['run_at'],'2025-10-13T06:30:00+08:00')
        wrong=json.loads(json.dumps(choices));wrong[0]['run_at']='2025-10-13T06:31:00+08:00'
        with self.assertRaisesRegex(ValueError,'固定时点'):wf.commit('wrong-fixed-001',data['context_token'],wrong)
        receipt=wf.commit('late-plan-001',data['context_token'],choices)
        self.assertTrue(wf.commit('late-plan-001',data['context_token'],choices)['duplicate'])
        tid=receipt['decisions'][0]['task_id'];self.stop_plan(plan['id'])
        state=tm.state();state.update(auto_highlights=True,manual_service_task_id=tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        task=ap.claim();self.assertEqual(task['id'],tid)
        self.assertEqual(task['due_at'],'2025-10-13T06:30:00+08:00')
        self.assertEqual(len(s.read('SELECT * FROM service_task_versions')),1)

    def test_fixed_s01_catchup_does_not_extend_expiry(self):
        catalog=wf.catalog
        def expired(c,state):
            result=catalog(c,state)
            for entry in result:
                if entry['skill']=='remind-reading':
                    for item in entry['opportunities']:item['time_window']['latest']='2025-10-13T06:30:04+08:00'
            return result
        with patch.object(wf,'catalog',side_effect=expired):
            _,data=self.begin_workflow('2025-10-13T06:30:05+08:00')
            with self.assertRaisesRegex(ValueError,'时间超出'):wf.commit('expired-fixed-001',data['context_token'],self.plan(data,True))
        self.assertFalse(s.read('SELECT * FROM service_task_versions'))

    def test_unique_matter_atomic_rollback_and_fixed_time(self):
        _,data=self.begin_workflow();choices=self.plan(data,True)
        bad=[*choices,{'skill':'invented','decision':'create','reason':'not allowed','opportunity_id':'x','run_at':'2025-10-13T08:00:00+08:00'}]
        with self.assertRaises(ValueError):wf.commit('bad-plan-001',data['context_token'],bad)
        self.assertFalse(s.read('SELECT * FROM service_task_versions'))
        wf.commit('good-plan-001',data['context_token'],choices)
        self.assertEqual(len(s.read('SELECT * FROM service_case_tasks')),1)
        self.assertEqual(len(s.read('SELECT * FROM service_cases')),1)
        with self.assertRaises(ValueError):wf.commit('stale-plan-001',data['context_token'],choices)

    def test_new_message_invalidates_execution_before_binding(self):
        tid=self.new_task();self.start_action(tid)
        token=s.read('SELECT execution_token FROM service_task_versions')[0]['execution_token']
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO messages VALUES('new','user','今天不要提醒',?,'fixture','hash',1)",(s.now(),))
        with self.assertRaisesRegex(ValueError,'旧执行失效'):wf.execution_authority(token)
        with closing(sqlite3.connect(s.DB)) as c,c:
            with self.assertRaisesRegex(ValueError,'旧执行失效'):wf.bind_delivery(c,'test-delivery',tid)
        self.assertFalse(s.read('SELECT * FROM service_links'))

    def test_accepted_partial_unknown_late_sent_project_original(self):
        tid=self.new_task();self.start_action(tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            wf.bind_delivery(c,'receipt',tid)
            c.execute("INSERT INTO deliveries VALUES('receipt','content','accepted',?,'fixture')",(s.now(),))
            wf.reconcile(c)
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'awaiting_delivery')
        for status,expected in [('partial','blocked'),('unknown','blocked'),('failed','blocked'),('sent','completed')]:
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute('UPDATE deliveries SET status=?',(status,));wf.reconcile(c)
            self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],expected)
        self.assertEqual(len(s.read('SELECT * FROM service_task_versions')),1)

    def test_publish_invalidates_pending_and_requests_reassessment(self):
        tid=self.new_task();wf.published('remind-reading')
        self.assertEqual(s.read('SELECT needs_revalidation FROM service_task_versions')[0]['needs_revalidation'],1)
        self.assertEqual(len(s.read("SELECT * FROM service_tasks WHERE kind='plan' AND status='planned'")),1)

    def dialogue(self):
        import live_runtime
        with closing(sqlite3.connect(s.DB)) as c,c:
            for ident,body in [('m-one','今天阅读提醒不用发了'),('m-two','其他安排先不变')]:
                c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)',(ident,'user',body,s.now(),'fixture','hash'))
            live_runtime.enqueue(c,'batch','passive',{'source_message_id':'m-two','source_message_ids':['m-one','m-two']})
            c.execute("UPDATE events SET status='dispatched' WHERE id='batch'")
        return wf.context()

    def test_dialogue_earlier_message_cancel_and_no_broad_pause(self):
        tid=self.new_task();ctx=self.dialogue();task=ctx['tasks'][0]
        decision={'task_id':tid,'revision':task['revision'],'decision':'cancel','reason':'今天不需阅读提醒'}
        evidence=[{'source_message_id':'m-one','quote':'今天阅读提醒不用发了'}]
        with self.assertRaisesRegex(ValueError,'所有主动服务'):
            wf.commit('cancel-bad-001',ctx['context_token'],[decision],evidence,{'scope':{'kind':'all'},'starts_at':'2025-10-13T06:30:00+08:00','ends_at':'2025-10-13T23:59:00+08:00'})
        wf.commit('cancel-good-001',ctx['context_token'],[decision],evidence)
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'cancelled')
        self.assertFalse(s.read('SELECT * FROM contact_constraints'))

    def test_passive_fulfillment_owns_task_before_flush_and_final_receipt(self):
        tid=self.new_task();ctx=self.dialogue();task=ctx['tasks'][0]
        import wechat_test
        def flush():
            self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(tid,))[0]['status'],'awaiting_delivery')
            self.assertEqual(len(s.read('SELECT * FROM service_links WHERE task_id=?',(tid,))),1)
        with patch.object(ap,'require_skills'),patch.object(wechat_test,'flush',side_effect=flush):
            wf.fulfill('fulfill-001',ctx['context_token'],tid,task['revision'],'当前服务已经完整回答。','当前真实对话与对应Skill目标完整一致')
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE deliveries SET status='sent'");wf.reconcile(c)
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'completed')

    def test_restart_preserves_model_task_without_fixed_jobs_or_followups(self):
        tid=self.new_task();before=s.read('SELECT id FROM service_tasks')
        ap.recover();ap.sync();ap.sync()
        self.assertEqual(s.read('SELECT id FROM service_tasks'),before)
        tm.advance('2025-10-20 06:00')
        self.assertFalse(s.read("SELECT * FROM service_tasks WHERE kind='case_review'"))
        self.assertEqual(len(s.read('SELECT * FROM service_task_versions')),1)

    def test_dialogue_interrupt_invalidates_old_token_and_changes_same_task(self):
        tid=self.new_task();self.start_action(tid)
        token=s.read('SELECT execution_token FROM service_task_versions')[0]['execution_token']
        self.dialogue();wf.interrupt_for_dialogue()
        with self.assertRaises(ValueError):wf.execution_authority(token)
        ctx=wf.context();self.assertEqual(ctx['owner']['type'],'dialogue')
        task=ctx['tasks'][0]
        wf.commit('collision-cancel',ctx['context_token'],[{'task_id':tid,'revision':task['revision'],'decision':'cancel','reason':'当前原话取消'}],[{'source_message_id':'m-one','quote':'今天阅读提醒不用发了'}])
        self.assertEqual(len(s.read('SELECT * FROM service_task_versions')),1)
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'cancelled')

    def test_passive_fulfillment_rechecks_at_transport_and_is_idempotent(self):
        tid=self.new_task();ctx=self.dialogue();task=ctx['tasks'][0]
        import wechat_test
        args=('fulfill-stable',ctx['context_token'],tid,task['revision'],'完整的服务回复。','与原事项完整一致')
        with patch.object(ap,'require_skills'),patch.object(wechat_test,'flush'):
            wf.fulfill(*args);self.assertTrue(wf.fulfill(*args)['duplicate'])
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO messages VALUES('newer','user','等等先不发',?,'fixture','hash',1)",(s.now(),))
        with closing(sqlite3.connect(s.DB)) as c,c:
            for suffix in ('',':emoji:0'):
                with self.assertRaisesRegex(ValueError,'被动履约发送授权已失效'):
                    wf.authorize_part(c,tm.state()['run_id']+':reply:batch'+suffix,'operation-new')
        self.assertFalse(s.read('SELECT * FROM service_send_authorizations'))

    def test_restart_reuses_running_task_and_invalidates_old_execution(self):
        tid=self.new_task();self.start_action(tid);before=s.read('SELECT id FROM service_tasks')
        token=s.read('SELECT execution_token FROM service_task_versions')[0]['execution_token']
        ap.recover()
        self.assertEqual(s.read('SELECT id FROM service_tasks'),before)
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(tid,))[0]['status'],'deferred')
        with self.assertRaises(ValueError):wf.execution_authority(token)

    def test_missing_internal_finish_recovers_same_key_without_fabricated_plan(self):
        task,ctx=self.begin_workflow()
        self.assertEqual(ap.settle(task['id'])['status'],'deferred')
        self.assertFalse(s.read('SELECT * FROM daily_plans'))
        self.assertFalse(s.read('SELECT * FROM service_task_versions'))
        self.assertEqual(len(ap.tasks()),1)

    def test_sender_new_message_between_parts_stops_unsent_and_never_retries(self):
        import wechat_test as w
        tid=self.new_task();self.start_action(tid)
        body='第一段完整服务内容。'*8+'\n\n'+'第二段完整服务内容。'*8
        with closing(sqlite3.connect(s.DB)) as c,c:
            wf.bind_delivery(c,'multipart',tid)
            c.execute("INSERT INTO deliveries VALUES('multipart',?,'queued',?,'test_wechat_pending')",(body,s.now()))
            w.queue_text(c,'multipart',body)
        def transport(*args):
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("INSERT INTO messages VALUES('collision','user','先等等',?,'fixture','hash',1)",(s.now(),))
            return {'errcode':0,'requestId':'accepted-first-part'}
        with patch.object(w,'http_send',side_effect=transport) as send:
            result=w.flush();w.flush()
        self.assertEqual(send.call_count,1)
        self.assertEqual(result[0]['status'],'blocked')
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'blocked')
        self.assertEqual(len(s.read('SELECT * FROM test_outbox')),1)

    def test_parallel_flush_does_not_finalize_inflight_bundle(self):
        import wechat_test as w
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        import time
        tid=self.new_task();self.start_action(tid)
        body='第一段完整服务内容。'*8+'\n\n'+'第二段完整服务内容。'*8
        with closing(sqlite3.connect(s.DB)) as c,c:
            wf.bind_delivery(c,'parallel-parts',tid)
            c.execute("INSERT INTO deliveries VALUES('parallel-parts',?,'queued',?,'test_wechat_pending')",(body,s.now()))
            w.queue_text(c,'parallel-parts',body)
        entered=Event();release=Event();calls=[]
        def transport(*args):
            calls.append(args[2])
            if len(calls)==1:
                entered.set();self.assertTrue(release.wait(5))
            return {'errcode':0,'requestId':'request-'+args[2]}
        with patch.object(w,'http_send',side_effect=transport),ThreadPoolExecutor(2) as pool:
            first=pool.submit(w.flush);self.assertTrue(entered.wait(5))
            second=pool.submit(w.flush)
            time.sleep(0.15)
            during=s.read("SELECT source FROM deliveries WHERE action_key='parallel-parts'")[0]['source']
            release.set();first.result(timeout=5);second.result(timeout=5)
        self.assertEqual(during,'test_wechat_pending')
        self.assertEqual(len(calls),2)
        self.assertEqual(len(set(calls)),2)
        self.assertEqual(s.read("SELECT status FROM deliveries WHERE action_key='parallel-parts'")[0]['status'],'accepted')
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'awaiting_delivery')
        from test_fixture_integration import confirm_outbox
        confirm_outbox()
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'completed')
        with patch.object(w,'http_send') as send:w.flush();send.assert_not_called()

    def test_birthday_host_card_is_planned_as_highlight_not_plain_action(self):
        plan,ctx=self.begin_workflow('2025-10-28 09:30')
        entries=[x for x in ctx['catalog'] if x['skill']=='celebrate-important-day']
        self.assertEqual(len(entries),1)
        card=next(x for x in entries if x['kind']=='highlight')
        self.assertEqual(card['allowed_triggers'],['observed_asset'])
        self.assertEqual(card['timing']['mode'],'window')
        item=card['opportunities'][0]
        self.assertFalse(any(o['id']==item['id'] for x in entries if x['kind']=='action' for o in x['opportunities']))
        choices=self.plan(ctx)
        choices.append({'skill':card['skill'],'decision':'create','reason':'已观察到生日卡','opportunity_id':item['id'],'run_at':ctx['business_time']})
        receipt=wf.commit('birthday-highlight-plan',ctx['context_token'],choices)
        tid=receipt['decisions'][-1]['task_id']
        self.assertEqual(s.read('SELECT kind FROM service_tasks WHERE id=?',(tid,))[0]['kind'],'highlight')

    def test_highlight_uses_shared_contact_gap_and_quota_without_receipt(self):
        plan,ctx=self.begin_workflow('2025-11-07 20:30')
        con=next(x for x in ctx['catalog'] if x['kind']=='highlight' and x['opportunities'][0]['status']=='eligible')
        item=con['opportunities'][0];choices=self.plan(ctx)
        choices.append({'skill':con['skill'],'decision':'create','reason':'已有高光','opportunity_id':item['id'],'run_at':ctx['business_time']})
        tid=wf.commit('highlight-contact-plan',ctx['context_token'],choices)['decisions'][-1]['task_id'];self.stop_plan(plan['id'])
        state=tm.state();state.update(auto_highlights=True,manual_service_task_id=tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        self.assertEqual(ap.claim()['id'],tid)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('prior-1','earlier service','sent','2025-11-07T20:00:00+08:00','test_wechat'))
        wf.prepare_execution(tid)
        result=wf.highlight();self.assertEqual(result['status'],'deferred');self.assertEqual(result['retry_at'],'2025-11-07T21:00:00+08:00')
        self.assertFalse(s.read('SELECT * FROM proactive_receipts'));self.assertFalse(s.read('SELECT * FROM test_outbox'))
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('prior-2','another service','sent','2025-11-07T18:00:00+08:00','test_wechat'))
        wf.prepare_execution(tid)
        self.assertEqual(wf.highlight()['status'],'skipped')
        self.assertFalse(s.read('SELECT * FROM proactive_receipts'));self.assertFalse(s.read('SELECT * FROM test_outbox'))

    def test_model_planned_highlight_preserves_original_caption_image_and_waits_all(self):
        import wechat_test as w
        plan,ctx=self.begin_workflow('2025-11-07 20:30')
        con=next(x for x in ctx['catalog'] if x['kind']=='highlight' and x['opportunities'][0]['status']=='eligible')
        item=con['opportunities'][0];choices=self.plan(ctx)
        choices.append({'skill':con['skill'],'decision':'create','reason':'当前已产生的原始高光','opportunity_id':item['id'],'run_at':ctx['business_time']})
        receipt=wf.commit('highlight-planning',ctx['context_token'],choices)
        tid=receipt['decisions'][-1]['task_id'];self.stop_plan(plan['id'])
        state=tm.state();state.update(auto_highlights=True,manual_service_task_id=tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        self.assertEqual(ap.claim()['id'],tid);wf.prepare_execution(tid)
        pending='resume-'+__import__('hashlib').sha256(item['id'].encode()).hexdigest()[:20]
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO proactive_pending VALUES(?,?,?,?,?)',(pending,s.encoded([item['id']]),ctx['business_time'],item['time_window']['latest'],'pending'))
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'caption'},{'errcode':0,'requestId':'image'}]) as send:
            wf.highlight();w.flush()
        self.assertEqual(s.read('SELECT status FROM proactive_pending WHERE id=?',(pending,))[0]['status'],'done')
        self.assertEqual(send.call_count,2)
        out=s.read('SELECT id,body,message_type,request_id FROM test_outbox ORDER BY rowid')
        self.assertEqual([r['body'] for r in out],[item['evidence']['share_txt'],item['evidence']['img_url']])
        with closing(sqlite3.connect(s.DB)) as c,c:
            w.observe(c,{'externalRequestId':out[0]['id'],'requestId':out[0]['request_id'],'sendCode':0,'messageId':'unit-caption'})
        self.assertNotEqual(s.read('SELECT status FROM service_cases')[0]['status'],'completed')
        with closing(sqlite3.connect(s.DB)) as c,c:
            w.observe(c,{'externalRequestId':out[1]['id'],'requestId':out[1]['request_id'],'sendCode':0,'messageId':'unit-image'})
        self.assertEqual(s.read('SELECT status FROM service_cases')[0]['status'],'completed')

if __name__=='__main__':unittest.main()
