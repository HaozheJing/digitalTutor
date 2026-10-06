"""Runtime admission/step boundaries with real queue, isolated store and mock channel."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from digital_tutor.application import Application, Clock
from digital_tutor.context import build_context, read_context
from digital_tutor.domain import BusinessError
from digital_tutor.execution_projection import project_service_state
from digital_tutor.jobs import JobQueue, recovery_state
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import dispatch, reconcile
from digital_tutor.runtime_outcome import record_outcome
from digital_tutor.scheduler import Scheduler, Worker
from digital_tutor.scheduling import baseline_committed, step_key, step_ready
from digital_tutor.storage import digest
from digital_tutor.wechat import prepare_message


class IntradayRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.tmp.name)/'runtime.sqlite', Clock('2026-10-05T10:00:00+08:00'),
                               mode='full', strict_runtime=True,
                               runtime_clock=Clock('2026-10-05T10:00:00+08:00'))
        self.app.create_family('f', 'synthetic-f', 'mock:f')
        self.queue = JobQueue(self.app)
        self.serial = 0
        self.plan([])

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def put(self, kind, key, value):
        with self.app.store.transaction():
            old = next((r for r in self.app.store.list(kind, 'f') if r['id']==key), None)
            return self.app.store.put(kind, key, 'f', value, old['version'] if old else None)

    def plan(self, ids, **extra):
        return self.put('plan', 'historical-plan', {'business_date':'2026-10-05',
            'items':[{'event_id':eid,'rank':i+1,'authority':'baseline'} for i,eid in enumerate(ids)],
            'baseline_committed':True, **extra})

    def event(self, eid, when='2026-10-05T10:00:00+08:00', due=None):
        # Explicit historical approved responsibility fixture, not a model commit.
        return self.put('event', eid, {'goal_key':'historical-'+eid,'goal_text':'Original obligation '+eid,
            'status':'pending','due_at':due,'waiting':None,'requirement_revision':1,
            'completion':{'kind':'goal','lesson_refs':['lesson-'+eid],'required_part_ids':[],
                          'required_stage':'sent'}, 'completion_condition':'Source verifies original lesson completed',
            'next_action':{'action_kind':'remind_makeup','target_refs':['lesson-'+eid],
                           'purpose':'Original approved reminder','execute_at':when}})

    def enqueue_step(self, event):
        with self.app.store.transaction():
            self.queue.enqueue('f','step-fixture-'+event['id'], {'kind':'step','event_id':event['id'],
                'event_version':event['version'],'step_key':step_key(event)})

    def inbound(self, text='A new synthetic request'):
        self.serial += 1
        return self.app.ingest_message(self.app.open_session('f','channel'), {
            'external_id':'incoming-'+str(self.serial),'body':text,'occurred_at':self.app.clock.iso()})['data']

    def interpret(self, message, requires_reply=True):
        return self.app.interpret(self.app.open_session('f','service'), {
            'request_id':'interpret-'+message['id'],'message_id':message['id'],
            'expected_version':message['version'],'intents':[{'intent_id':'request',
            'text':message['body'],'requires_reply':requires_reply}]})['data']

    def pending_wait(self):
        message=self.interpret(self.inbound())
        pending=self.put('pending','pending-fixture', {'source_message_id':message['id'],
            'source_intent_id':'request','planning_requested':True,'await_decision':True,
            'decision_revision':1,'disposition':'pending','event_id':None,'request':'Original requested service'})
        def handler(job, tools):
            result=tools.call('service.record_outcome', {'request_id':'wait-planning','outcome':'waiting',
                'refs':[{'kind':'message','id':message['id'],'version':message['version']},
                        {'kind':'pending','id':pending['id'],'version':pending['version']}],
                'reason':'Original request awaits an actual decision','remaining_work':['Original request'],
                'resume_condition':'Bound pending decision changes'})
            self.assertTrue(result['ok'],result)
            return {'outcome_id':job['run_id']}
        self.assertEqual(Worker(self.app,handler).run_once()['outcome'],'waiting')
        return message,pending

    def send_args(self, event):
        self.serial+=1
        return {'request_id':'send-'+str(self.serial), 'expected_family_version':self.app.store.family('f')['version'],
                'event_id':event['id'],'step_id':step_key(event),'action_kind':'remind_makeup',
                'body':'Synthetic original approved reminder'}

    def test_optional_null_step_fields_preserve_historical_key(self):
        event=self.event('one',None)
        old={**event,'next_action':{k:v for k,v in event['next_action'].items() if k!='execute_at'}}
        event['next_action']['completion_condition']=None
        self.assertEqual(step_key(event),digest({'event_id':old['id'],'next_action':old['next_action']}))

    def test_admission_container_does_not_count_as_baseline(self):
        self.plan([],baseline_committed=False)
        self.assertFalse(baseline_committed(self.app,'f','2026-10-05'))
        self.assertEqual(Scheduler(self.app).tick()['created'],1)
        self.assertEqual(Scheduler(self.app).tick()['created'],0)
        self.assertEqual(self.queue.claim('baseline')['job']['kind'],'daily')

    def test_future_ready_job_does_not_block_due_step(self):
        future=self.event('future','2026-10-05T18:00:00+08:00')
        due=self.event('due')
        self.plan(['future','due']);self.enqueue_step(future);self.enqueue_step(due)
        claim=self.queue.claim('executor')
        self.assertEqual(claim['job']['event_id'],'due')
        self.assertEqual(self.app.store.get('job','step-fixture-future','f')['status'],'ready')

    def test_same_time_steps_sort_by_deadline_then_rank(self):
        late=self.event('late',due='2026-10-05T20:00:00+08:00')
        early=self.event('early',due='2026-10-05T16:00:00+08:00')
        self.plan(['late','early']);self.enqueue_step(late);self.enqueue_step(early)
        self.assertEqual(self.queue.claim('executor')['job']['event_id'],'early')

    def test_old_daily_membership_does_not_authorize_future_day_step(self):
        event=self.event('tomorrow','2026-10-06T09:00:00+08:00')
        self.plan(['tomorrow'])
        self.assertFalse(step_ready(self.app,'f',event))
        self.app.clock.fixed += timedelta(days=1)
        self.assertFalse(step_ready(self.app,'f',event))

    def test_admission_item_without_grant_is_not_baseline_authority(self):
        event=self.event('one')
        self.plan([],items=[{'event_id':'one','rank':1,'authority':'admission'}])
        self.assertFalse(step_ready(self.app,'f',event))

    def test_expired_active_grant_cannot_fall_back_to_today_baseline(self):
        event=self.event('one')
        event=self.put('event','one',{**event,'accepted_commitment':{'id':'grant-original','status':'active',
            'requirement_revision':1,'schedule_revision':1,'authorized_step_key':step_key(event),
            'valid_from':'2026-10-05T08:00:00+08:00','valid_until':'2026-10-05T09:00:00+08:00'}})
        self.plan(['one'])
        self.assertFalse(step_ready(self.app,'f',event))

    def test_unchanged_pending_wait_yields_to_admission_despite_unrelated_version(self):
        message,_=self.pending_wait()
        with self.app.store.transaction():self.app.store.bump('f')
        Scheduler(self.app).tick()
        claim=self.queue.claim('admission')
        self.assertEqual(claim['job']['planning_reason'],'admission')
        self.assertEqual(claim['job']['pending_id'],'pending-fixture')
        self.assertEqual(self.app.store.get('job','message:'+message['id'],'f')['attempt'],1)

    def test_new_inbound_still_precedes_waiting_original_and_admission(self):
        original,_=self.pending_wait();new=self.inbound('Pause the requested contact')
        Scheduler(self.app).tick()
        claim=self.queue.claim('fresh-service')
        self.assertEqual(claim['job']['message_id'],new['id'])
        self.assertNotEqual(new['id'],original['id'])

    def test_changed_bound_decision_wakes_without_unrelated_business_bump(self):
        message,pending=self.pending_wait()
        before=self.app.store.family('f')['version']
        self.put('pending',pending['id'],{**pending,'await_decision':False,'decision_state':'retained'})
        self.assertEqual(self.app.store.family('f')['version'],before)
        claim=self.queue.claim('after-decision')
        self.assertEqual(claim['job']['message_id'],message['id'])
        self.assertEqual(claim['job']['attempt'],2)

    def test_proactive_send_needs_step_capability_and_rechecks_twenty_one(self):
        event=self.event('one');self.plan(['one']);self.enqueue_step(event)
        ordinary=self.inbound();self.interpret(ordinary,False)
        # A normal service session cannot borrow an old approved event.
        token=self.app.open_session('f','service')
        with self.assertRaises(BusinessError):prepare_message(self.app,token,self.send_args(event))
        claim=self.queue.claim('step');token=self.queue.session(claim)
        operation=prepare_message(self.app,token,self.send_args(event))['data']
        self.app.clock.fixed+=timedelta(hours=11)
        result=dispatch(self.app,'f',operation['id'])
        self.assertEqual(result['cancel_reason'],'WINDOW_CLOSED')
        self.assertEqual(channel_for(self.app).count(),0)

    def test_step_unknown_wait_new_input_and_same_operation_recovery(self):
        event=self.event('one');self.plan(['one']);self.enqueue_step(event)
        claim=self.queue.claim('step');token=self.queue.session(claim)
        self.app.mock_send_outcome='unknown'
        operation=prepare_message(self.app,token,self.send_args(event))['data']
        operation=dispatch(self.app,'f',operation['id']);self.assertEqual(operation['stage'],'unknown')
        record_outcome(self.app,token,{'request_id':'unknown-wait','outcome':'waiting',
            'refs':[{'kind':'event','id':event['id'],'version':event['version']},
                    {'kind':'operation','id':operation['id'],'version':operation['version']}],
            'reason':'Original receipt uncertain','remaining_work':['Original receipt'],
            'resume_condition':'Original operation receipt changes'})
        self.queue.finish(claim,'waiting')
        self.assertIsNone(self.queue.claim('unchanged'))
        message=self.inbound('A new unrelated acknowledgement')
        fresh=self.queue.claim('fresh');self.assertEqual(fresh['job']['message_id'],message['id'])
        self.interpret(message,False);self.queue.finish(fresh)
        self.assertIsNone(self.queue.claim('still-unchanged'))
        reconcile(self.app,'f',operation['id'])
        resumed=self.queue.claim('receipt-updated')
        self.assertEqual(resumed['job']['id'],claim['job']['id']);self.assertTrue(resumed['job']['recovery_only'])
        recovery_token=self.queue.session(resumed)
        _,binding=self.app.auth(recovery_token,{'service'})
        self.assertTrue(binding['no_contact']);self.assertEqual(binding['reply_scope'],{})
        with self.assertRaises(BusinessError) as error:prepare_message(self.app,recovery_token,self.send_args(event))
        self.assertEqual(error.exception.code,'NO_CONTACT_RUN')
        self.assertEqual(channel_for(self.app).count(),1)

    def test_service_state_has_small_index_and_only_selected_execution(self):
        one=self.event('one');two=self.event('two');self.plan(['one','two'])
        state={'events':[one,two],'plans':self.app.store.list('plan','f'),'reports':[],
               'pending_inputs':[],'messages':[],'operations':[]}
        empty=project_service_state(self.app,'f',state,{})
        self.assertEqual(empty['events'],[]);self.assertEqual(empty['execution_agenda'],[])
        self.assertEqual({e['id'] for e in empty['responsibility_index']},{'one','two'})
        self.assertNotIn('next_action',empty['responsibility_index'][0])
        chosen=project_service_state(self.app,'f',state,{'event_id':'one'})
        self.assertEqual([e['id'] for e in chosen['events']],['one'])
        self.assertEqual(chosen['events'][0]['completion'],one['completion'])
        self.assertEqual([i['event_id'] for i in chosen['plans'][0]['items']],['one'])
        self.assertNotIn('two',str(chosen['execution_agenda']))

    def test_context_on_demand_event_read_does_not_grant_send_authority(self):
        self.event('one');self.event('two');self.plan(['one','two'])
        self.inbound();claim=self.queue.claim('service');token=self.queue.session(claim)
        context=build_context(self.app,token,claim['job'])
        self.assertEqual(context['events'],[])
        self.assertEqual(context['service_evidence_contract']['responsibility_contracts'],[])
        focused=read_context(self.app,token,{'event_id':'one'})
        self.assertEqual([e['id'] for e in focused['events']],['one'])
        self.assertEqual([e['event_id'] for e in focused['service_evidence_contract']['responsibility_contracts']],['one'])
        _,binding=self.app.auth(token,{'service'});self.assertIsNone(binding.get('event_id'))


if __name__=='__main__':unittest.main()
