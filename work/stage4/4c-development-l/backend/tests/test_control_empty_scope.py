"""Real preparation/dispatch and runner counterexamples for blank pause scope."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from digital_tutor.application import Application,Clock
from digital_tutor.context import build_context
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue
from digital_tutor.scheduling import step_key
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import dispatch
from digital_tutor.send_policy import action_paused
from digital_tutor.service_context import control_scope_snapshot
from digital_tutor.tools import ToolRegistry
from digital_tutor.wechat import prepare_message,read_messages,send_message

sys.path.append(str(Path(__file__).resolve().parents[1]/'scripts'))
from stage4_behavior_checks import evaluate


class BlankControlScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=Application(Path(self.tmp.name)/'scope.sqlite',Clock('2026-10-04T10:00:00+08:00'),
                             mode='full',strict_runtime=True)
        self.app.create_family('f','synthetic-f','mock:f');self.app.create_family('g','synthetic-g','mock:g')
        self.seq=0
        with self.app.store.transaction() as db:
            for eid in ('first','second'):
                self.app.store.put('event',eid,'f',{'goal_key':'original-'+eid,'goal_text':'Historical '+eid,
                    'status':'pending','completion':{'kind':'goal','lesson_refs':['synthetic-'+eid],
                       'required_part_ids':[],'required_stage':'sent'},'requirement_revision':1,
                    'remaining_work':['synthetic-'+eid],'waiting':None,'next_action':{
                        'action_kind':'remind_makeup','target_refs':['synthetic-'+eid],
                        'purpose':'Historical approved reminder'},'due_at':None})
            self.app.store.put('plan','historical-today','f',{'business_date':'2026-10-04',
                'items':[{'event_id':'first','rank':1},{'event_id':'second','rank':2}]})
            db.execute('INSERT INTO plans VALUES(?,?,?)',('f','2026-10-04','historical-today'))
        channel=self.app.open_session('f','channel')
        self.message=self.app.ingest_message(channel,{'external_id':'pause-question',
            'body':'先暂停补读提醒，课程怎么陪读？','occurred_at':self.app.clock.iso()})['data']
        self.app.revoke_session(channel)
        self.queue=JobQueue(self.app);self.claim=self.queue.claim('scope-test')
        self.token=self.queue.session(self.claim);self.tools=ToolRegistry(self.app,self.token)
        read_messages(self.app,self.token,{'mode':'all','direction':'inbound'})
        self.message=self.app.interpret(self.token,{'request_id':'interpret','message_id':self.message['id'],
            'expected_version':self.message['version'],
            'intents':[{'intent_id':'q','text':'课程问题','requires_reply':True}]})['data']

    def tearDown(self):self.app.close();self.tmp.cleanup()

    def key(self):self.seq+=1;return 'scope-'+str(self.seq)

    def control_args(self,event_id=None):
        return {'request_id':self.key(),'event_id':event_id,'action_kinds':['remind_makeup'],
            'reason':'暂停补读催办','source_message_id':self.message['id'],'source_version':self.message['version']}

    def historical_control(self,event_id='',family='f',status='active'):
        with self.app.store.transaction():
            return self.app.store.put('control',self.key(),family,{
                'event_id':event_id,'action_kinds':['remind_makeup'],'status':status,
                'source_message_id':self.message['id'],'source_version':self.message['version'],
                'reason':'Historical pause scope fixture','effective_at':self.app.clock.iso()})

    def reminder_args(self,event):
        return {'request_id':self.key(),'expected_family_version':self.app.store.family('f')['version'],
                'event_id':event,'step_id':step_key(self.app.event('f',event)),
                'action_kind':'remind_makeup','body':'Synthetic original-step reminder'}

    def prepare_target(self,event):
        # Real serialized step capabilities replace the former arbitrary service token.
        self.queue.finish(self.claim,'succeeded')
        current=self.app.event('f',event)
        with self.app.store.transaction():
            self.queue.enqueue('f','fixture-step-'+event,{'kind':'step','event_id':event,
                'step_key':step_key(current),'event_version':current['version']})
        self.claim=self.queue.claim('step-'+event)
        self.assertEqual(self.claim['job']['event_id'],event)
        self.token=self.queue.session(self.claim);self.tools=ToolRegistry(self.app,self.token)
        return prepare_message(self.app,self.token,self.reminder_args(event))['data']

    def assert_prepare_paused(self,event):
        with self.assertRaises(BusinessError) as caught:prepare_message(self.app,self.token,self.reminder_args(event))
        self.assertEqual(caught.exception.code,'CONTROL_ACTIVE')

    def test_new_empty_or_whitespace_control_rejected_without_effect(self):
        before=self.app.store.family('f')['version']
        for value in ('',' ','\t\n'):
            with self.subTest(value=value):
                result=self.tools.call('control.apply',self.control_args(value))
                self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'INVALID_SCHEMA')
        self.assertEqual(self.app.store.list('control','f'),[])
        self.assertEqual(self.app.store.family('f')['version'],before)

    def test_null_family_pause_blocks_both_real_preparations_but_allows_question(self):
        result=self.tools.call('control.apply',self.control_args(None));self.assertTrue(result['ok'],result)
        for event in ('first','second'):self.assert_prepare_paused(event)
        sent=send_message(self.app,self.token,{'request_id':self.key(),
            'expected_family_version':self.app.store.family('f')['version'],'body':'Synthetic sourced course answer',
            'action_kind':'reply','reply_to':[{'message_id':self.message['id'],'intent_id':'q'}]})
        self.assertEqual(sent['data']['stage'],'sent');self.assertEqual(channel_for(self.app).count(),1)

    def test_historical_empty_scope_blocks_both_targets_and_future_scope_without_rewrite(self):
        old=self.historical_control();before=self.app.store.family('f')['version']
        for event in ('first','second','not-created-yet'):
            self.assertTrue(action_paused(self.app,'f','remind_makeup',event))
        self.assertFalse(action_paused(self.app,'f','reply',None))
        for event in ('first','second'):self.assert_prepare_paused(event)
        scope=control_scope_snapshot(self.app,'f')['controls'][0]
        self.assertEqual(scope['scope_kind'],'invalid_legacy');self.assertIsNone(scope['event_id'])
        self.assertEqual(scope['effective_scope_kind'],'family')
        self.assertEqual(scope['stored_event_id'],'');self.assertTrue(scope['legacy_invalid_scope'])
        self.assertEqual(scope['applies_to_current_event_ids'],['first','second'])
        self.assertTrue(scope['applies_to_future_events'])
        self.assertEqual(self.app.store.get('control',old['id'],'f'),old)
        self.assertEqual(self.app.store.family('f')['version'],before)

    def test_historical_empty_scope_cancels_two_actual_queued_operations(self):
        queued=[self.prepare_target(event) for event in ('first','second')]
        self.historical_control()
        for op in queued:
            current=dispatch(self.app,'f',op['id'])
            self.assertEqual(current['stage'],'cancelled')
            # The earlier serialized run has already released its lease; it is
            # independently unsendable. The still-live run must hit the pause.
            self.assertEqual(current['cancel_reason'],'LEASE_LOST' if op==queued[0] else 'CONTROL_ACTIVE')
            self.assertEqual(current['dispatch_count'],0)
        self.assertEqual(channel_for(self.app).count(),0)

    def test_new_null_pause_cancels_both_preexisting_queued_operations(self):
        queued=[self.prepare_target(event) for event in ('first','second')]
        result=self.tools.call('control.apply',self.control_args(None));self.assertTrue(result['ok'],result)
        for op in queued:
            current=dispatch(self.app,'f',op['id'])
            self.assertEqual(current['cancel_reason'],'LEASE_LOST' if op==queued[0] else 'CONTROL_ACTIVE')
            self.assertEqual(current['stage'],'cancelled');self.assertEqual(current['dispatch_count'],0)
        self.assertEqual(channel_for(self.app).count(),0)

    def test_specific_event_pause_and_released_or_other_family_pause_do_not_expand(self):
        self.historical_control('first');self.historical_control('',family='g')
        self.historical_control('',status='released')
        self.assert_prepare_paused('first')
        op=self.prepare_target('second')
        self.assertEqual(dispatch(self.app,'f',op['id'])['stage'],'sent')
        self.assertFalse(action_paused(self.app,'f','remind_makeup','second'))

    def test_historical_blank_scope_releases_with_null_and_new_source_only(self):
        old=self.historical_control('  ')
        same=self.control_args(None);same['release_control_id']=old['id']
        with self.assertRaises(BusinessError) as caught:self.app.apply_control(self.token,same)
        self.assertEqual(caught.exception.code,'EVIDENCE_MISSING')
        channel=self.app.open_session('f','channel')
        new=self.app.ingest_message(channel,{'external_id':'new-permission','body':'恢复补读提醒。',
                                           'occurred_at':self.app.clock.iso()})['data']
        token=self.app.open_session('f','service')
        args={**self.control_args(None),'release_control_id':old['id'],
              'source_message_id':new['id'],'source_version':new['version']}
        released=self.app.apply_control(token,args)['data']
        self.assertEqual(released['status'],'released');self.assertEqual(released['event_id'],'  ')
        self.assertFalse(action_paused(self.app,'f','remind_makeup','first'))

    def test_progress_context_includes_historical_blank_family_control(self):
        old=self.historical_control()
        report_id='scope-report';key=self.app.report_key('f',report_id,1)
        with self.app.store.transaction():
            self.app.store.put('report',key,'f',{'report_id':report_id,'revision':1,'resolution':None,
                'event_ids':['first'],'payload':{'evidence_refs':[]}})
        token=self.app.open_session('f','planner_progress',report_id,1)
        self.assertEqual([c['id'] for c in build_context(self.app,token)['controls']],[old['id']])

    def runner_checks(self):
        events={e['id']:e for e in self.app.store.list('event','f')}
        plans={p['id']:p for p in self.app.store.list('plan','f')}
        return evaluate(self.app,['f'],SimpleNamespace(calls=[]),'pause_course',
                        {'allow_unanswered':True},events,plans,[])[0]

    def test_runner_rejects_blank_and_single_event_pause_even_when_any_active(self):
        control=self.historical_control('')
        checks=self.runner_checks()
        self.assertTrue(checks['active_makeup_pause'])
        self.assertFalse(checks['f_pause_scope_well_formed'])
        self.assertFalse(checks['f_pause_covers_all_original_goals'])
        with self.app.store.transaction():
            self.app.store.put('control',control['id'],'f',{**control,'event_id':'first'},control['version'])
        checks=self.runner_checks();self.assertTrue(checks['f_pause_scope_well_formed'])
        self.assertFalse(checks['f_pause_covers_all_original_goals'])

    def test_runner_accepts_null_control_covering_both_original_goals(self):
        self.app.apply_control(self.token,self.control_args(None));checks=self.runner_checks()
        self.assertTrue(checks['f_pause_scope_well_formed'])
        self.assertTrue(checks['f_pause_covers_all_original_goals'])


if __name__=='__main__':unittest.main()
