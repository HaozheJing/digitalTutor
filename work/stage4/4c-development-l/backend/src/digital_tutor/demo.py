"""Deterministic, synthetic-only end-to-end stage-one demonstration."""
from .application import Clock
from .domain import require
from .storage import encode
from .tools import ToolRegistry


def seed_learning(app, uid='demo-uid', other_uid='demo-other-uid'):
    meta={'batch_id':'synthetic-stage1','imported_at':app.clock.iso(),
          'coverage_through':None,'source_timezone':None,'course_binding':None,'synthetic':True}
    with app.store.transaction() as db:
        db.execute('INSERT INTO imports VALUES(?,?)',(meta['batch_id'],encode(meta)))
        for learner in (uid,other_uid):
            for day in range(1,4):
                db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (meta['batch_id'],learner,day,'attended','completed' if day==1 else 'incomplete',
                            'not_configured','not_configured','not_checked',None,None,'{"synthetic":true}'))


def run_demo(app):
    require(not app.store.connection.execute('SELECT 1 FROM families LIMIT 1').fetchone(),
            'DEMO_REQUIRES_EMPTY_DATABASE','演示要求独立空数据库，请选择新路径')
    app.clock=Clock('2026-10-03T08:00:00+08:00')
    app.create_family('family-demo','demo-uid','mock:family-demo')
    seed_learning(app)
    trace=[]
    def call(token,name,args):
        result=ToolRegistry(app,token).call(name,args)
        trace.append({'tool':name,'ok':result['ok']})
        require(result['ok'],'DEMO_FAILED',str(result.get('error')))
        return result['result']
    service=app.open_session('family-demo','service')
    channel=app.open_session('family-demo','channel')
    planner=app.open_session('family-demo','planner_daily')
    daily_args={'request_id':'daily-demo','business_date':'2026-10-03','expected_family_version':1,
                'new_events':[{'client_ref':'makeup','goal_key':'makeup-lesson-demo','goal_text':'完成虚构课节补课',
                               'completion':{'kind':'goal','lesson_refs':['lesson-demo']},
                               'due_at':'2026-10-03T20:00:00+08:00'}],'plan_refs':['makeup']}
    daily=call(planner,'planner.commit_daily',daily_args)
    eid=daily['data']['event_ids']['makeup']
    repeated=call(planner,'planner.commit_daily',daily_args)
    msg=call(channel,'wechat.receive_message',{'external_id':'mock-001','body':'孩子病了，恢复后我联系，先别催。课程入口在哪里？',
                                             'occurred_at':'2026-10-03T09:00:00+08:00'})['data']
    app.clock=Clock('2026-10-03T09:00:00+08:00')
    interpreted=call(service,'message.interpret',{'request_id':'interpret-demo','message_id':msg['id'],'expected_version':1,
                      'intents':[{'intent_id':'pause','text':'暂停补课提醒','requires_reply':False},
                                 {'intent_id':'entry','text':'询问课程入口'}]})['data']
    control=call(service,'control.apply',{'request_id':'pause-demo','action_kinds':['remind_makeup'],
                     'reason':'恢复后由家长联系','source_message_id':msg['id'],'source_version':interpreted['version']})['data']
    all_messages=call(service,'wechat.read_messages',{'mode':'all'})
    latest=call(service,'wechat.read_messages',{'mode':'latest'})
    family_version=app.store.family('family-demo')['version']
    reply=call(service,'wechat.send_message',{'request_id':'reply-demo','expected_family_version':family_version,
               'body':'模拟答复：入口信息需要由课程资料工具核实；补课提醒已暂停。','action_kind':'reply',
               'reply_to':[{'message_id':msg['id'],'intent_id':'entry'}]})['data']
    evidence={'kind':'control','id':control['id'],'version':control['version']}
    call(service,'progress.submit',{'request_id':'report-demo','report_id':'report-demo','revision':1,'event_ids':[eid],
          'facts':['家长要求暂停补课提醒，恢复后主动联系'],'remaining_work':['lesson-demo'],'evidence_refs':[evidence]})
    pending_before=app.event('family-demo',eid)['update_pending']
    progress=app.open_session('family-demo','planner_progress','report-demo',1)
    call(progress,'planner.commit_progress',{'request_id':'progress-demo','report_id':'report-demo','revision':1,
             'expected_family_version':app.store.family('family-demo')['version'],
             'event_updates':[{'event_id':eid,'expected_version':1,'changes':{'status':'waiting',
                'waiting':{'reason':'家庭暂停','resume_condition':'家长恢复后主动联系'}},'evidence_refs':[evidence]}]})
    blocked=ToolRegistry(app,service).call('wechat.send_message',{'request_id':'blocked-demo',
             'expected_family_version':app.store.family('family-demo')['version'],'body':'模拟提醒','action_kind':'remind_makeup','event_id':eid})
    require(not blocked['ok'] and blocked['error']['code']=='CONTROL_ACTIVE','DEMO_FAILED','暂停未拦截')
    sql=call(service,'learning.query_sql',{'sql':'SELECT day, completion_status FROM learning_days ORDER BY day'})
    state=call(service,'state.read',{})
    return {'stage':1,'channel':'mock_wechat','clock':app.clock.iso(),'family':'family-demo',
            'duplicate_daily':repeated['outcome'],'all_count_before_reply':all_messages['returned_count'],
            'latest_count_before_reply':latest['returned_count'],'read_marks_answered':False,
            'reply_stage':reply['stage'],'active_controls':len([c for c in state['controls'] if c['status']=='active']),
            'update_pending_before_commit':pending_before,'event':state['events'][0],
            'paused_send_error':blocked['error']['code'],'sql_rows':sql['rows'],
            'message_count':len(state['messages']),'operation_count':len(state['operations']),
            'tool_calls':trace,'real_model_used':False,'real_wechat_used':False}
