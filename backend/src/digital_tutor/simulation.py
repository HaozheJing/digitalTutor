"""Two-day deterministic acceptance fixture; never binds real student identifiers."""
import json
from .application import Application, Clock
from .domain import require
from .jobs import JobQueue
from .mock_channel import channel_for
from .outbox import InjectedCrash, dispatch, reconcile, drain
from .scheduler import Scheduler, Worker
from .tools import ToolRegistry
from .wechat import prepare_message


class Scenario:
    def __init__(self, path):
        self.path=path; self.app=Application(path,Clock('2026-10-03T08:00:00+08:00'))
        require(not self.app.store.connection.execute('SELECT 1 FROM families').fetchone(),'SIMULATION_REQUIRES_EMPTY_DB','请使用独立空库')
        self.app.create_family('sim-family','sim-uid','mock:sim-family')
        self.trace=[]; self.counter=0; self.crashed=False

    def key(self):
        self.counter+=1; return 'sim-'+str(self.counter)

    def call(self, tools, name, args):
        result=tools.call(name,args)
        self.trace.append({'at':self.app.clock.iso(),'tool':name,'ok':result['ok']})
        require(result['ok'],'SCENARIO_FAILED',str(result.get('error')))
        return result['result']

    def receive(self, label, body):
        token=self.app.open_session('sim-family','channel')
        try:
            return self.call(ToolRegistry(self.app,token),'wechat.receive_message',
                             {'external_id':label,'body':body,'occurred_at':self.app.clock.iso()})['data']
        finally:
            self.app.revoke_session(token)

    def report(self, tools, event, refs, changes, fact):
        self.call(tools,'progress.submit',{'request_id':self.key(),'report_id':self.key(),
                  'event_ids':[event['id']],'facts':[fact],'evidence_refs':refs,'suggested_update':json.dumps(changes)})

    def daily(self, job, tools, state):
        events=[]; refs=[e['id'] for e in state['events'] if e['status'] not in {'done','cancelled'}]; decisions=[]
        if not state['events']:
            events=[{'client_ref':'makeup','goal_key':'makeup','goal_text':'完成模拟原课节',
                     'completion':{'kind':'goal','lesson_refs':['sim-lesson']},'due_at':'2026-10-03T20:00:00+08:00'},
                    {'client_ref':'feedback','goal_key':'feedback','goal_text':'交付模拟两部分反馈',
                     'completion':{'kind':'delivery','required_part_ids':['overview','example']},
                     'next_action':{'action_kind':'deliver_feedback','target_refs':['overview','example'],'purpose':'交付已核对反馈'}}]
            refs=['makeup','feedback']
        for pending in state['pending_inputs']:
            if pending['disposition']=='pending':
                # Preserve the new request for later work; do not lose its original deadline.
                decisions.append({'input_id':pending['id'],'disposition':'retained','reason':'待取得作品，不虚构反馈材料'})
        self.call(tools,'planner.commit_daily',{'request_id':self.key(),'business_date':job['business_date'],
                  'expected_family_version':state['family_version'],'new_events':events,'plan_refs':refs,'pending_decisions':decisions})

    def service(self, job, tools, state):
        msg=next(m for m in state['messages'] if m['id']==job['message_id'])
        if not msg['interpreted']:
            msg=self.call(tools,'message.interpret',{'request_id':self.key(),'message_id':msg['id'],'expected_version':msg['version'],
                'intents':[{'intent_id':'answer','text':'当前独立问题'},{'intent_id':'arrange','text':'本次安排','requires_reply':False}]})['data']
        if msg['external_id']=='sick':
            control=self.call(tools,'control.apply',{'request_id':self.key(),'action_kinds':['remind_makeup'],
                     'reason':'恢复后家长联系','source_message_id':msg['id'],'source_version':msg['version']})['data']
            event=next(e for e in state['events'] if e['goal_key']=='makeup')
            self.report(tools,event,[{'kind':'control','id':control['id'],'version':control['version']}],
                        {'status':'waiting','waiting':{'reason':'生病暂停','resume_condition':'家长主动联系'}},'模拟家庭要求暂停')
            self.call(tools,'planning.record_pending',{'request_id':self.key(),'source_message_id':msg['id'],'source_version':msg['version'],
                      'request':'之后看看写作作品','original_deadline':'下周五','remaining_work':['等待作品']})
        if not msg['intents'][0]['reply_operation_refs']:
            current=self.call(tools,'state.read',{})
            self.call(tools,'wechat.send_message',{'request_id':self.key(),'expected_family_version':current['family_version'],
                      'action_kind':'reply','body':'模拟答复：已收到您的当前问题，原催课暂停继续有效。',
                      'reply_to':[{'message_id':msg['id'],'intent_id':'answer'}]})

    def step(self, job, tools, state):
        event=next(e for e in state['events'] if e['id']==job['event_id'])
        proofs=[]
        for part in event['completion']['required_part_ids']:
            existing=next((o for o in self.app.store.list('operation','sim-family') if o['event_id']==event['id'] and o['part_id']==part and o['stage']!='cancelled'),None)
            if existing:
                op=reconcile(self.app,'sim-family',existing['id'])
            else:
                args={'request_id':self.key(),'expected_family_version':self.app.store.family('sim-family')['version'],
                      'action_kind':'deliver_feedback','event_id':event['id'],'part_id':part,'step_id':job['step_key'],
                      'body':'模拟反馈部分：'+part}
                if part=='example' and not self.crashed:
                    self.crashed=True
                    op=prepare_message(self.app,tools.token,args)['data']
                    dispatch(self.app,'sim-family',op['id'],failpoint='after_transport')
                op=self.call(tools,'wechat.send_message',args)['data']
            require(op['stage'] in {'sent','delivered'},'SCENARIO_FAILED','交付仍需核实')
            proofs.append({'kind':'operation','id':op['id'],'version':op['version']})
        self.report(tools,event,proofs,{'status':'done'},'所有模拟反馈部分已确认发送')

    def handler(self, job, tools):
        self.trace.append({'at':self.app.clock.iso(),'job_kind':job['kind']})
        state=self.call(tools,'state.read',{})
        if job['kind']=='daily':
            self.daily(job,tools,state)
        elif job['kind']=='service':
            self.service(job,tools,state)
        elif job['kind']=='progress':
            report=next(r for r in state['reports'] if r['id']==job['report_key'])
            changes=json.loads(report['payload']['suggested_update'])
            self.call(tools,'planner.commit_progress',{'request_id':self.key(),'report_id':report['report_id'],'revision':report['revision'],
                      'expected_family_version':state['family_version'],'event_updates':[
                          {'event_id':eid,'expected_version':next(e['version'] for e in state['events'] if e['id']==eid),
                           'changes':changes,'evidence_refs':report['payload']['evidence_refs']} for eid in report['event_ids']]})
        elif job['kind']=='step':
            self.step(job,tools,state)
        else:
            return {'no_contact':True,'reason':'内部核验暂无新事实'}
        return {}

    def pump(self):
        Scheduler(self.app).tick(); drain(self.app)
        worker=Worker(self.app,self.handler)
        for _ in range(30):
            result=worker.run_once()
            if not result['ran']:
                return
            require(result['outcome'] not in {'failed','rejected'},'SCENARIO_FAILED',str(result))
        raise RuntimeError('Simulation did not reach idle')

    def run(self):
        try:
            self.pump()  # daily; steps discovered on next tick
            self.app.clock=Clock('2026-10-03T09:00:00+08:00')
            self.receive('sick','孩子病了先别催，入口在哪里？下周五看看写作作品。')
            try:
                self.pump()
            except InjectedCrash:
                pass
            require(self.crashed,'SCENARIO_FAILED','未注入目标故障')
            before=channel_for(self.app).count()
            self.app.close(); self.app=Application(self.path,Clock('2026-10-03T09:02:00+08:00'))
            self.pump()
            require(channel_for(self.app).count()==before,'SCENARIO_FAILED','崩溃恢复重复发送')
            self.app.clock=Clock('2026-10-03T21:01:00+08:00')
            self.receive('late','另一个入口问题，明天答复即可。')
            self.pump()
            at_cutoff=channel_for(self.app).count()
            self.app.close(); self.app=Application(self.path,Clock('2026-10-04T08:00:00+08:00'))
            self.pump()
            state=self.app.state(self.app.open_session('sim-family','service'))
            events={e['goal_key']:e for e in state['events']}
            require(events['makeup']['status']=='waiting' and events['feedback']['status']=='done','SCENARIO_FAILED','原责任或交付状态不正确')
            require(len(state['plans'])==2 and len(state['pending_inputs'])==1,'SCENARIO_FAILED','跨日记录缺失')
            require(channel_for(self.app).count()==at_cutoff+1,'SCENARIO_FAILED','次日答复重复或缺失')
            idle_count=len(self.trace); self.pump()
            require(len(self.trace)==idle_count,'SCENARIO_FAILED','空扫描重复调用执行器')
            return {'scenario':'two-day-service','channel':'mock','real_model_used':False,'real_wechat_used':False,
                    'plans':len(state['plans']),'event_statuses':{k:e['status'] for k,e in events.items()},
                    'pending_original_deadline':state['pending_inputs'][0]['original_deadline'],
                    'transport_operations':channel_for(self.app).count(),'crash_recovery_no_resend':True,
                    'cutoff_deferred_reply':True,'idle_no_handler_calls':True,'trace':self.trace}
        finally:
            self.app.close()
