"""Daily planning cannot enable active delivery, including already queued operations."""
import json
from pathlib import Path
import subprocess
import sys
from digital_tutor.application import Application,Clock
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue
from digital_tutor.mock_channel import channel_for
from digital_tutor.outbox import dispatch,reconcile
from digital_tutor.scheduler import Scheduler
from digital_tutor.scheduling import step_key
from digital_tutor.wechat import prepare_message,read_messages
import test_stage1 as baseline


class DailySendGateTests(baseline.CoreTests):
    def claim_step(self,event):
        # This event is an explicit historical fixture created while legacy
        # mode was active; obtain real strict execution authority for dispatch.
        with self.app.store.transaction():
            current=self.app.event('f',event)
            current['next_action']={'action_kind':'remind_makeup','target_refs':current['completion']['lesson_refs'],
                                    'purpose':'Historical approved reminder'}
            current=self.app.store.put('event',event,'f',current,current['version'])
        self.app.mode='full';self.app.strict_runtime=True;self.app.runtime_clock=Clock()
        queue=JobQueue(self.app)
        with self.app.store.transaction():
            queue.enqueue('f','fixture-step-'+event,{'kind':'step','event_id':event,
                'step_key':step_key(current),'event_version':current['version']})
        claim=queue.claim('isolated-step',120)
        self.assertEqual(claim['job']['kind'],'step')
        token=queue.session(claim)
        return token,{**self.send_event(event),'step_id':step_key(current)}

    def claim_service(self,mode='full'):
        message=self.incoming()
        self.app.mode=mode;self.app.strict_runtime=True;self.app.runtime_clock=Clock()
        queue=JobQueue(self.app);claim=queue.claim('isolated-worker',120)
        self.assertEqual(claim['job']['kind'],'service')
        token=queue.session(claim)
        read_messages(self.app,token,{'mode':'all'})
        return message,token,claim

    def test_daily_claims_service_and_daily_not_historical_active_work(self):
        self.app.mode='daily'
        queue=JobQueue(self.app)
        with self.app.store.transaction():
            for kind in ('step','review','progress'):
                queue.enqueue('f','historical-'+kind,{'kind':kind})
        message=self.incoming()
        Scheduler(self.app).tick()
        first=queue.claim('worker')
        self.assertEqual(first['job']['kind'],'service')
        queue.finish(first,'succeeded')
        second=queue.claim('worker')
        self.assertEqual(second['job']['kind'],'daily')
        queue.finish(second,'succeeded')
        # Independent family can have its own daily; no active kind is admitted.
        while (claim:=queue.claim('worker')):
            self.assertEqual(claim['job']['kind'],'daily')
            queue.finish(claim,'succeeded')
        self.assertEqual(channel_for(self.app).count(),0)
        self.assertTrue(all(j['status']=='ready' for j in self.app.store.list('job','f') if j['kind'] in {'step','review','progress'}))

    def test_daily_service_cannot_prepare_event_but_can_answer_authorized_input(self):
        event=self.create()['a']
        message,token,_=self.claim_service('daily')
        with self.assertRaises(BusinessError) as error:
            prepare_message(self.app,token,self.send_event(event))
        self.assertEqual(error.exception.code,'DAILY_MODE')
        self.assertEqual(self.app.store.list('operation','f'),[])
        operation=prepare_message(self.app,token,self.reply(message))['data']
        self.assertEqual(dispatch(self.app,'f',operation['id'])['stage'],'sent')
        self.assertEqual(channel_for(self.app).count(),1)
        saved=self.app.store.get('message',message['id'],'f')
        self.assertEqual(saved['intents'][0]['reply_operation_refs'],[operation['id']])

    def test_daily_capability_cannot_be_upgraded_by_later_full_mode(self):
        event=self.create()['a'];_,token,_=self.claim_service('daily')
        self.app.mode='full'
        with self.assertRaises(BusinessError) as error:
            prepare_message(self.app,token,self.send_event(event))
        self.assertEqual(error.exception.code,'DAILY_MODE')
        self.assertEqual(channel_for(self.app).count(),0)

    def test_daily_capability_still_answers_input_after_app_full(self):
        message,token,_=self.claim_service('daily')
        self.app.mode='full'
        operation=prepare_message(self.app,token,self.reply(message))['data']
        self.assertEqual(dispatch(self.app,'f',operation['id'])['stage'],'sent')
        self.assertEqual(channel_for(self.app).count(),1)

    def test_actual_child_dispatch_blocks_full_capability_after_daily_downgrade(self):
        event=self.create()['a'];token,args=self.claim_step(event)
        operation=prepare_message(self.app,token,args)['data']
        clock=Path(self.tmp.name)/'shared-clock.json'
        clock.write_text(json.dumps({'now':self.app.clock.iso()}),encoding='utf-8')
        code=('import json,sys; from digital_tutor.application import Application,Clock; '
              'from digital_tutor.outbox import dispatch; '
              'a=Application(sys.argv[1],Clock(shared_path=sys.argv[2]),mode="daily",strict_runtime=True); '
              'o=dispatch(a,"f",sys.argv[3]); '
              'print(json.dumps({"stage":o["stage"],"reason":o.get("cancel_reason"),"dispatch_count":o["dispatch_count"]})); a.close()')
        result=json.loads(subprocess.check_output([sys.executable,'-c',code,str(self.path),str(clock),operation['id']],text=True))
        self.assertEqual(result,{'stage':'cancelled','reason':'DAILY_MODE','dispatch_count':0})
        self.assertEqual(channel_for(self.app).count(),0)
        version=self.app.store.family('f')['version']
        op_version=self.app.store.get('operation',operation['id'],'f')['version']
        for index in range(20):
            restarted=Application(self.path,Clock(shared_path=clock),mode='daily',strict_runtime=True)
            try:
                self.assertEqual(dispatch(restarted,'f',operation['id'])['stage'],'cancelled')
                self.assertEqual(restarted.store.family('f')['version'],version)
                self.assertEqual(restarted.store.get('operation',operation['id'],'f')['version'],op_version)
                self.assertEqual(channel_for(restarted).count(),0)
            finally:restarted.close()

    def test_daily_can_reconcile_prior_unknown_without_sending_again(self):
        event=self.create()['a'];token,args=self.claim_step(event)
        self.app.mock_send_outcome='unknown'
        operation=prepare_message(self.app,token,args)['data']
        self.assertEqual(dispatch(self.app,'f',operation['id'])['stage'],'unknown')
        before=channel_for(self.app).count()
        self.app.mode='daily'
        self.assertEqual(reconcile(self.app,'f',operation['id'])['stage'],'sent')
        self.assertEqual(channel_for(self.app).count(),before)
        self.assertEqual(self.app.store.get('operation',operation['id'],'f')['dispatch_count'],1)


for name in baseline.CoreTests.__dict__:
    if name.startswith('test_'):setattr(DailySendGateTests,name,None)
