"""Frozen-clock service plan and explicit opt-in automatic highlighters."""
import json
import sqlite3
import sys
import time
from contextlib import closing
import sql_tools as s
import test_mode as tm
import fixture_query as fq
import observability as audit


def initialize():
    audit.initialize()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('''CREATE TABLE IF NOT EXISTS test_checks(
          run_id TEXT PRIMARY KEY,checked_at TEXT,wall_at REAL,result TEXT)''')


def set_automatic(enabled):
    if type(enabled) is not bool:
        raise ValueError('自动执行设置必须为布尔值')
    if enabled and __import__('manual_workbench').configured():raise ValueError('此工作台仅供手动检验，不能开启自动发送')
    state=tm.state()
    state['auto_highlights']=enabled
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state),))
    return {'status':'updated','auto_highlights':enabled}


def plan(day=None):
    state=tm.state();day=day or state['as_of'][:10]
    fq.query(state['as_of'],day)  # Date range validation, no future outcome inference.
    if state.get('agent_planning'):
        import agent_plan
        return agent_plan.view(day)
    fixture=json.loads(fq.FILE.read_text(encoding='utf-8'))
    calendar=next(row for row in fixture['course_calendar'] if row['date']==day)
    current=day==state['as_of'][:10];at=state['as_of']
    receipts={row['candidate_id']:row for row in s.read(
        'SELECT r.*,d.status AS delivery_status FROM proactive_receipts r LEFT JOIN deliveries d ON r.delivery_key=d.action_key')} if current else {}
    checks=s.read('SELECT * FROM test_checks WHERE run_id=?',(state['run_id'],)) if current and s.read("SELECT 1 FROM sqlite_master WHERE name='test_checks'") else []
    last=json.loads(checks[0]['result']) if checks else None
    items=[{'time':'07:30','title':'读取当日课程与服务条件','kind':'internal',
            'status':'checked' if checks else 'planned',
            'reason':'冻结时钟下由测试调度器检查，内部操作不向微信发送消息。'}]
    if calendar['is_course_day']:
        items.append({'time':'17:00','title':f"第{calendar['lesson_no']}课开始",'kind':'course',
                      'status':'reference','reason':'课程表节点，不是智能体发消息任务。'})
        items.append({'time':'18:00','title':'今日课程截止 / 待补课检查','kind':'course',
                      'status':'reference','reason':'当前测试版展示学情，不自动发送催课消息。'})
    assets=[h for h in fixture['highlight_assets'] if h.get('trigger_status')=='eligible_fixture' and h['candidate_at'][:10]==day]
    for h in sorted(assets,key=lambda h:h['candidate_at']):
        receipt=receipts.get('proactive:fixture:'+state['run_id']+':'+h['id'])
        if not current:
            status,reason='planned','该日测试安排预览；切到这一天后才核对条件并执行。'
        elif receipt:
            status,reason=receipt['delivery_status'] or 'unknown','已创建交付，以下显示平台回执；不会盲目重发。'
        elif at<h['candidate_at']:
            status,reason='not_due','尚未到触发时间；不提前使用未来完课或评选结果。'
        elif at>h['expires_at']:
            status,reason='expired','已经超过素材有效期。'
        elif not state.get('auto_highlights',False):
            status,reason='manual','条件已到检查时点；自动执行未开启，可手动发送。'
        elif last and last.get('status') in ('deferred','skipped','error'):
            status,reason=last['status'],last.get('reason','等待重新检查')
        else:
            status,reason='pending','等待调度器检查；微信对话处理结束后执行。'
        items.append({'time':h['candidate_at'][11:16],'title':h['medal_name'],'kind':'highlight',
                      'highlight_id':h['id'],'status':status,'reason':reason,
                      'evidence':h['evidence'] if current and at>=h['candidate_at'] else '到点后读取事实',
                      'expires_at':h['expires_at']})
    if not assets:
        items.append({'time':'19:00','title':'高光条件检查','kind':'internal',
                      'status':'checked' if checks else 'planned',
                      'reason':'该日没有配置可触发的首月高光，不补造全勤、连续21天或L3成就。'})
    return {'day':day,'source':'test_scheduler','auto_highlights':state.get('auto_highlights',False),
            'items':sorted(items,key=lambda item:item['time']),'last_check':checks[0] if checks else None,
            'model_plans':[json.loads(r['body']) for r in s.read('SELECT body FROM daily_plans WHERE business_date=?',(day,)) if json.loads(r['body']).get('source')!='test_scheduler']}


def tick():
    initialize()
    state=tm.state()
    if not state:
        return {'status':'disabled'}
    if state.get('agent_planning') or __import__('service_workflow').enabled(state):
        return {'status':'agent_owned','reason':'模型规划已接管，旧固定调度器不创建交付'}
    prior=s.read('SELECT * FROM test_checks WHERE run_id=?',(state['run_id'],))
    if prior and time.time()-prior[0]['wall_at']<15:
        return {'status':'waiting'}
    result={'status':'manual','reason':'自动执行未开启，请手动触发。'}
    if state.get('auto_highlights',False):
        import wechat_test as w
        # Only retry unresolved guards. Transport attempts have their own immutable ledger.
        pending=[h for h in tm.snapshot()['available_highlights'] if not s.read(
            'SELECT 1 FROM proactive_receipts WHERE dedupe_key=?',(state['run_id']+':'+h['dedupe_key'],))]
        if pending:
            result=w.trigger_highlights(manual=False,origin='scheduler')
        else:
            result={'status':'idle','reason':'没有新的到点高光；未到时间或本轮已经交付。'}
    if not prior:
        audit.invoke('daily_service_plan',{},lambda:plan(),origin='scheduler')
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('INSERT OR REPLACE INTO test_checks VALUES(?,?,?,?)',
                  (state['run_id'],s.now(),time.time(),s.encoded(result)))
    return result


if __name__=='__main__':
    command=sys.argv[1]
    print(s.encoded(tick() if command=='tick' else set_automatic(json.loads(sys.argv[2])) if command=='automatic' else plan(sys.argv[2] if len(sys.argv)>2 else None)))
