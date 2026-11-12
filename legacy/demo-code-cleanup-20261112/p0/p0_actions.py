"""Operator-only actions in a verified, frozen P0 experiment workspace."""
import json
import sqlite3
import sys
from contextlib import closing
import sql_tools as s


def table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone())


def snapshot():
    import experiment_runtime as er, operator_alerts as oa, preference_memory as pm
    v=er.snapshot()
    result={k:v[k] for k in ['business_time','run_id','experiment_run_id','session_id','tasks','deliveries','parts','receipts','preference_memory']}
    result['assembly']={k:v['assembly'][k] for k in ['code_commit','config_version','scenario_version','model','components']}
    result['alerts']=oa.list_alerts()
    result['tools']=[{k:x.get(k) for k in ['name','input','output','status','origin','started_at']} for x in v['tool_trace'][-45:]]
    with closing(sqlite3.connect(s.DB)) as c:
        c.row_factory=sqlite3.Row
        result['events']=[dict(x) for x in c.execute('SELECT id,kind,status,attempts FROM events ORDER BY rowid DESC LIMIT 20')]
        result['messages']=[dict(x) for x in c.execute('SELECT source_id,speaker,body,visible FROM messages ORDER BY rowid DESC LIMIT 30')][::-1]
        result['journal']=[dict(x) for x in c.execute('SELECT seq,kind,source_id,body FROM memory_journal ORDER BY seq DESC LIMIT 30')] if table(c,'memory_journal') else []
    sessions=s.R/'runtime/memory-sessions.jsonl'
    result['memory_sessions']=[json.loads(x) for x in sessions.read_text().splitlines()] if sessions.exists() else []
    result['memory_recall']=pm.recall()
    result['simulation']=True
    return result


def idle():
    import console_control
    with closing(sqlite3.connect(s.DB)) as c:console_control.idle(c,receipt_complete=True)


def late_receipt(operation_id):
    """Confirm an already attempted mock operation. Never invent an unsent part."""
    import wechat_test as w, service_workflow as wf, operator_alerts as oa
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        if not table(c,'experiment_receipts'):raise ValueError('尚无模拟发送记录')
        row=c.execute('SELECT e.body,e.applied,o.status FROM experiment_receipts e JOIN test_outbox o ON o.id=e.operation_id WHERE e.operation_id=?',(operation_id,)).fetchone()
        if not row or row[2] not in ('unknown','accepted','sent'):raise ValueError('只能补录已尝试发送的本场景分段回执')
        if row[1]:return {'duplicate':True,'operation_id':operation_id}
        receipt=json.loads(row[0])
        if receipt.get('simulation') is not True or receipt.get('externalRequestId')!=operation_id:raise ValueError('模拟回执绑定错误')
        receipt['sendCode']=0
        w.observe(c,receipt)
        c.execute("UPDATE experiment_receipts SET body=?,outcome='sent',applied=1 WHERE operation_id=?",(s.encoded(receipt),operation_id))
        wf.reconcile(c)
    oa.reconcile()
    return {'operation_id':operation_id,'status':'simulated_final_receipt_applied','simulation':True}


def execute(action,data):
    import experiment_runtime as er, test_mode as tm, agent_plan as ap, service_workflow as wf, operator_alerts as oa
    manifest=er.verify()
    if not manifest or not manifest['scenario_version'].startswith('p0-'):raise ValueError('仅允许P0隔离验收场景')
    if action=='state':return snapshot()
    if action=='idle':idle();return {'idle':True}
    if action=='stage':
        idle()
        candidates=[x for x in ap.tasks() if x['status']=='planned' and x['kind'] not in ('plan','completion_plan','case_review')]
        if len(candidates)!=1:raise ValueError('需恰好一个待执行验收任务')
        import console_control
        console_control.execute(candidates[0]['id'])
        task=ap.claim()
        if not task or task['id']!=candidates[0]['id']:raise ValueError('任务未成功领取')
        auth=wf.prepare_execution(task['id'])
        (s.R/'runtime/p0-staged.json').write_text(s.encoded({'task_id':task['id'],**auth}))
        return {'status':'staged_before_model_dispatch','task_id':task['id'],'note':'宿主固定发送前现场，未模拟模型自然决策'}
    if action=='takeover':
        task=ap.active()
        if not task:raise ValueError('先点击准备发送前断点，或等待真实任务开始')
        return oa.request_alert('人工业务验收：操作员接管当前事项',task_id=task['id'],proposed_reply='请人工核对本次事项后填写实际回复。')
    if action=='probe':
        before=s.read('SELECT id,status FROM test_outbox')
        checks={}
        for name in ['deliver_proactive','workflow_fulfill']:
            try:oa.guard_model_delivery(name,{});checks[name]='allowed'
            except ValueError as e:checks[name]=str(e)
        staged=s.R/'runtime/p0-staged.json'
        if staged.exists():
            try:wf.execution_authority(json.loads(staged.read_text())['execution_token']);checks['old_execution']='allowed'
            except ValueError as e:checks['old_execution']=str(e)
        return {'checks':checks,'outbox_unchanged':before==s.read('SELECT id,status FROM test_outbox'),'note':'直接探测实际发送门禁，不伪造模型回复'}
    if action=='human_reply':
        return oa.send_reply(tm.state()['run_id'],data['alert_id'],data['body'],data['revision'],data['request_id'])
    if action=='late_receipt':return late_receipt(data['operation_id'])
    if action=='flush':
        import wechat_test
        before=s.read('SELECT id,status FROM test_outbox')
        result=wechat_test.flush()
        return {'result':result,'before':before,'after':s.read('SELECT id,status FROM test_outbox'),'note':'沿用原flush规则，不重置unknown或生成替代发送键'}
    if action=='night':
        idle();state=tm.state();target=state['as_of'][:10]+'T22:00:00+08:00'
        if state['as_of']>=target:raise ValueError('本场景已到夜间，请查看整理状态；重复验收请开新场景')
        tm.advance(target)
        return __import__('preference_memory').tick()
    if action=='advance':
        idle();return tm.advance(data['as_of'])
    if action=='execute':
        return __import__('console_control').execute(data['task_id'])
    raise ValueError('未开放的验收操作')


if __name__=='__main__':
    try:print(s.encoded({'ok':True,'result':execute(sys.argv[1],json.loads(sys.argv[2]) if len(sys.argv)>2 else {})}))
    except (ValueError,KeyError) as e:print(s.encoded({'ok':False,'error':str(e)}))
