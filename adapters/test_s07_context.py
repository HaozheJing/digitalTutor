import json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as base
import sql_tools as s,test_mode as tm,agent_plan as ap,service_workflow as wf
import fixture_query as fq,s07_service as service,task_context,learning_context
import course_service,host_receipt,mcp_server,wechat_test as w

class S07ContextTests(unittest.TestCase):
    setUp=base.FixtureIntegrationTests.setUp
    tearDown=base.FixtureIntegrationTests.tearDown
    create_source=base.FixtureIntegrationTests.create_source
    set_context=base.FixtureIntegrationTests.set_context

    def rhythm_fixture(self):
        from reply_lab_presets import build
        original=json.loads(fq.FILE.read_text())
        fixture=next(x for x in build(original) if x['id']==service.RULE)['payload']['native_fixture']
        self.assertEqual(fixture['profile'],original['profile'])
        fq.FILE.write_text(json.dumps(fixture))

    def begin(self):
        self.rhythm_fixture()
        with patch.object(wf,'default_mode',return_value=True):tm.activate('2025-10-22 06:30',False)
        ap.enable();wf.initialize();parent=ap.claim();self.assertEqual(parent['kind'],'plan')
        ctx=wf.context();contract=next(x for x in ctx['catalog'] if x['skill']==service.RULE)
        options=[x for x in contract['opportunities'] if x['status']=='eligible'];self.assertEqual(len(options),1)
        choices=[{'skill':x['skill'],'decision':'skip','reason':'S07 test only'} for x in ctx['catalog']]
        next(x for x in choices if x['skill']==service.RULE).update(decision='create',opportunity_id=options[0]['id'],run_at=contract['timing']['at'])
        result=wf.commit('s07-test-plan',ctx['context_token'],choices)
        self.tid=next(x['task_id'] for x in result['decisions'] if x.get('skill')==service.RULE)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='done' WHERE id=?",(parent['id'],))
            c.execute("DELETE FROM metadata WHERE key='active_service_task'")
        tm.advance('2025-10-22 19:00')
        state=tm.state();state.update(auto_highlights=True,manual_service_task_id=self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        task=ap.claim();self.assertEqual(task['id'],self.tid);wf.prepare_execution(self.tid)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE service_tasks SET native_request_id='s07-request' WHERE id=?",(self.tid,))
        provider=patch.object(course_service,'course_knowledge',return_value={'status':'ok','body':'故事里一只狐狸想吃到树上的葡萄','live_read':True})
        self.provider=provider.start();self.addCleanup(provider.stop)

    def test_family_action_uses_persona_and_can_deliver_without_a_style_read(self):
        import family_lab_bridge
        self.begin()
        with patch.object(family_lab_bridge,'configuration',return_value={'family_id':'F01'}), patch.object(family_lab_bridge,'verify',return_value=None), patch.object(family_lab_bridge,'connection',return_value=(None,None)):
            packet=ap.prompt(self.tid)
            self.assertEqual([r['name'] for r in packet['prepared_skills']],[service.RULE])
            self.assertNotIn('<loaded_skill name="reply-style"',packet['prompt'])
            self.assertEqual(ap.load_skill('reply-style')['status'],'not_applicable')
            with self.assertRaises(ap.ServiceValidationError):
                ap.require_skills('reply-style')
            self.inquire()
            with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'isolated-active'}) as sender:
                self.assertEqual(self.send()['status'],'accepted')
                self.assertEqual(sender.call_count,1)

    def material(self):
        prepared=task_context.prepare(self.tid)
        self.assertEqual([x['name'] for x in prepared['prepared_skills']],[service.RULE,'reply-style'])
        prompt=prepared['prompt']
        return json.loads(prompt.split('<task_material>\n')[1].split('\n</task_material>')[0])

    def inquire(self, lesson_no=7):
        family=mcp_server.audited_call('family_profile',{})
        learning=mcp_server.audited_call('learning_query',{'day':'2025-10-22'})
        mcp_server.audited_call('daily_context',{'day':'2025-10-22'})
        candidates=mcp_server.audited_call('action_candidates',{'day':'2025-10-22'})
        rows=candidates.get('actions') or candidates.get('candidates',[])
        self.candidate=next(x['id'] for x in rows if x['skill']==service.RULE)
        mcp_server.audited_call('course_knowledge',{'class_type':family['class_type'],'lesson_no':lesson_no})
        return family,learning

    def test_host_does_not_select_or_read_course(self):
        self.begin();data=self.material()
        self.assertEqual(data['facts'],{})
        self.provider.assert_not_called()
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE task_id=? AND name IN ('learning_context','family_profile','learning_query','daily_context','action_candidates','course_knowledge')",(self.tid,)))
        with self.assertRaises(ap.ServiceValidationError):ap.guard_delivery()

    def test_model_can_choose_another_current_week_lesson(self):
        self.begin();self.material();family,learning=self.inquire(lesson_no=7)
        self.assertEqual(family['child_nickname'],'侨侨')
        self.assertEqual(learning['weekly_learning']['previous_week']['completed'],2)
        self.assertEqual(learning['weekly_learning']['current_week']['completed'],0)
        self.provider.assert_called_once_with(class_type=family['class_type'],lesson_no=7)
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE task_id=? AND name='learning_context'",(self.tid,)))
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s07-choice'}) as sender:
            result=self.send();self.assertEqual(result['status'],'accepted');self.assertEqual(sender.call_count,1)

    def send(self):
        candidate=self.candidate
        token=s.read('SELECT execution_token FROM service_task_versions WHERE task_id=?',(self.tid,))[0]['execution_token']
        return mcp_server.audited_call('deliver_proactive',{'candidate_ids':[candidate],'body':'侨侨妈妈，方便时提醒侨侨从这节接着读吧。','execution_token':token})

    def finish(self,result,request='s07-request'):
        return host_receipt.finish_delivery(self.tid,request,'deliver_proactive',result.get('action_key',''),json.dumps(result))

    def test_optional_aggregate_remains_live_when_explicitly_requested(self):
        self.begin();data=self.material();self.assertEqual(data['facts'],{})
        facts=learning_context.read()
        self.assertEqual(facts['family']['child_nickname'],'侨侨')
        self.assertEqual(facts['family']['guardian_role'],'mother')
        self.assertEqual(facts['learning']['previous_week']['completed'],2)
        self.assertEqual(facts['learning']['current_week']['completed'],0)
        self.assertEqual(facts['suggested_lesson']['scheduled_date'],'2025-10-20')
        self.assertEqual(facts['course']['bound_lesson_id'],facts['suggested_lesson']['lesson_id'])
        self.provider.return_value={'status':'ok','body':'更新后的故事正文','live_read':True}
        newer=learning_context.read();self.assertEqual(newer['course']['body'],'更新后的故事正文')
        self.assertNotEqual(newer['snapshot_id'],facts['snapshot_id'])
        with self.assertRaises(ValueError):learning_context.read('2025-10-21')

    def test_delivery_and_host_receipt_without_model_finish(self):
        self.begin();self.material();self.inquire()
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s07-send'}) as sender:
            result=self.send();self.assertEqual(result['status'],'accepted')
            self.assertEqual(self.finish(result)['status'],'awaiting_delivery');self.assertEqual(sender.call_count,1)
        self.assertFalse(s.read("SELECT 1 FROM tool_audit WHERE name IN ('check_proactive','service_task_finish')"))
        base.confirm_outbox();ap.sync()
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(self.tid,))[0]['status'],'done')
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_pause_after_preload_never_sends(self):
        self.begin();self.material();self.inquire()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-22','pause','今天不联系',s.now()))
        wf.prepare_execution(self.tid)
        with patch.object(w,'http_send') as sender:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish(result)['status'],'no_contact');sender.assert_not_called()

    def test_completion_after_preload_no_longer_eligible(self):
        self.begin();self.material();self.inquire();raw=json.loads(fq.FILE.read_text())
        next(x for x in raw['learning_daily'] if x['date']=='2025-10-20')['completed_at']='2025-10-22T18:50:00+08:00'
        fq.FILE.write_text(json.dumps(raw));wf.prepare_execution(self.tid)
        value=learning_context.read();self.assertEqual(value['learning']['status'],'not_eligible')
        self.assertEqual(value['course']['status'],'not_requested')
        with patch.object(w,'http_send') as sender:
            result=self.send();self.assertEqual(result['status'],'skipped')
            self.assertEqual(self.finish(result)['status'],'no_contact');sender.assert_not_called()

    def test_unknown_delivery_and_forged_receipts(self):
        self.begin();self.material();self.inquire()
        self.assertEqual(self.finish({'status':'sent','action_key':'forged'})['status'],'continue')
        with patch.object(w,'http_send',side_effect=TimeoutError) as sender:
            result=self.send();self.assertEqual(result['status'],'unknown')
            self.assertEqual(self.finish(result,'wrong')['status'],'continue')
            self.assertEqual(self.finish(result)['status'],'blocked');self.assertEqual(sender.call_count,1)

    def test_incomplete_week_is_not_zero(self):
        self.begin();self.material();raw=json.loads(fq.FILE.read_text())
        raw['learning_daily']=[x for x in raw['learning_daily'] if x['date']!='2025-10-14'];fq.FILE.write_text(json.dumps(raw))
        value=learning_context.read();self.assertEqual(value['learning']['status'],'unknown')
        self.assertEqual(value['course']['status'],'not_requested')

if __name__=='__main__':unittest.main()
