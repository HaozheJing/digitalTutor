import concurrent.futures,json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as fixtures
import sql_tools as s,test_mode as tm,agent_plan as ap,proactive as p,observability as audit
import completion_planner as cp,service_ledger as ledger,completion_events as events

class LedgerTests(unittest.TestCase):
    def setUp(self):
        # A developer may have enabled the mode for a separate native acceptance.
        # Establish this test's legacy starting state independently of that config.
        with patch.object(cp,'default_mode',return_value=False):
            fixtures.FixtureIntegrationTests.setUp(self)
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context
    def begin(self,at='2025-10-14 21:00'):
        with patch.object(cp,'default_mode',return_value=True):tm.activate(at,False)
        ap.enable()
        tasks=s.read("SELECT * FROM service_tasks WHERE kind='completion_plan' AND run_id=?",(tm.state()['run_id'],))
        self.assertTrue(tasks)
        task=tasks[-1]
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='running',started_at=? WHERE id=?",(s.now(),task['id']))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(task['id']),))
        prepared=ap.prompt(task['id']);self.assertEqual(prepared['prepared_skills'][0]['name'],'plan-after-lesson-completion')
        ctx=cp.context()
        return task,ctx
    def choices(self,ctx,code=None):
        result=[{'code':k,'decision':'skip','reason':'核对当前事实，本次不联系','evidence':[ctx['event']['event_id']]} for k in cp.RULES]
        if code:
            item=next(i for i in ctx['candidates'] if i['skill']==cp.RULES[code]);self.assertEqual(item['status'],'eligible')
            next(d for d in result if d['code']==code).update(decision='schedule',run_at=max(ctx['business_time'],item['time_window']['earliest']))
        return result
    def test_atomic_idempotent_four_decisions_and_preloaded_independent_skill(self):
        task,ctx=self.begin();choices=self.choices(ctx,'S02')
        self.assertIsNone(ap.load_skill('plan-after-lesson-completion')['required_shared_skill'])
        self.assertEqual(ap.load_skill('service-policy')['status'],'not_applicable')
        bad=choices[:-1]
        with self.assertRaises(ValueError):cp.commit('request-atomic',ctx['context_token'],bad)
        self.assertEqual(ledger.view()['cases'],[])
        result=cp.commit('request-atomic',ctx['context_token'],choices)
        self.assertEqual(result['status'],'committed')
        self.assertTrue(cp.commit('request-atomic',ctx['context_token'],choices)['duplicate'])
        self.assertEqual(len(ledger.view()['cases']),1)
        self.assertEqual(ap.finish(task['id'],'completed','四项已持久化')['status'],'done')
        ap.sync();self.assertEqual(len(s.read("SELECT * FROM service_tasks WHERE id LIKE 'service-ledger-%'")),1)
    def test_late_validation_rolls_back_every_task_case_and_plan(self):
        _,ctx=self.begin();choices=self.choices(ctx,'S02');choices[-1]['decision']='invalid'
        with self.assertRaises(ValueError):cp.commit('request-rollback',ctx['context_token'],choices)
        self.assertEqual(ledger.view()['cases'],[]);self.assertEqual(s.read('SELECT * FROM completion_plans'),[])
        self.assertEqual(s.read("SELECT * FROM service_tasks WHERE id LIKE 'service-ledger-%'"),[])
    def test_concurrent_commit_is_one_plan_and_one_task(self):
        _,ctx=self.begin();choices=self.choices(ctx,'S02')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:cp.commit('request-concurrent',ctx['context_token'],choices),range(2)))
        self.assertEqual(len(s.read('SELECT * FROM completion_plans')),1)
        self.assertEqual(sum(bool(r.get('duplicate')) for r in results),1)
    def test_stale_context_and_missing_evidence_rejected(self):
        _,ctx=self.begin();choices=self.choices(ctx,'S02');choices[0]['evidence']=[]
        with self.assertRaises(ValueError):cp.commit('request-evidence',ctx['context_token'],choices)
        state=tm.state();state['as_of']='2025-10-14T21:01:00+08:00'
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        with self.assertRaises(ValueError):cp.commit('request-stale',ctx['context_token'],self.choices(ctx,'S02'))
    def test_internal_tools_cannot_send_or_create_regular_tasks(self):
        self.begin();import mcp_server
        for name in ('reply_to_message','reply_with_highlights','deliver_proactive','deliver_message','deliver_festival','generate_image','service_schedule','save_plan','service_case_feedback'):
            with self.subTest(name=name),self.assertRaises(ValueError):mcp_server.business_call(name,{})
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])
    def test_accepted_is_not_completed_and_late_sent_resolves_once(self):
        _,ctx=self.begin();cp.commit('request-receipt',ctx['context_token'],self.choices(ctx,'S02'))
        case=ledger.view()['cases'][0];task_id=case['tasks'][0]['id']
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('receipt','body','accepted',s.now(),'test'))
            c.execute('INSERT INTO service_links VALUES(?,?)',(task_id,'receipt'))
            ledger.reconcile(c,tm.state())
        self.assertEqual(ledger.view()['cases'][0]['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE deliveries SET status='sent'");ledger.reconcile(c,tm.state());ledger.reconcile(c,tm.state())
        case=ledger.view()['cases'][0];self.assertEqual(case['status'],'completed')
        self.assertEqual(len([t for t in case['timeline'] if t['kind']=='delivery']),2)
    def makeup(self):
        task,ctx=self.begin('2025-10-27 21:00');state=tm.state();event=ctx['event']
        raw=tm.fixture_data(state);missing=[l['id'] for l in raw['learning_daily'] if not l.get('completed_at')][:2]
        if len(missing)<2:missing=['goal-one','goal-two']
        with closing(sqlite3.connect(s.DB)) as c,c:
            case_id=ledger.case_for(c,state,event,'S03',missing)
            self.assertEqual(case_id,ledger.case_for(c,state,event,'S03',missing))
        return case_id,missing,ctx
    def test_partial_goals_and_resolution_cancel_pending_without_contact(self):
        case_id,goals,ctx=self.makeup();state=tm.state()
        with closing(sqlite3.connect(s.DB)) as c,c:
            cp.insert(c,state,'service-test-goal','signal',cp.RULES['S03'],state['as_of'],state['as_of'],None,'test')
            c.execute('INSERT INTO service_case_tasks VALUES(?,?,?,?)',('service-test-goal',case_id,'event','S03'))
            for i,lesson in enumerate(goals):
                c.execute('INSERT INTO completion_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',(state['run_id'],'test','goal-'+str(i),state['uid'],ctx['event']['enrollment_id'],lesson,'goal-'+str(i),state['as_of'],s.now(),state['as_of'],'{}'))
                ledger.progress(c,state)
                status=c.execute('SELECT status FROM service_cases WHERE id=?',(case_id,)).fetchone()[0]
                self.assertEqual(status,'open' if i==0 else 'resolved')
            self.assertEqual(c.execute("SELECT status FROM service_tasks WHERE id='service-test-goal'").fetchone()[0],'cancelled')
    def message(self,source,body):
        with closing(sqlite3.connect(s.DB)) as c,c:
            columns=[r[1] for r in c.execute('PRAGMA table_info(messages)')]
            values={'source_id':source,'speaker':'user','body':body,'sha256':'hash-'+source,'created_at':s.now(),'source':'wechat','visible':1,'channel':'wechat'}
            c.execute('INSERT INTO messages('+','.join(k for k in values if k in columns)+') VALUES('+','.join('?' for k in values if k in columns)+')',[v for k,v in values.items() if k in columns])
    def test_feedback_source_revision_retry_pause_and_intent(self):
        case_id,goals,ctx=self.makeup();self.message('msg-pause','先别提醒，周末补')
        case=ledger.view()['cases'][0]
        with self.assertRaises(ValueError):ledger.feedback(case_id,case['revision'],'request-bad','msg-pause','已经全部完成','intent')
        result=ledger.feedback(case_id,case['revision'],'request-pause','msg-pause','先别提醒','pause')
        self.assertEqual(result['status'],'paused')
        self.assertEqual(ledger.feedback(case_id,case['revision'],'request-pause','msg-pause','先别提醒','pause'),result)
        with self.assertRaises(ValueError):ledger.feedback(case_id,case['revision'],'request-stale','msg-pause','周末补','intent')
        ledger.feedback(case_id,result['revision'],'request-intent','msg-pause','周末补','intent')
        self.assertTrue(all(g['completed_event'] is None for g in ledger.view()['cases'][0]['goals']))
        self.message('msg-new','谢谢')
        with self.assertRaises(ValueError):ledger.feedback(case_id,result['revision']+1,'request-old','msg-pause','先别提醒','resume')
        self.assertEqual(ledger.view()['constraints'][0]['status'],'paused')
    def test_read_only_clear_context_new_run_and_history(self):
        task,ctx=self.begin();cp.commit('request-history',ctx['context_token'],self.choices(ctx,'S02'));ap.finish(task['id'],'completed','已完成规划')
        run=tm.state()['run_id'];before=s.read('SELECT * FROM service_tasks')
        import workbench
        self.assertEqual(len(workbench.inspection()['service_ledger']['cases']),1)
        ledger.view();ledger.view();self.assertEqual(s.read('SELECT * FROM service_tasks'),before)
        import console_control
        console_control.clear_context();self.assertEqual(len(ledger.view()['cases']),1)
        tm.advance('2025-10-14 22:00');self.assertEqual(len(ledger.view()['cases']),1)
        tm.activate('2025-10-15 21:00',False);self.assertEqual(ledger.view()['cases'],[])
        self.assertEqual(len(ledger.view(run)['cases']),1)
    def test_legacy_default_does_not_change_live_run(self):
        self.assertFalse(cp.enabled());self.assertEqual(tm.state().get('completion_planning_mode'),'legacy')
        with patch.object(cp,'default_mode',return_value=True):
            self.assertFalse(cp.enabled())
            tm.activate('2025-10-14 21:00',False)
        self.assertTrue(cp.enabled())

    def scenario(self,at):
        import fixture_query as fq
        raw=tm.fixture_data(tm.state(),include_events=False)
        ordered=sorted(raw['learning_daily'],key=lambda x:x['id'])
        today=next(l for l in ordered if l['date']=='2025-10-30')
        index=ordered.index(today)
        for lesson in ordered[index-2:index]:lesson['completed_at']=None
        ordered[index-3]['completed_at']=ordered[index-3]['date']+'T19:00:00+08:00'
        today['completed_at']='2025-10-30T19:45:00+08:00'
        source=self.root/'scenario.json';source.write_text(json.dumps(raw))
        override=patch.object(fq,'FILE',source);override.start();self.addCleanup(override.stop)
        return self.begin(at)
    def test_s06_schedules_with_goals_and_s03_does_not_duplicate(self):
        _,ctx=self.scenario('2025-10-30 20:00')
        cp.commit('request-s06',ctx['context_token'],self.choices(ctx,'S06'))
        case=ledger.view()['cases'][0];self.assertEqual(case['kind'],'makeup');self.assertEqual(len(case['goals']),2)
        self.assertEqual(next(x for x in ctx['candidates'] if x['skill']==cp.RULES['S03'])['status'],'merged_into_higher_priority')
    def test_s03_schedules_after_s06_window_and_cancel_does_not_resolve(self):
        task,ctx=self.scenario('2025-10-30 20:31')
        cp.commit('request-s03',ctx['context_token'],self.choices(ctx,'S03'));ap.finish(task['id'],'completed','已安排')
        case=ledger.view()['cases'][0];task_id=case['tasks'][0]['id']
        with closing(sqlite3.connect(s.DB)) as c,c:ledger.cancel_pending(c,case['id'],'当前不需要执行')
        cp.sync();self.assertEqual(ledger.view()['cases'][0]['status'],'cancelled')
        self.assertTrue(all(g['completed_event'] is None for g in ledger.view()['cases'][0]['goals']))
    def test_s04_highlight_precedence_and_separate_streak(self):
        task,ctx=self.begin('2025-10-15 20:00')
        self.assertEqual(next(x for x in ctx['candidates'] if x['skill']==cp.RULES['S04'])['status'],'merged_into_higher_priority')
        cp.commit('request-covered',ctx['context_token'],self.choices(ctx));ap.finish(task['id'],'completed','原文原图高光覆盖')
        task,ctx=self.begin('2025-10-22 20:00')
        cp.commit('request-streak',ctx['context_token'],self.choices(ctx,'S04'))
        self.assertEqual(ledger.view()['cases'][0]['kind'],'S04')
    def test_action_due_uses_published_skill_and_host_receipt(self):
        task,ctx=self.begin();result=cp.commit('request-action',ctx['context_token'],self.choices(ctx,'S02'));ap.finish(task['id'],'completed','规划完成')
        action=next(x['task_id'] for x in result['decisions'] if x['code']=='S02')
        import console_control,mcp_server,host_receipt,service_content
        console_control.execute(action);claimed=ap.claim();self.assertEqual(claimed['id'],action)
        ap.dispatched(action,'native-request')
        prepared=ap.prompt(action)
        self.assertEqual(prepared['prepared_skills'][0]['bundle_sha256'],service_content.bundle_digest(cp.RULES['S02']))
        self.assertIn('reply-style',[x['name'] for x in prepared['prepared_skills']])
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('late-receipt','正文','accepted',s.now(),'isolated'))
            c.execute('INSERT INTO service_links VALUES(?,?)',(action,'late-receipt'))
        self.assertEqual(host_receipt.finish_delivery(action,'native-request','deliver_proactive','late-receipt')['status'],'awaiting_delivery')
        cp.sync();self.assertEqual(ledger.view()['cases'][0]['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent' WHERE action_key='late-receipt'")
        ap.sync();self.assertEqual(ledger.view()['cases'][0]['status'],'completed')
        self.assertEqual(s.read('SELECT status FROM service_tasks WHERE id=?',(action,))[0]['status'],'done')
    def test_historical_manual_review_remains_internal_and_does_not_recur(self):
        task,ctx=self.scenario('2025-10-30 20:31');result=cp.commit('request-review-plan',ctx['context_token'],self.choices(ctx,'S03'));ap.finish(task['id'],'completed','已安排')
        case=ledger.view()['cases'][0];action=case['tasks'][0]['id']
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('makeup-receipt','正文','sent',s.now(),'isolated'))
            c.execute('INSERT INTO service_links VALUES(?,?)',(action,'makeup-receipt'));ledger.reconcile(c,tm.state());ledger.reconcile(c,tm.state())
            self.assertEqual(c.execute("SELECT count(*) FROM service_tasks WHERE kind='case_review'").fetchone()[0],0)
            cp.insert(c,tm.state(),'historical-manual-review','case_review','review-service-case',tm.state()['as_of'],tm.state()['as_of'],case['id'],'历史内部检查兼容')
            c.execute('INSERT INTO service_case_tasks VALUES(?,?,?,?)',('historical-manual-review',case['id'],'','review'))
        case=ledger.view()['cases'][0];reviews=[x for x in case['tasks'] if x['kind']=='case_review'];self.assertEqual(len(reviews),1)
        review=reviews[0]
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='running' WHERE id=?",(review['id'],))
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(review['id']),))
        ap.dispatched(review['id'],'native-review');ap.prompt(review['id'])
        audit.invoke('service_case_review',{},lambda:cp.review('无新增完课事实，保持等待，不联系'))
        ap.finish(review['id'],'no_contact','内部检查完成')
        import mcp_server
        with self.assertRaises(ValueError):mcp_server.business_call('reply_to_message',{})
        cp.sync();self.assertEqual(len([x for x in ledger.view()['cases'][0]['tasks'] if x['kind']=='case_review']),1)
    def test_s03_s06_sent_complete_one_task_and_never_schedule_seven_day_review(self):
        for code,at in [('S06','2025-10-30 20:00'),('S03','2025-10-30 20:31')]:
            with self.subTest(code=code):
                task,ctx=self.scenario(at)
                cp.commit('request-single-'+code,ctx['context_token'],self.choices(ctx,code))
                ap.finish(task['id'],'completed','已安排')
                case=next(x for x in ledger.view()['cases'] if any(y['code']==code for y in x['tasks']));action=case['tasks'][0]['id'];case_id=case['id']
                import console_control,host_receipt
                console_control.execute(action);self.assertEqual(ap.claim()['id'],action)
                ap.dispatched(action,'single-native-'+code);ap.prompt(action)
                with closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('single-receipt','正文','accepted',s.now(),'isolated'))
                    c.execute('INSERT INTO service_links VALUES(?,?)',(action,'single-receipt'))
                    ledger.reconcile(c,tm.state())
                self.assertEqual(host_receipt.finish_delivery(action,'single-native-'+code,'deliver_proactive','single-receipt')['status'],'awaiting_delivery')
                self.assertEqual(next(x for x in ledger.view()['cases'] if x['id']==case_id)['status'],'awaiting_delivery')
                with closing(sqlite3.connect(s.DB)) as c,c:
                    c.execute("UPDATE deliveries SET status='sent' WHERE action_key='single-receipt'")
                ap.sync();cp.sync();cp.sync()
                case=next(x for x in ledger.view()['cases'] if x['id']==case_id)
                self.assertEqual(case['status'],'completed');self.assertEqual(len(case['tasks']),1)
                self.assertEqual(case['tasks'][0]['status'],'done')
                self.assertTrue(all(g['completed_event'] is None for g in case['goals']))
                tm.advance('2025-11-06 21:00');cp.sync()
                case=next(x for x in ledger.view()['cases'] if x['id']==case_id)
                self.assertEqual(case['status'],'completed');self.assertEqual(len(case['tasks']),1)
                self.assertIsNone(case['next_review'])
                self.assertEqual(s.read("SELECT * FROM service_tasks WHERE kind='case_review'"),[])
                self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_distinct_actions_do_not_reuse_or_expand_makeup_matter(self):
        case_id,goals,ctx=self.makeup();state=tm.state()
        with closing(sqlite3.connect(s.DB)) as c,c:
            other=ledger.case_for(c,state,ctx['event'],'S06',['different-goal'])
            next_event={**ctx['event'],'event_id':'next-event'}
            later=ledger.case_for(c,state,next_event,'S03',['later-goal'])
            self.assertEqual(len({case_id,other,later}),3)
            self.assertEqual({x[0] for x in c.execute('SELECT lesson_id FROM service_case_goals WHERE case_id=?',(case_id,))},set(goals))

    def test_keep_reuses_task_without_creating_matter_or_adding_goals(self):
        task,ctx=self.scenario('2025-10-30 20:31')
        cp.commit('request-original-s03',ctx['context_token'],self.choices(ctx,'S03'))
        ap.finish(task['id'],'completed','已安排')
        before=ledger.view()['cases'][0];state=tm.state()
        with closing(sqlite3.connect(s.DB)) as c,c:
            cp.insert(c,state,'recheck-plan','completion_plan','plan-after-lesson-completion',state['as_of'],state['as_of'],task['candidate_id'],'再次核对')
            c.execute("UPDATE service_tasks SET status='running' WHERE id='recheck-plan'")
            c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded('recheck-plan'),))
        facts=cp.facts
        def changed(c,task,state):
            value=facts(c,task,state)
            for item in value['candidates']:
                if item['skill']==cp.RULES['S03']:
                    item['evidence']['overdue_lessons'].append({'lesson_id':'new-unrelated-goal'})
            return value
        with patch.object(cp,'facts',side_effect=changed):
            ap.prompt('recheck-plan');ctx=cp.context();choices=self.choices(ctx)
            next(x for x in choices if x['code']=='S03')['decision']='keep'
            cp.commit('request-keep-s03',ctx['context_token'],choices)
        cases=ledger.view()['cases'];self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]['id'],before['id']);self.assertEqual(cases[0]['goals'],before['goals'])
        self.assertEqual(cases[0]['tasks'],before['tasks'])

    def test_upgrade_cancels_only_pending_automatic_reviews_and_preserves_history(self):
        case_id,goals,ctx=self.makeup();state=tm.state()
        with closing(sqlite3.connect(s.DB)) as c,c:
            for status in ['planned','deferred','done']:
                task_id='service-review-'+status
                cp.insert(c,state,task_id,'case_review','review-service-case',state['as_of'],state['as_of'],case_id,'原七天复查')
                c.execute('UPDATE service_tasks SET status=? WHERE id=?',(status,task_id))
                c.execute('INSERT INTO service_case_tasks VALUES(?,?,?,?)',(task_id,case_id,'','review'))
            self.assertEqual(ledger.cancel_automatic_reviews(c,'unrelated-run'),[])
            self.assertEqual(set(ledger.cancel_automatic_reviews(c,state['run_id'])),{'service-review-planned','service-review-deferred'})
            self.assertEqual(ledger.cancel_automatic_reviews(c,state['run_id']),[])
        statuses={x['id']:x['status'] for x in ledger.view()['cases'][0]['tasks']}
        self.assertEqual(statuses,{'service-review-planned':'cancelled','service-review-deferred':'cancelled','service-review-done':'done'})
        self.assertEqual(len([x for x in ledger.view()['cases'][0]['timeline'] if x['kind']=='task_cancelled']),2)
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_completed_delivery_keeps_status_when_learning_progress_arrives(self):
        case_id,goals,ctx=self.makeup();state=tm.state()
        with closing(sqlite3.connect(s.DB)) as c,c:
            ledger.change(c,case_id,'completed')
            for i,lesson in enumerate(goals):
                c.execute('INSERT INTO completion_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',(state['run_id'],'test','later-'+str(i),state['uid'],ctx['event']['enrollment_id'],lesson,'later-'+str(i),state['as_of'],s.now(),state['as_of'],'{}'))
            ledger.progress(c,state)
        case=ledger.view()['cases'][0]
        self.assertEqual(case['status'],'completed')
        self.assertTrue(all(g['completed_event'] for g in case['goals']))
        self.assertEqual(case['tasks'],[])

    def test_completion_post_contract_dedupe_conflict_and_concurrent_retry(self):
        import fixture_query as fq
        raw=tm.fixture_data(tm.state(),include_events=False);lesson=next(x for x in raw['learning_daily'] if x['date']=='2025-10-24');lesson['completed_at']=None
        source=self.root/'event-fixture.json';source.write_text(json.dumps(raw));override=patch.object(fq,'FILE',source);override.start();self.addCleanup(override.stop)
        tm.activate('2025-10-24 20:30',False);state=tm.state()
        payload={'schema_version':1,'event_type':'lesson.completed','event_id':'api-event-1','completion_id':'course-record-1','uid':state['uid'],'enrollment_id':events.enrollment(raw,lesson)['id'],'lesson_id':lesson['id'],'completed_at':'2025-10-24T20:00:00+08:00'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:events.accept(payload,state['run_id']),range(2)))
        self.assertEqual(sorted(x['status'] for x in results),['duplicate','recorded'])
        self.assertEqual(len(events.saved(state)),1)
        with self.assertRaises(ValueError):events.accept({**payload,'completed_at':'2025-10-24T20:01:00+08:00'},state['run_id'])
        with self.assertRaises(ValueError):events.accept(payload,'wrong-run')

if __name__=='__main__':unittest.main()
