import json,copy,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as fixtures
import sql_tools as s,test_mode as tm,fixture_query as fq,agent_plan as ap,service_workflow as wf,completion_events as events,s11_service as svc,completion_service as cs,completion_planner as cp,proactive as proactive

class S11Tests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def fixture(self,count=5,return_lesson=6,stamp='2025-10-20T19:10:00+08:00',done=True):
        data=json.loads(fq.FILE.read_text());self.original_profile=copy.deepcopy(data['profile'])
        changed={l['id'] for l in data['learning_daily'] if l['lesson_no']<=count or l['lesson_no'] in (return_lesson,6)}
        for row in data['learning_daily']:
            if row['id'] in changed:
                row.update(attended_at=None,completed_at=None,checked_in_at=None)
            if row['lesson_no']==return_lesson:row.update(attended_at=stamp,completed_at=stamp if done else None)
        for key in ['learning_events','quiz_attempts']:data[key]=[x for x in data[key] if x.get('lesson_id') not in changed]
        data['highlight_assets']=[];data['weekly_rankings']=[]
        fq.FILE.write_text(json.dumps(data));return data

    def now(self,at='2025-10-20 19:40'):
        tm.activate(at,False);ap.initialize();wf.initialize();return tm.state()

    def items(self):
        with closing(sqlite3.connect(s.DB)) as c:return svc.decisions(c,tm.state(),tm.snapshot(),tm.fixture_data(include_events=False))

    def enable(self):
        state=tm.state();state.update(agent_planning=True,service_workflow_mode=wf.MODE)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        ap.sync()

    def clock(self,at):
        state=tm.state();state['as_of']=at
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))

    def test_five_before_catchup_four_after_is_eligible(self):
        self.fixture(return_lesson=1);self.now('2025-10-20 19:40');item=next(x for x in self.items() if x['evidence']['lesson']['lesson_no']==1)
        self.assertEqual(item['status'],'eligible');v=item['evidence']['return_to_learning'];self.assertEqual((v['before_missing_count'],v['current_missing_count']),(5,4))

    def test_four_missing_below_threshold(self):
        self.fixture(count=4);self.now();self.assertFalse(any(x['status']=='eligible' for x in self.items()))

    def test_attendance_without_completion_never_triggers(self):
        self.fixture(done=False);self.now();self.assertEqual(self.items(),[])

    def test_host_task_due_exact_thirty_minutes_once_without_model_plan(self):
        self.fixture();self.now('2025-10-20 19:10');self.enable();ap.sync()
        rows=s.read('SELECT * FROM service_tasks WHERE skill=? AND run_id=?',(svc.RULE,tm.state()['run_id']))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['due_at'],'2025-10-20T19:40:00+08:00')
        self.assertEqual(s.read('SELECT * FROM completion_plans'),[])
        with closing(sqlite3.connect(s.DB)) as c,c:
            state=tm.state();state['manual_service_task_id']=rows[0]['id'];c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
        self.clock('2025-10-20T19:39:59+08:00');self.assertIsNone(ap.claim())
        self.clock('2025-10-20T19:40:00+08:00');self.assertEqual(ap.claim()['id'],rows[0]['id'])

    def test_repeated_completion_signal_keeps_one_task(self):
        self.fixture(done=False);self.now('2025-10-20 19:10');self.enable();state=tm.state();raw=tm.fixture_data();lesson=next(l for l in raw['learning_daily'] if l['lesson_no']==6)
        payload={'schema_version':1,'event_type':'lesson.completed','event_id':'s11-event','completion_id':'s11-event','uid':state['uid'],'enrollment_id':events.enrollment(raw,lesson)['id'],'lesson_id':lesson['id'],'completed_at':state['as_of']}
        self.assertEqual(events.accept(payload,state['run_id'])['status'],'recorded');ap.sync()
        self.assertEqual(events.accept(payload,state['run_id'])['status'],'duplicate');ap.sync()
        self.assertEqual(len(s.read('SELECT * FROM service_tasks WHERE skill=?',(svc.RULE,))),1)

    def test_following_catchup_does_not_repeat_welcome(self):
        data=self.fixture();second=next(l for l in data['learning_daily'] if l['lesson_no']==1);second.update(completed_at='2025-10-20T19:20:00+08:00',attended_at='2025-10-20T19:15:00+08:00');fq.FILE.write_text(json.dumps(data));self.now()
        items=self.items();self.assertEqual(sum(x['status']=='eligible' for x in items),1);self.assertEqual(next(x for x in items if x['status']=='eligible')['evidence']['lesson']['lesson_no'],6)

    def test_continuous_catchup_does_not_restart_welcome(self):
        data=self.fixture(return_lesson=1)
        for row in data['learning_daily']:
            if row['lesson_no'] in [6,7]:row.update(attended_at=None,completed_at=None,checked_in_at=None)
            if row['lesson_no']==2:row.update(attended_at='2025-10-21T18:50:00+08:00',completed_at='2025-10-21T19:10:00+08:00')
        fq.FILE.write_text(json.dumps(data));self.now('2025-10-20 19:40')
        self.assertTrue(any(x['status']=='eligible' for x in self.items()))
        self.clock('2025-10-21T19:40:00+08:00')
        self.assertFalse(any(x['status']=='eligible' for x in self.items()))

    def test_weekend_rest_does_not_restart_daily_catchup_welcome(self):
        data=self.fixture(return_lesson=1)
        for row in data['learning_daily']:
            if row['lesson_no']<=11:row.update(attended_at=None,completed_at=None,checked_in_at=None)
        for lesson_no,day in enumerate(['2025-10-20','2025-10-21','2025-10-22','2025-10-23','2025-10-24','2025-10-27'],1):
            row=next(x for x in data['learning_daily'] if x['lesson_no']==lesson_no)
            row.update(attended_at=day+'T18:50:00+08:00',completed_at=day+'T19:10:00+08:00')
        fq.FILE.write_text(json.dumps(data));self.now('2025-10-27 19:40')
        item=next(x for x in self.items() if x['evidence']['lesson']['lesson_no']==6)
        self.assertEqual(item['evidence']['return_to_learning']['before_missing_count'],5)
        self.assertEqual(item['status'],'not_eligible')

    def test_weekly_pdf_and_summary_count_as_one_contact_for_s11(self):
        import weekly_materials as wm,mcp_server,wechat_test as w
        self.fixture(return_lesson=1,stamp='2025-10-18T12:00:00+08:00')
        self.now('2025-10-18 12:30');self.enable();state=tm.state();day=state['as_of'][:10]
        with closing(sqlite3.connect(s.DB)) as c,c:
            wm.initialize(c)
            item=next(x for x in proactive.evaluate(c,day) if x['rule']=='weekly-learning-review')
            c.execute('INSERT OR REPLACE INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',(item['id'],item['day'],item['rule'],item['scope'],s.encoded(item),'delivered',s.now()))
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(item['dedupe_key'],item['id'],'prior-s10-text',day+'T09:00:00+08:00'))
            for key in ['prior-s10-text','prior-s10-pdf']:c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,'本周总结','sent',day+'T09:00:00+08:00','test_wechat'))
            c.execute('INSERT INTO weekly_material_deliveries VALUES(?,?,?,?,?,?)',(state['run_id'],state['uid'],'week1','file1','prior-s10-pdf','{}'))
            self.assertEqual(wm.companion_file_keys(c,day),{'prior-s10-pdf'})
            c.execute("UPDATE deliveries SET status='superseded' WHERE action_key='prior-s10-text'")
            self.assertEqual(wm.companion_file_keys(c,day),set())
            c.execute("UPDATE deliveries SET status='sent' WHERE action_key='prior-s10-text'")
            self.assertEqual(wm.companion_file_keys(c,'2025-10-19'),set())
            tid=c.execute('SELECT id FROM service_tasks WHERE skill=?',(svc.RULE,)).fetchone()[0]
            c.execute("UPDATE service_tasks SET status='running',started_at=?,native_request_id='quota-test' WHERE id=?",(s.now(),tid));c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(tid),))
        material=json.loads(ap.prompt(tid)['prompt'].split('<task_material>\n')[1].split('\n</task_material>')[0])
        args={'candidate_ids':[material['task']['candidate_id']],'body':'侨侨，今天回来完成这一课，老师真替你开心。咱们慢慢来。','execution_token':material['task']['execution_token']}
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'quota-return'}) as send:
            result=mcp_server.audited_call('deliver_proactive',args)
        self.assertEqual(result['status'],'accepted');self.assertEqual(send.call_count,1)

    def test_fresh_later_backlog_can_create_new_return(self):
        data=self.fixture(return_lesson=1)
        for row in data['learning_daily']:
            if 6<=row['lesson_no']<=10:row.update(attended_at=None,completed_at=None,checked_in_at=None)
            if row['lesson_no']==11:row.update(attended_at='2025-10-27T19:10:00+08:00',completed_at='2025-10-27T19:10:00+08:00')
        fq.FILE.write_text(json.dumps(data));self.now('2025-10-27 19:40');self.assertTrue(any(x['status']=='eligible' for x in self.items()))

    def test_missing_or_duplicate_lesson_is_unknown(self):
        original=fq.FILE.read_text()
        for duplicate in [False,True]:
            with self.subTest(duplicate=duplicate):
                fq.FILE.write_text(original)
                data=self.fixture();rows=data['learning_daily'];target=next(l for l in rows if l['lesson_no']==2)
                if duplicate:rows.append(copy.deepcopy(target))
                else:rows.remove(target)
                fq.FILE.write_text(json.dumps(data));self.now();self.assertFalse(any(x['status']=='eligible' for x in self.items()))

    def test_same_event_other_completion_prompts_are_merged(self):
        self.fixture();self.now()
        with closing(sqlite3.connect(s.DB)) as c:items=cs.decisions(c,tm.state(),tm.snapshot(),tm.fixture_data(include_events=False))
        self.assertEqual(next(x for x in items if x['skill']==cs.RULES['S03'])['status'],'merged_into_higher_priority')

    def test_pause_prevents_host_task(self):
        self.fixture();self.now()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-20','s11-pause','今天暂停',s.now()))
        self.enable();self.assertEqual(s.read('SELECT * FROM service_tasks WHERE skill=?',(svc.RULE,)),[])

    def test_late_and_midnight_delay(self):
        self.fixture(stamp='2025-10-20T23:50:00+08:00');self.now('2025-10-21 00:20');item=next(x for x in self.items() if x['status']=='expired');self.assertEqual(item['time_window']['earliest'],'2025-10-21T00:20:00+08:00')
        self.assertIn('不顺延',item['reason'])
        self.now('2025-10-21 06:30');self.assertFalse(any(x['status']=='eligible' for x in self.items()))
        self.now('2025-10-22 00:20');self.assertFalse(any(x['status']=='eligible' for x in self.items()))

    def start_action(self):
        self.fixture();self.now();self.enable();tid=s.read('SELECT id FROM service_tasks WHERE skill=?',(svc.RULE,))[0]['id']
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("UPDATE service_tasks SET status='running',started_at=?,native_request_id='s11-request' WHERE id=?",(s.now(),tid));c.execute("INSERT OR REPLACE INTO metadata VALUES('active_service_task',?)",(s.encoded(tid),))
        prepared=ap.prompt(tid);return tid,prepared

    def test_actual_host_context_and_delivery_receipt(self):
        import host_receipt,mcp_server,wechat_test as w
        tid,prepared=self.start_action();material=json.loads(prepared['prompt'].split('<task_material>\n')[1].split('\n</task_material>')[0]);facts=material['facts']
        self.assertEqual(facts['family_profile']['child_nickname'],self.original_profile['child_nickname']);self.assertEqual(facts['family_profile']['guardian_role'],'mother')
        self.assertEqual(facts['return_to_learning']['return_to_learning']['before_missing_count'],5)
        self.assertEqual([x['name'] for x in prepared['prepared_skills']],[svc.RULE,'reply-style'])
        args={'candidate_ids':[material['task']['candidate_id']],'body':'侨侨，今天回来读完这一课，老师真替你高兴，咱们慢慢接着读。','execution_token':material['task']['execution_token']}
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'s11-one'}) as send:
            result=mcp_server.audited_call('deliver_proactive',args);self.assertEqual(result['status'],'accepted');self.assertEqual(send.call_count,1)
            self.assertEqual(host_receipt.finish_delivery(tid,'s11-request','deliver_proactive',result['action_key'],json.dumps(result))['status'],'awaiting_delivery')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent'");wf.reconcile(c)
        self.assertEqual(host_receipt.finish_delivery(tid,'s11-request','deliver_proactive',result['action_key'],json.dumps(result))['status'],'done')

if __name__=='__main__':unittest.main()
