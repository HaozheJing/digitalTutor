"""Read-only workbench progress. No model requests or message dispatch."""
import contextlib,datetime as dt,sqlite3,time
from pathlib import Path

TITLES={'plan':'制定当日服务计划','completion_plan':'评估完课后的服务',
 'plan-service-day':'制定当日服务计划','close-week-reading-gap':'周五阅读收尾',
 'invite-reading-interaction':'阅读互动','plan-weekend-makeup':'补读沟通',
 'weekly-learning-review':'周学习总结','welcome-back-to-reading':'恢复阅读欢迎',
 'support-zero-completion':'落后学习关怀'}

def title(task):
    return TITLES.get(task.get('skill')) or TITLES.get(task.get('kind')) or task.get('title') or '处理服务事项'

def activities(lab,snapshot):
    run=snapshot['run'];now=time.time();result=[]
    models=lab.read("SELECT * FROM model_attempts WHERE run_id=? AND status IN ('queued','running') ORDER BY created",(run['id'],))
    parts=lab.read("SELECT family_id,status FROM outbox WHERE run_id=? AND status IN ('queued','submitting','accepted','unknown')",(run['id'],))
    for f in snapshot['families']:
        tasks=[];db=Path(f['workspace'])/'runtime/live.sqlite'
        if db.exists():
            with contextlib.closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as c:
                c.row_factory=sqlite3.Row
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='service_tasks'").fetchone():
                    tasks=[dict(t) for t in c.execute("SELECT * FROM service_tasks WHERE status IN ('planned','running','deferred','awaiting_delivery') ORDER BY due_at")]
        due=[t for t in tasks if t['due_at']<=run['as_of']<=t['expires_at']] if not (f['held'] or f['paused']) else []
        requests=[m for m in models if m['family_id']==f['family_id']]
        outgoing=[p for p in parts if p['family_id']==f['family_id']]
        running=next((m for m in requests if m['status']=='running'),None)
        queued=next((m for m in requests if m['status']=='queued'),None)
        active=next((t for t in tasks if t['status']=='running'),None)
        request=running or queued
        if request:
            active=next((t for t in tasks if t['id']==request['owner_id']),active)
        topic=title(active) if active else ('处理学生消息' if request and request['kind']=='interactive' else '理解学生语音' if request and request['kind']=='voice' else '整理内部记忆' if request and request['kind']=='maintenance' else '处理工具与服务结果')
        phase='idle';summary='等待学生消息或下一事件';since=None
        if running:
            phase='model';summary='模型处理中 · '+topic;since=running['started']
        elif queued and not (run['paused'] or f['held']):
            phase='model_queue';summary='等待模型名额 · '+topic;since=queued['created']
        elif any(p['status']=='unknown' for p in outgoing):
            phase='unknown';summary='发送结果未知，等待核对；不会自动重发'
        elif any(p['status'] in ('submitting','accepted') for p in outgoing):
            phase='receipt';summary='已提交发送，等待微信逐片回执'
        elif run['paused']:
            phase='experiment_paused';summary='实验已暂停'+(' · '+str(len(due))+'项到点服务待执行' if due else '')
        elif f['held']:
            phase='human';summary='老师接管中 · AI 外发已停止'
        elif outgoing:
            phase='send_queue';summary='等待发送队列 · '+str(len(outgoing))+'片'
        elif f['projected_seq']!=f['inbox_seq']:
            phase='sync';summary='正在接收入站消息或更新学情'
        elif f['applied_clock']!=run['epoch']:
            phase='sync';summary='正在同步业务时间'
        elif f['busy']:
            phase='tools';summary='智能体处理中 · '+topic
        elif due:
            phase='scheduled';summary='等待执行 · '+title(due[0])
            if due[0]['status']=='deferred':
                phase='deferred';summary='暂缓执行 · '+title(due[0])
                wait=due[0].get('not_before_wall')
                if isinstance(wait,(int,float)) and wait>now:summary+=' · 约'+str(int(wait-now)+1)+'秒后可重试'
                else:summary+=' · 等待联系间隔或执行条件'
        elif f['paused']:
            phase='service_paused';summary='主动服务暂停 · 仍可接收学生提问'
        future=next((t for t in tasks if t['due_at']>run['as_of']),None)
        result.append({'family_id':f['family_id'],'student':f['student'],'phase':phase,'summary':summary,
          'elapsed_seconds':max(0,int(now-since)) if since else None,'due_count':len(due),
          'due_tasks':[{'title':title(t),'status':t['status'],'due_at':t['due_at']} for t in due],
          'next':{'title':title(future),'due_at':future['due_at']} if future and not(f['held'] or f['paused']) else None})
    return result
