import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from digital_tutor.application import Application, Clock
from digital_tutor.demo import run_demo, seed_learning
from digital_tutor.domain import BusinessError
from digital_tutor.learning import import_workbook
from digital_tutor.tools import ToolRegistry


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.sqlite'
        self.app=Application(self.path,Clock('2026-10-03T09:00:00+08:00'))
        self.app.create_family('f','u','mock:f')
        self.app.create_family('other','other-u','mock:other')
        self.service=self.app.open_session('f','service')
        self.channel=self.app.open_session('f','channel')
        self.admin=self.app.open_session('f','admin')
        self.counter=0

    def tearDown(self):
        self.app.close(); self.tmp.cleanup()

    def key(self):
        self.counter+=1; return 'req-'+str(self.counter)

    def call(self,token,name,args,code=None):
        result=ToolRegistry(self.app,token).call(name,args)
        if code:
            self.assertFalse(result['ok'],result)
            self.assertEqual(result['error']['code'],code,result)
            return result
        self.assertTrue(result['ok'],result)
        return result['result']

    def version(self):
        return self.app.store.family('f')['version']

    def daily(self,events=None,refs=None,decisions=None):
        events=events if events is not None else [self.goal('a')]
        token=self.app.open_session('f','planner_daily')
        args={'request_id':self.key(),'expected_family_version':self.version(),
              'business_date':self.app.clock.now().date().isoformat(),'new_events':events,
              'plan_refs':refs if refs is not None else [e['client_ref'] for e in events],
              'pending_decisions':decisions or []}
        return token,args

    def goal(self,name,delivery=False):
        return {'client_ref':name,'goal_key':name,'goal_text':'测试目标 '+name,
                'due_at':'2026-10-03T20:00:00+08:00',
                'completion':{'kind':'delivery','required_part_ids':['p1','p2'],'required_stage':'delivered'} if delivery else {'kind':'goal','lesson_refs':['lesson-'+name]}}

    def create(self,events=None):
        t,a=self.daily(events)
        return self.call(t,'planner.commit_daily',a)['data']['event_ids']

    def incoming(self,interpret=True,count=1):
        m=self.call(self.channel,'wechat.receive_message',{'external_id':self.key(),'body':'先别催；入口在哪里？','occurred_at':self.app.clock.iso()})['data']
        if interpret:
            m=self.call(self.service,'message.interpret',{'request_id':self.key(),'message_id':m['id'],'expected_version':m['version'],
                'intents':[{'intent_id':'i'+str(i),'text':'问题'+str(i)} for i in range(count)]})['data']
        return m

    def pause(self,msg,eid=None):
        return self.call(self.service,'control.apply',{'request_id':self.key(),'event_id':eid,'action_kinds':['remind_makeup'],
                         'reason':'恢复后家长联系','source_message_id':msg['id'],'source_version':msg['version']})['data']

    @staticmethod
    def ref(kind,record):
        return {'kind':kind,'id':record['id'],'version':record['version']}

    def evidence(self,kind='lesson_completed',**fields):
        return self.ref('evidence',self.app.add_evidence(self.admin,self.key(),{'kind':kind,**fields}))

    def report(self,ids,refs,rid=None,revision=1,facts=None,supersedes=None):
        rid=rid or self.key()
        args={'request_id':self.key(),'report_id':rid,'revision':revision,'event_ids':ids,'facts':facts or ['已核实事实'],
              'evidence_refs':refs,'supersedes_revision':supersedes}
        self.call(self.service,'progress.submit',args)
        return rid

    def progress(self,rid,updates,revision=1):
        t=self.app.open_session('f','planner_progress',rid,revision)
        return t,{'request_id':self.key(),'report_id':rid,'revision':revision,'expected_family_version':self.version(),'event_updates':updates}

    def update(self,eid,changes,refs):
        return {'event_id':eid,'expected_version':self.app.event('f',eid)['version'],'changes':changes,'evidence_refs':refs}

    def reply(self,msg,intent='i0',**overrides):
        args={'request_id':self.key(),'expected_family_version':self.version(),'body':'模拟答复','action_kind':'reply',
              'reply_to':[{'message_id':msg['id'],'intent_id':intent}]}
        args.update(overrides); return args

    def send_event(self,eid,part=None):
        return {'request_id':self.key(),'expected_family_version':self.version(),'body':'模拟交付',
                'action_kind':'deliver_feedback' if part else 'remind_makeup','event_id':eid,'part_id':part}

    def test_daily_idempotent_conflict_and_once(self):
        t,a=self.daily(); first=self.call(t,'planner.commit_daily',a)
        self.assertEqual(self.call(t,'planner.commit_daily',a)['outcome'],'duplicate')
        changed=copy.deepcopy(a); changed['plan_refs']=[]
        self.call(t,'planner.commit_daily',changed,'IDEMPOTENCY_CONFLICT')
        t2,a2=self.daily([]); self.call(t2,'planner.commit_daily',a2,'DAILY_ALREADY_COMMITTED')
        self.assertEqual(len(self.app.state(self.service)['events']),1)

    def test_daily_rolls_back_partial_event_creation(self):
        t,a=self.daily([self.goal('a'),{**self.goal('b'),'goal_key':'a'}])
        self.call(t,'planner.commit_daily',a,'DUPLICATE_GOAL')
        self.assertEqual(self.version(),1); self.assertEqual(self.app.state(self.service)['events'],[])
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0],0)

    def test_fields_and_role_cannot_create_white_day_target(self):
        t,a=self.daily(); self.call(self.service,'planner.commit_daily',a,'ROLE_FORBIDDEN')
        a['family_id']='other'; self.call(t,'planner.commit_daily',a,'INVALID_SCHEMA')
        self.assertNotIn('planner.commit_daily',[t['name'] for t in ToolRegistry(self.app,self.service).describe()])

    def test_stale_planner_snapshot_rejected(self):
        t,a=self.daily(); self.incoming()
        self.call(t,'planner.commit_daily',a,'STALE_INPUT')
        a['expected_family_version']=self.version()
        self.call(t,'planner.commit_daily',a,'STALE_INPUT')

    def test_pause_immediate_without_planner_and_separate_reply(self):
        eid=self.create()['a']; m=self.incoming(count=2); self.pause(m)
        self.assertEqual(self.app.event('f',eid)['version'],1)
        self.call(self.service,'wechat.send_message',self.send_event(eid),'CONTROL_ACTIVE')
        self.call(self.service,'wechat.send_message',self.reply(m))
        current=self.app.store.get('message',m['id'],'f')
        self.assertEqual(len(current['intents'][0]['reply_operation_refs']),1)
        self.assertEqual(current['intents'][1]['reply_operation_refs'],[])

    def test_pause_not_released_by_same_message(self):
        m=self.incoming(); c=self.pause(m)
        a={'request_id':self.key(),'action_kinds':['remind_makeup'],'reason':'解除','source_message_id':m['id'],
           'source_version':m['version'],'release_control_id':c['id']}
        self.call(self.service,'control.apply',a,'EVIDENCE_MISSING')
        fresh=self.incoming(); a.update(request_id=self.key(),source_message_id=fresh['id'],source_version=fresh['version'])
        self.assertEqual(self.call(self.service,'control.apply',a)['data']['status'],'released')

    def test_wechat_all_latest_and_cursor_never_answer(self):
        one=self.incoming(); two=self.incoming(); three=self.incoming()
        all_=self.call(self.service,'wechat.read_messages',{'mode':'all'})
        self.assertEqual(all_['returned_count'],3)
        latest=self.call(self.service,'wechat.read_messages',{'mode':'latest'})
        self.assertEqual(latest['messages'][0]['id'],three['id']); self.assertTrue(latest['has_older'])
        latest2=self.call(self.service,'wechat.read_messages',{'mode':'latest','limit':2})
        self.assertEqual([m['id'] for m in latest2['messages']],[two['id'],three['id']])
        page=self.call(self.service,'wechat.read_messages',{'mode':'since','limit':2})
        next_=self.call(self.service,'wechat.read_messages',{'mode':'since','after_seq':page['next_cursor'],'limit':2})
        self.assertTrue(page['has_more']); self.assertFalse(next_['has_more'])
        self.assertEqual(next_['messages'][0]['id'],three['id'])
        self.assertFalse(all_['read_marks_answered'])
        self.assertEqual(self.app.store.get('message',one['id'],'f')['intents'][0]['reply_operation_refs'],[])

    def test_cross_family_message_state_and_send(self):
        msg=self.incoming(); other=self.app.open_session('other','service')
        self.assertEqual(self.call(other,'wechat.read_messages',{'mode':'all'})['returned_count'],0)
        a=self.reply(msg); a['expected_family_version']=1
        self.call(other,'wechat.send_message',a,'RECORD_NOT_FOUND')
        self.call(other,'wechat.get_operation',{'operation_id':'nonexistent'},'RECORD_NOT_FOUND')

    def test_uninterpreted_input_blocks_proactive(self):
        eid=self.create()['a']; self.incoming(False)
        self.call(self.service,'wechat.send_message',self.send_event(eid),'INPUT_UNRESOLVED')

    def test_reinterpret_does_not_drop_intent(self):
        m=self.incoming(count=2)
        self.call(self.service,'message.interpret',{'request_id':self.key(),'message_id':m['id'],'expected_version':m['version'],
                  'intents':[{'intent_id':'new','text':'替代'}]},'ALREADY_INTERPRETED')

    def test_window_edges_both_reply_and_proactive(self):
        eid=self.create()['a']; m=self.incoming()
        for hour in (7,21,23):
            self.app.clock=Clock(f'2026-10-03T{hour:02}:00:00+08:00')
            self.call(self.service,'wechat.send_message',self.reply(m),'WINDOW_CLOSED')
            self.call(self.service,'wechat.send_message',self.send_event(eid),'WINDOW_CLOSED')
        self.app.clock=Clock('2026-10-03T08:00:00+08:00')
        self.assertEqual(self.call(self.service,'wechat.send_message',self.reply(m))['data']['stage'],'sent')

    def test_unknown_send_reuses_operation_and_late_receipt_once(self):
        m=self.incoming(); self.app.mock_send_outcome='unknown'; a=self.reply(m)
        first=self.call(self.service,'wechat.send_message',a)['data']
        self.assertEqual(self.call(self.service,'wechat.send_message',a)['outcome'],'duplicate')
        other={**a,'request_id':self.key()}
        self.assertEqual(self.call(self.service,'wechat.send_message',other)['data']['id'],first['id'])
        self.assertEqual(len(self.app.state(self.service)['messages']),1)
        read=self.call(self.service,'wechat.get_operation',{'operation_id':first['id']})
        self.assertEqual(read['dispatch_count'],1)
        receipt={'request_id':self.key(),'operation_id':first['id'],'stage':'sent'}
        self.call(self.channel,'wechat.mock_receipt',receipt)
        receipt.update(request_id=self.key(),stage='delivered'); self.call(self.channel,'wechat.mock_receipt',receipt)
        receipt.update(request_id=self.key(),stage='unknown'); self.call(self.channel,'wechat.mock_receipt',receipt)
        self.assertEqual(len(self.app.state(self.service)['messages']),2)
        self.assertEqual(self.call(self.service,'wechat.get_operation',{'operation_id':first['id']})['stage'],'delivered')
        receipt.update(request_id=self.key(),stage='failed'); self.call(self.channel,'wechat.mock_receipt',receipt,'RECEIPT_CONFLICT')

    def test_send_content_change_not_new_send_and_stale_send(self):
        m=self.incoming(); a=self.reply(m); self.incoming()
        self.call(self.service,'wechat.send_message',a,'STALE_INPUT')
        a['expected_family_version']=self.version(); self.call(self.service,'wechat.send_message',a)
        a.update(request_id=self.key(),body='换一种话术')
        self.call(self.service,'wechat.send_message',a,'OPERATION_ALREADY_EXISTS')

    def test_unknown_intent_cannot_be_reserved_in_new_combination(self):
        m=self.incoming(count=2); self.app.mock_send_outcome='unknown'
        self.call(self.service,'wechat.send_message',self.reply(m))
        a=self.reply(m); a['reply_to'].append({'message_id':m['id'],'intent_id':'i1'})
        self.call(self.service,'wechat.send_message',a,'OPERATION_UNCERTAIN')

    def test_goal_requires_completion_evidence_not_reminder(self):
        eid=self.create()['a']; op=self.call(self.service,'wechat.send_message',self.send_event(eid))['data']
        ref=self.ref('operation',op); rid=self.report([eid],[ref])
        t,a=self.progress(rid,[self.update(eid,{'status':'done'},[ref])])
        self.call(t,'planner.commit_progress',a,'COMPLETION_NOT_MET')
        self.assertTrue(self.app.event('f',eid)['update_pending'])
        good=self.evidence(lesson_ref='lesson-a'); t,a=self.progress(rid,[self.update(eid,{'status':'done'},[good])])
        self.call(t,'planner.commit_progress',a)
        self.assertEqual(self.app.event('f',eid)['remaining_work'],[])

    def test_delivery_all_parts_and_required_receipt_stage(self):
        eid=self.create([self.goal('a',True)])['a']
        one=self.call(self.service,'wechat.send_message',self.send_event(eid,'p1'))['data']
        two=self.call(self.service,'wechat.send_message',self.send_event(eid,'p2'))['data']
        # Submit only after sends, then progressively obtain stronger receipts.
        receipt=lambda op: self.call(self.channel,'wechat.mock_receipt',{'request_id':self.key(),'operation_id':op['id'],'stage':'delivered'})['data']
        one=receipt(one); refs=[self.ref('operation',one),self.ref('operation',two)]
        rid=self.report([eid],refs)
        t,a=self.progress(rid,[self.update(eid,{'status':'done'},refs)])
        self.call(t,'planner.commit_progress',a,'COMPLETION_NOT_MET')
        two=receipt(two); refs[1]=self.ref('operation',two)
        self.report([eid],refs,rid=rid,revision=2,supersedes=1)
        t,a=self.progress(rid,[self.update(eid,{'status':'done'},refs)],2)
        self.call(t,'planner.commit_progress',a)
        self.assertEqual(self.app.event('f',eid)['status'],'done')

    def test_progress_scope_and_rule_immutable(self):
        ids=self.create([self.goal('a'),self.goal('b')]); m=self.incoming(); ref=self.ref('message',m)
        rid=self.report([ids['a']],[ref])
        t,a=self.progress(rid,[self.update(ids['b'],{},[])])
        self.call(t,'planner.commit_progress',a,'PROGRESS_SCOPE_VIOLATION')
        a['event_updates']=[self.update(ids['a'],{'completion':{'kind':'delivery'}},[ref])]
        self.call(t,'planner.commit_progress',a,'INVALID_SCHEMA')
        a['new_events']=[self.goal('c')]; self.call(t,'planner.commit_progress',a,'INVALID_SCHEMA')

    def test_multi_event_progress_rolls_back_and_keeps_report(self):
        ids=self.create([self.goal('a'),self.goal('b')]); ref=self.evidence(lesson_ref='lesson-a')
        rid=self.report(list(ids.values()),[ref])
        t,a=self.progress(rid,[self.update(eid,{'status':'done'},[ref]) for eid in ids.values()])
        self.call(t,'planner.commit_progress',a,'COMPLETION_NOT_MET')
        self.assertEqual([self.app.event('f',e)['version'] for e in ids.values()],[1,1])
        self.assertTrue(all(self.app.event('f',e)['update_pending'] for e in ids.values()))

    def test_consuming_one_report_does_not_clear_another(self):
        eid=self.create()['a']; m=self.incoming(); ref=self.ref('message',m)
        r1=self.report([eid],[ref]); r2=self.report([eid],[ref])
        original_plan=copy.deepcopy(self.app.state(self.service)['plans'])
        t,a=self.progress(r1,[self.update(eid,{'service_hint':'等待核验'},[ref])])
        self.call(t,'planner.commit_progress',a)
        self.assertTrue(self.app.event('f',eid)['update_pending'])
        t,a=self.progress(r2,[self.update(eid,{},[])])
        self.call(t,'planner.commit_progress',a)
        self.assertFalse(self.app.event('f',eid)['update_pending'])
        self.assertEqual(self.app.state(self.service)['plans'],original_plan)

    def test_report_revision_invalidates_old_runtime(self):
        eid=self.create()['a']; ref=self.ref('message',self.incoming()); rid=self.report([eid],[ref])
        t,a=self.progress(rid,[self.update(eid,{},[])])
        self.report([eid],[ref],rid=rid,revision=2,facts=['已核实事实','新增事实'],supersedes=1)
        self.call(t,'planner.commit_progress',a,'STALE_INPUT')
        self.assertTrue(self.app.event('f',eid)['update_pending'])
        self.call(self.service,'progress.submit',{'request_id':self.key(),'report_id':rid,'revision':3,'supersedes_revision':2,
                 'event_ids':[eid],'facts':['删掉旧事实'],'evidence_refs':[ref]},'REPORT_FACT_LOSS')

    def test_report_original_payload_immutable(self):
        eid=self.create()['a']; ref=self.ref('message',self.incoming()); rid=self.report([eid],[ref])
        self.call(self.service,'progress.submit',{'request_id':self.key(),'report_id':rid,'event_ids':[eid],
                 'facts':['替换事实'],'evidence_refs':[ref]},'IDEMPOTENCY_CONFLICT')

    def test_reschedule_proposal_retains_deadline_until_evidence(self):
        eid=self.create()['a']; ref=self.ref('message',self.incoming()); rid=self.report([eid],[ref])
        old=self.app.event('f',eid)['due_at']; new='2026-10-05T20:00:00+08:00'
        t,a=self.progress(rid,[self.update(eid,{'proposed_due_at':new},[ref])]); self.call(t,'planner.commit_progress',a)
        event=self.app.event('f',eid); self.assertEqual(event['due_at'],old)
        change=event['proposed_change']['change_id']; rid=self.report([eid],[ref])
        t,a=self.progress(rid,[self.update(eid,{'accept_change_id':change},[ref])]); self.call(t,'planner.commit_progress',a,'EVIDENCE_MISSING')
        evidence=self.evidence('accept_change',change_id=change,event_id=eid)
        t,a=self.progress(rid,[self.update(eid,{'accept_change_id':change},[evidence])]); self.call(t,'planner.commit_progress',a)
        event=self.app.event('f',eid); self.assertEqual(event['due_at'],new); self.assertEqual(event['deadline_history'][0]['due_at'],old)

    def test_pending_requires_daily_decision_and_retains_original_deadline(self):
        m=self.incoming(); pending=self.call(self.service,'planning.record_pending',{'request_id':self.key(),'source_message_id':m['id'],
              'source_version':m['version'],'request':'做阶段反馈','original_deadline':'下周五','done_work':['已收到请求'],'remaining_work':['核实作品']})['data']
        t,a=self.daily(); self.call(t,'planner.commit_daily',a,'INPUT_COVERAGE_INCOMPLETE')
        t,a=self.daily(decisions=[{'input_id':pending['id'],'disposition':'retained','reason':'资料尚缺'}])
        self.call(t,'planner.commit_daily',a)
        saved=self.app.state(self.service)['pending_inputs'][0]
        self.assertEqual(saved['original_deadline'],'下周五'); self.assertEqual(saved['disposition'],'pending')

    def test_restart_retains_controls_intents_reports_and_receipts(self):
        eid=self.create()['a']; msg=self.incoming(count=2); c=self.pause(msg)
        self.report([eid],[self.ref('control',c)])
        a=self.reply(msg); self.call(self.service,'wechat.send_message',a)
        before=self.app.state(self.service)
        self.app.close(); self.app=Application(self.path,Clock('2026-10-03T09:00:00+08:00'))
        self.assertEqual(self.app.state(self.service),before)
        self.assertEqual(self.call(self.service,'wechat.send_message',a)['outcome'],'duplicate')

    def test_next_day_continues_same_event(self):
        eid=self.create()['a']; self.app.clock=Clock('2026-10-04T08:00:00+08:00')
        t,a=self.daily([],refs=[eid]); self.call(t,'planner.commit_daily',a)
        self.assertEqual(len(self.app.state(self.service)['events']),1)
        self.assertEqual(len(self.app.state(self.service)['plans']),2)
        self.assertEqual(self.app.event('f',eid)['due_at'],'2026-10-03T20:00:00+08:00')

    def test_concurrent_duplicate_message_one_effect(self):
        other=Application(self.path,Clock('2026-10-03T09:00:00+08:00'))
        try:
            args={'external_id':'same-message','body':'hello','occurred_at':'2026-10-03T09:00:00+08:00'}
            with ThreadPoolExecutor(2) as pool:
                fs=[pool.submit(app.ingest_message,self.channel,args) for app in (self.app,other)]
                results=[f.result() for f in fs]
            self.assertEqual({r['outcome'] for r in results},{'applied','duplicate'})
            self.assertEqual(len(self.app.state(self.service)['messages']),1)
        finally:
            other.close()

    def test_revoked_runtime_cannot_mutate_but_can_replay_receipt(self):
        t,a=self.daily(); self.call(t,'planner.commit_daily',a); self.app.revoke_session(t)
        self.assertEqual(self.call(t,'planner.commit_daily',a)['outcome'],'duplicate')
        a['request_id']=self.key(); self.call(t,'planner.commit_daily',a,'RUN_REVOKED')

    def test_synthetic_demo_full_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(Path(tmp)/'demo.sqlite')
            try:
                report=run_demo(app)
                self.assertEqual(report['event']['status'],'waiting')
                self.assertEqual(report['operation_count'],1)
                self.assertEqual(report['duplicate_daily'],'duplicate')
                self.assertFalse(report['real_wechat_used'])
            finally:
                app.close()


class SqlTests(CoreTests):
    # Reuse fixtures, not inherited core tests (installed below).
    def setUp(self):
        super().setUp(); seed_learning(self.app,'u','other-u')

    def test_sql_family_isolation_even_without_where(self):
        result=self.call(self.service,'learning.query_sql',{'sql':'SELECT DISTINCT uid FROM learning_days WHERE 1=1 UNION SELECT uid FROM learning_days'})
        self.assertEqual(result['rows'],[{'uid':'u'}])
        result=self.call(self.service,'learning.query_sql',{'sql':'SELECT count(*) AS n FROM learning_days WHERE uid=:uid','parameters':{'uid':'other-u'}})
        self.assertEqual(result['rows'][0]['n'],0)

    def test_sql_parameters_aggregation_null_and_row_limit(self):
        result=self.call(self.service,'learning.query_sql',{'sql':'SELECT count(*) AS n FROM learning_days WHERE day>=:day','parameters':{'day':2}})
        self.assertEqual(result['rows'][0]['n'],2)
        result=self.call(self.service,'learning.query_sql',{'sql':'SELECT day FROM learning_days ORDER BY day','max_rows':2})
        self.assertTrue(result['truncated']); self.assertEqual(result['rows'],[{'day':1},{'day':2}])
        result=self.call(self.service,'learning.query_sql',{'sql':'SELECT coverage_through FROM learning_batches'})
        self.assertIsNone(result['rows'][0]['coverage_through'])

    def test_sql_rejects_writes_metadata_extensions_and_multiple_statements(self):
        for sql in ('DELETE FROM learning_days','UPDATE learning_days SET day=99','DROP TABLE learning_days',
                    "ATTACH DATABASE ':memory:' AS other",'PRAGMA table_info(learning_days)',
                    'SELECT name FROM sqlite_master',"SELECT load_extension('x')",'SELECT 1; SELECT 2'):
            with self.subTest(sql=sql):
                self.call(self.service,'learning.query_sql',{'sql':sql},'SQL_REJECTED')
        self.assertEqual(self.call(self.service,'learning.query_sql',{'sql':'SELECT count(*) AS n FROM learning_days'})['rows'][0]['n'],3)

    def test_sql_budget_and_duplicate_column_names(self):
        self.call(self.service,'learning.query_sql',{'sql':'WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT sum(n) AS total FROM x'},'SQL_REJECTED')
        self.call(self.service,'learning.query_sql',{'sql':'SELECT day,day FROM learning_days'},'SQL_DUPLICATE_COLUMNS')


# unittest discovers inherited tests; prevent repeating CoreTests under SqlTests.
for _name in list(CoreTests.__dict__):
    if _name.startswith('test_'):
        setattr(SqlTests,_name,None)


@unittest.skipUnless(os.environ.get('DIGITALTUTOR_TEST_WORKBOOK'),'Set workbook path for read-only integration checks')
class WorkbookTests(unittest.TestCase):
    def test_actual_sample_idempotent_isolated_preserves_source_and_anomalies(self):
        source=Path(os.environ['DIGITALTUTOR_TEST_WORKBOOK']); before=hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(Path(tmp)/'import.sqlite')
            try:
                meta=import_workbook(app,source)
                self.assertEqual((meta['user_count'],meta['learning_day_count'],meta['assignment_count']),(15,3600,405))
                self.assertIsNone(meta['coverage_through']); self.assertIsNone(meta['course_binding'])
                flags=meta['quality_flags']
                self.assertEqual(sum(f['code']=='submission_count_conflict' for f in flags),3)
                self.assertEqual(sum(f['code']=='question_text_missing' for f in flags),405)
                self.assertEqual(import_workbook(app,source)['outcome'],'duplicate')
                self.assertEqual(app.store.connection.execute('SELECT count(*) FROM assignments WHERE status="submitted"').fetchone()[0],284)
                uids=[r[0] for r in app.store.connection.execute('SELECT DISTINCT uid FROM learning_days LIMIT 2')]
                for i,uid in enumerate(uids):
                    family='offline-read-'+str(i); app.create_family(family,uid,'mock:offline-'+str(i))
                    tools=ToolRegistry(app,app.open_session(family,'service'))
                    result=tools.call('learning.query_sql',{'sql':'SELECT count(*) AS n, count(DISTINCT uid) AS users FROM learning_days'})
                    self.assertEqual(result['result']['rows'],[{'n':240,'users':1}])
                    result=tools.call('learning.query_sql',{'sql':'SELECT count(*) AS n FROM assignments'})
                    self.assertEqual(result['result']['rows'],[{'n':27}])
                    # No mock conversations are injected into these real-UID read bindings.
                    self.assertEqual(tools.call('wechat.read_messages',{'mode':'all'})['result']['returned_count'],0)
            finally:
                app.close()
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
