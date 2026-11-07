import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as base
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf
import fixture_query as fq,s08_service as service,task_context,learning_context
import course_service,host_receipt,mcp_server,wechat_test as w

class S08ContextTests(unittest.TestCase):
    setUp=base.FixtureIntegrationTests.setUp
    tearDown=base.FixtureIntegrationTests.tearDown
    create_source=base.FixtureIntegrationTests.create_source
    set_context=base.FixtureIntegrationTests.set_context

    def closing_fixture(self):
        from reply_lab_presets import build
        original=json.loads(fq.FILE.read_text())
        fixture=next(x for x in build(original) if x['id']==service.RULE)['payload']['native_fixture']
        self.assertEqual(fixture['profile'],original['profile'])
        fq.FILE.write_text(json.dumps(fixture))

    def begin(self):
        self.closing_fixture()
        with patch.object(wf,'default_mode',return_value=True):tm.activate('2025-10-17 06:30',False)
        ap.enable();wf.initialize();parent=ap.claim();self.assertEqual(parent['kind'],'plan')
        ctx=wf.context();contract=next(x for x in ctx['catalog'] if x['skill']==service.RULE)
        options=[x for x in contract['opportunities'] if x['status']=='eligible'];self.assertEqual(len(options),1)
        choices=[{'skill':x['skill'],'decision':'skip','reason':'S08 test only'} for x in ctx['catalog']]
        next(x for x in choices if x['skill']==service.RULE).update(decision='create',opportunity_id=options[0]['id'],run_at=contract['timing']['at'])
        result=wf.commit('s08-test-plan',ctx['context_token'],choices)
        self.tid=next(x['task_id'] for x in result['decisions'] if x.get('skill')==service.RULE)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='done' WHERE id=?",(parent['id'],))
            c.execute("DELETE FROM metadata WHERE key='active_service_task'")
        tm.advance('2025-10-17 19:00')
        state=tm.state();state.update(auto_highlights=True,manual_service_task_id=self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        task=ap.claim();self.assertEqual(task['id'],self.tid);wf.prepare_execution(self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET native_request_id='s08-request' WHERE id=?",(self.tid,))
        provider=patch.object(course_service,'course_knowledge',return_value={'status':'ok','body':'故事里一只狐狸想吃到树上的葡萄','live_read':True})
        self.provider=provider.start();self.addCleanup(provider.stop)

    def material(self):
        prepared=task_context.prepare(self.tid)
        self.assertEqual([x['name'] for x in prepared['prepared_skills']],[service.RULE,'reply-style'])
        data=json.loads(prepared['prompt'].split('<task_material>\n')[1].split('\n</task_material>')[0])
        self.candidate=data['facts']['learning_context']['candidate']['id']
        return data['facts']['learning_context']

    def send(self):
        token=s.read('SELECT execution_token FROM service_task_versions WHERE task_id=?',(self.tid,))[0]['execution_token']
        return mcp_server.audited_call('deliver_proactive',{'candidate_ids':[self.candidate],'body':'侨侨妈妈，这周前面的课都读完啦，方便时提醒侨侨接着读今天的故事吧。','execution_token':token})

    def finish(self,result,request='s08-request'):
        return host_receipt.finish_delivery(self.tid,request,'deliver_proactive',result.get('action_key',''),json.dumps(result))

    def test_preload_has_matching_week_family_schedule_and_live_body(self):
        self.begin();facts=self.material()
        self.assertEqual(facts['family']['child_nickname'],'侨侨')
        self.assertEqual(facts['family']['guardian_role'],'mother')
        self.assertEqual(facts['learning']['current_week']['completed'],4)
        self.assertEqual(facts['suggested_lesson']['scheduled_date'],'2025-10-17')
        self.assertEqual(facts['suggested_lesson']['lesson_id'],facts['schedule']['lesson_id'])
        self.assertEqual(facts['course']['bound_lesson_id'],facts['suggested_lesson']['lesson_id'])
        self.provider.assert_called_once_with(class_type=facts['family']['class_type'],lesson_no=5)
        ap.guard_delivery([__import__('business').original(self.candidate)])
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE origin='model' AND name='course_knowledge'"))
        self.provider.return_value={'status':'ok','body':'本次更新的真实正文','live_read':True}
        fresh=learning_context.read();self.assertEqual(fresh['course']['body'],'本次更新的真实正文')
        self.assertNotEqual(fresh['snapshot_id'],facts['snapshot_id'])
        with self.assertRaises(ValueError):learning_context.read('2025-10-16')

    def test_delivery_and_host_receipt_without_model_finish(self):
        self.begin();self.material()
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s08-send'}) as sender:
            result=self.send();self.assertEqual(result['status'],'accepted')
            self.assertEqual(self.finish(result)['status'],'awaiting_delivery');self.assertEqual(sender.call_count,1)
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE name IN ('check_proactive','service_task_finish')"))
        base.confirm_outbox();ap.sync()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(self.tid,))[0]['status'],'done')
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_pause_after_preload_never_sends(self):
        self.begin();self.material()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-17','pause','今天不联系',s.now()))
        wf.prepare_execution(self.tid)
        with patch.object(w,'http_send') as sender:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish(result)['status'],'no_contact');sender.assert_not_called()

    def test_completion_after_preload_no_longer_eligible(self):
        self.begin();self.material();raw=json.loads(fq.FILE.read_text())
        next(x for x in raw['learning_daily'] if x['date']=='2025-10-17')['completed_at']='2025-10-17T18:50:00+08:00'
        fq.FILE.write_text(json.dumps(raw));wf.prepare_execution(self.tid)
        with patch.object(w,'http_send') as sender:
            result=self.send();self.assertEqual(result['status'],'skipped')
            value=learning_context.read();self.assertEqual(value['learning']['status'],'not_eligible')
            self.assertEqual(value['course']['status'],'not_requested')
            self.assertEqual(self.finish(result)['status'],'no_contact');sender.assert_not_called()

    def test_unknown_delivery_and_forged_receipts(self):
        self.begin();self.material()
        self.assertEqual(self.finish({'status':'sent','action_key':'forged'})['status'],'continue')
        with patch.object(w,'http_send',side_effect=TimeoutError) as sender:
            result=self.send();self.assertEqual(result['status'],'unknown')
            self.assertEqual(self.finish(result,'wrong')['status'],'continue')
            self.assertEqual(self.finish(result)['status'],'blocked');self.assertEqual(sender.call_count,1)

    def test_incomplete_week_is_not_single_gap(self):
        self.begin();self.material();raw=json.loads(fq.FILE.read_text())
        raw['learning_daily']=[x for x in raw['learning_daily'] if x['date']!='2025-10-14'];fq.FILE.write_text(json.dumps(raw))
        value=learning_context.read();self.assertEqual(value['learning']['status'],'unknown')
        self.assertEqual(value['course']['status'],'not_requested')

    def test_missing_or_mismatched_course_cannot_deliver(self):
        self.begin()
        self.provider.return_value={'status':'unavailable','reason':'未读取到正文'}
        self.material()
        with patch.object(w,'http_send') as sender:
            with self.assertRaises(ap.ServiceValidationError):self.send()
            sender.assert_not_called()
        plan=course_service.course_schedule(date='2025-10-17',class_type=tm.snapshot()['profile']['class_type'],cohort=tm.snapshot()['profile']['cohort'])
        self.provider.reset_mock()
        with patch.object(course_service,'course_schedule',return_value={**plan,'lesson_no':999}):
            fresh=learning_context.read();self.assertEqual(fresh['course']['status'],'not_requested')
        self.provider.assert_not_called()

if __name__=='__main__':unittest.main()
