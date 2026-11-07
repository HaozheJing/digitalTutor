import copy,json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as base
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf
import fixture_query as fq,s05_service as service,task_context,learning_context
import course_service,proactive,host_receipt,mcp_server,wechat_test as w


class S05Tests(unittest.TestCase):
    setUp=base.FixtureIntegrationTests.setUp
    tearDown=base.FixtureIntegrationTests.tearDown
    create_source=base.FixtureIntegrationTests.create_source
    set_context=base.FixtureIntegrationTests.set_context

    def zero_fixture(self):
        raw=json.loads(fq.FILE.read_text())
        for row in raw['learning_daily']:
            if row['date'] in ('2025-10-20','2025-10-21'):
                row.update(completed_at=None,checked_in_at=None,attended_at=None)
        raw['learning_events']=[e for e in raw['learning_events'] if e['occurred_at'][:10] not in ('2025-10-20','2025-10-21')]
        raw['highlight_assets']=[]
        fq.FILE.write_text(json.dumps(raw))

    def begin(self):
        self.zero_fixture()
        with patch.object(wf,'default_mode',return_value=True):tm.activate('2025-10-21 06:30',False)
        ap.enable();wf.initialize();parent=ap.claim();self.assertEqual(parent['kind'],'plan')
        ctx=wf.context();contract=next(x for x in ctx['catalog'] if x['skill']==service.RULE)
        options=[x for x in contract['opportunities'] if x['status']=='eligible'];self.assertEqual(len(options),1)
        choices=[{'skill':x['skill'],'decision':'skip','reason':'S05 test only'} for x in ctx['catalog']]
        next(x for x in choices if x['skill']==service.RULE).update(decision='create',opportunity_id=options[0]['id'],run_at=contract['timing']['at'])
        result=wf.commit('s05-test-plan',ctx['context_token'],choices)
        self.tid=next(x['task_id'] for x in result['decisions'] if x.get('skill')==service.RULE)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='done' WHERE id=?",(parent['id'],))
            c.execute("DELETE FROM metadata WHERE key='active_service_task'")
        tm.advance('2025-10-21 19:00')
        state=tm.state();state.update(auto_highlights=True,manual_service_task_id=self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        task=ap.claim();self.assertEqual(task['id'],self.tid);wf.prepare_execution(self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET native_request_id='s05-request' WHERE id=?",(self.tid,))
        provider=patch.object(course_service,'course_knowledge',return_value={'status':'ok','body':'故事里一只狐狸想吃到树上的葡萄','live_read':True})
        self.provider=provider.start();self.addCleanup(provider.stop)

    def send(self):
        task=ap.active();token=s.read('SELECT execution_token FROM service_task_versions WHERE task_id=?',(self.tid,))[0]['execution_token']
        return mcp_server.audited_call('deliver_proactive',{'candidate_ids':[task['candidate_id']],'body':'孩子家长您好，最近时间还方便吗？咱们先从这一课慢慢接着读，有困难也可以跟我说。','execution_token':token})

    def finish(self,result,request='s05-request'):
        return host_receipt.finish_delivery(self.tid,request,'deliver_proactive',result.get('action_key',''),json.dumps(result))

    def test_live_bundle_and_scope(self):
        self.begin();prepared=task_context.prepare(self.tid)
        data=json.loads(prepared['prompt'].split('<task_material>\n')[1].split('\n</task_material>')[0]);facts=data['facts']['learning_context']
        self.assertEqual(set(data['facts']),{'learning_context'})
        self.assertEqual([x['name'] for x in prepared['prepared_skills']],[service.RULE,'reply-style'])
        self.assertNotIn('due_at',data['task']);self.assertNotIn('service_task_finish',prepared['prompt'].split('<loaded_skill')[0])
        self.assertEqual(facts['learning']['dates'],['2025-10-20','2025-10-21'])
        self.assertEqual(len(facts['learning']['lessons']),2)
        self.assertEqual(facts['course']['bound_lesson_id'],facts['suggested_lesson']['lesson_id'])
        self.provider.return_value={'status':'ok','body':'更新后的正文','live_read':True}
        newer=learning_context.read();self.assertEqual(newer['course']['body'],'更新后的正文')
        self.assertNotEqual(newer['snapshot_id'],facts['snapshot_id'])
        with self.assertRaises(ValueError):learning_context.read('2025-10-20')

    def test_real_workflow_send_and_host_receipt(self):
        self.begin();task_context.prepare(self.tid)
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s05-send'}) as sender:
            result=self.send();self.assertEqual(result['status'],'accepted')
            self.assertEqual(self.finish(result)['status'],'awaiting_delivery');self.assertEqual(sender.call_count,1)
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE name IN ('check_proactive','service_task_finish')"))
        base.confirm_outbox();ap.sync()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(self.tid,))[0]['status'],'done')
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_pause_latest_status_no_send(self):
        self.begin();task_context.prepare(self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-21','pause','今天不联系',s.now()))
        wf.prepare_execution(self.tid)
        with patch.object(w,'http_send') as sender:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish(result)['status'],'no_contact');sender.assert_not_called()

    def test_completion_after_preload_rechecks(self):
        self.begin();task_context.prepare(self.tid)
        raw=json.loads(fq.FILE.read_text())
        next(x for x in raw['learning_daily'] if x['date']=='2025-10-21')['completed_at']='2025-10-21T18:50:00+08:00'
        fq.FILE.write_text(json.dumps(raw));wf.prepare_execution(self.tid)
        with patch.object(w,'http_send') as sender:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish(result)['status'],'no_contact');sender.assert_not_called()

    def test_unknown_delivery_not_resent_or_claimed_done(self):
        self.begin();task_context.prepare(self.tid)
        with patch.object(w,'http_send',side_effect=TimeoutError) as sender:
            result=self.send();self.assertEqual(result['status'],'unknown')
            self.assertEqual(self.finish(result)['status'],'blocked');self.assertEqual(sender.call_count,1)

    def test_forged_and_wrong_request_receipts(self):
        self.begin();task_context.prepare(self.tid)
        self.assertEqual(self.finish({'status':'sent','action_key':'forged'})['status'],'continue')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s05-send'}):result=self.send()
        self.assertEqual(self.finish(result,'wrong')['status'],'continue')

    def test_missing_data_no_course_and_no_contact(self):
        self.begin();task_context.prepare(self.tid)
        raw=json.loads(fq.FILE.read_text());raw['learning_daily']=[x for x in raw['learning_daily'] if x['date']!='2025-10-20'];fq.FILE.write_text(json.dumps(raw))
        value=learning_context.read();self.assertEqual(value['learning']['status'],'unknown');self.assertEqual(value['course']['status'],'not_requested')
        wf.prepare_execution(self.tid)
        with patch.object(w,'http_send') as sender:
            result=self.send();self.assertEqual(result['status'],'blocked');self.assertEqual(self.finish(result)['status'],'blocked');sender.assert_not_called()

    def test_frequency_and_time_windows(self):
        self.begin();task_context.prepare(self.tid);candidate=ap.active()['candidate_id']
        import datetime as dt
        self.assertEqual(proactive.check(candidate,dt.datetime.fromisoformat('2025-10-21T18:59:00+08:00'))['status'],'not_due')
        self.assertEqual(proactive.check(candidate,dt.datetime.fromisoformat('2025-10-21T21:01:00+08:00'))['status'],'expired')
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s05-send'}):result=self.send()
        self.finish(result)
        with closing(sqlite3.connect(s.DB)) as c:
            self.assertEqual(service.decision(c,tm.state(),tm.snapshot())['status'],'already_delivered')

    def test_actual_makeup_and_incomplete_schedule_are_not_zero(self):
        self.begin();snap=tm.snapshot();self.assertEqual(service.evidence(snap)[0],'eligible')
        changed=copy.deepcopy(snap);changed['lessons_as_of'][0]['completed_at']='2025-10-20T20:00:00+08:00'
        self.assertEqual(service.evidence(changed)[0],'not_eligible')
        changed=copy.deepcopy(snap);changed['weekly_learning']['current_week']['coverage_complete']=False
        self.assertEqual(service.evidence(changed)[0],'unknown')
        changed=copy.deepcopy(snap);changed['weekly_learning']['current_week']['lessons']=[]
        self.assertEqual(service.evidence(changed)[0],'not_eligible')

if __name__=='__main__':unittest.main()
