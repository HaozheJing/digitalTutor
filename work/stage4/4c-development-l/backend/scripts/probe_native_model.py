"""Real API, synthetic families, native Hermes. One bounded case per invocation."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
from threading import Thread, Event
import time

from digital_tutor.application import Application, Clock
from digital_tutor.demo import seed_learning
from digital_tutor.hermes_host import HermesHost
from digital_tutor.tools import ToolRegistry


parser=argparse.ArgumentParser()
parser.add_argument('case',choices=['init','a1','b','a2','daily','progress','cancel'])
parser.add_argument('--run',required=True)
parser.add_argument('--materials',action='store_true',help='Only after approval of internal materials to the configured API')
args=parser.parse_args()
assert args.run.replace('-','').replace('_','').isalnum()
root=Path.cwd(); state=root/'state/backend-stage3'/args.run
out=root/'evaluations/backend-stage3'/args.run
state.mkdir(parents=True,exist_ok=True); out.mkdir(parents=True,exist_ok=True)
app=Application(state/'business.sqlite',Clock('2026-10-03T10:00:00+08:00'))
source_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (root/'backend').rglob('*.py') if '__pycache__' not in p.parts}

if args.case=='init':
    for f in ('A','B','P'):
        app.create_family('synthetic-'+f,'mock-uid-'+f,'mock-chat-'+f)
    seed_learning(app,'mock-uid-A','mock-uid-B')
    for f,word in [('A','青松校验词'),('B','海鸥校验词')]:
        token=app.open_session('synthetic-'+f,'channel')
        app.ingest_message(token,{'external_id':'synthetic-inbound-'+f,
            'body':f'本家庭校验词是{word}。请查询我的学习记录有几天，给我一条回复。',
            'occurred_at':app.clock.iso()})
        app.revoke_session(token)
    print(json.dumps({'initialized':True,'real_family_data':False})); app.close(); raise SystemExit

family='synthetic-B' if args.case=='b' else 'synthetic-A'
role='service'; options={'exclude_materials':not args.materials}; timer_stop=Event(); timer=None
if args.case=='daily':
    family='synthetic-P'; role='planner_daily'
    prompt=('这是隔离模拟验收。读取context.read，读取plan-proactive-service Skill。'
            '为今天提交唯一daily计划：一个新目标，goal_key=mock-reading、client_ref=reading、'
            'goal_text=完成模拟阅读、completion.kind=goal、lesson_refs=[mock-lesson-1]。'
            '今日plan_refs只含reading。用实际工具提交，不发送消息。')
elif args.case=='progress':
    family='synthetic-P'; role='planner_progress'
    event=app.store.list('event',family)[0]
    channel=app.open_session(family,'channel')
    message=app.ingest_message(channel,{'external_id':'progress-start','body':'我们已开始处理原阅读任务，还没完成。',
                                      'occurred_at':app.clock.iso()})['data']
    app.revoke_session(channel)
    service=app.open_session(family,'service')
    app.submit_report(service,{'request_id':'probe-report','report_id':'probe-report','revision':1,
        'event_ids':[event['id']],'facts':['模拟家庭已开始处理原阅读事项'],
        'remaining_work':['mock-lesson-1'],'evidence_refs':[{'kind':'message','id':message['id'],'version':message['version']}]})
    app.revoke_session(service)
    prompt=('读取context.read以及plan-proactive-service Skill，根据绑定报告将唯一原事项status改成in_progress，'
            '保留其目标、完成条件和待完成课节，调用planner.commit_progress。没有完成证据不能done。')
else:
    prompt=('这是隔离验收。请依次调用wechat.read_messages(mode=latest,direction=inbound)、'
            'learning.query_sql执行SELECT uid,day FROM learning_days ORDER BY day。'
            '最终内部文本列出你实际看到的家庭校验词及学习记录行数。不要推断别的家庭。')
    if args.case=='a1':
        manifest=json.loads((root/'build/openviking/courses/source-c764d8b325cc-nosplit-v2/access-map.json').read_text())
        unit=next(u for u in manifest['units'] if u.get('source_day') in (1,'1') and u.get('record_type')=='lesson')
        if args.materials:
            options['knowledge_binding']={'revision':manifest['revision'],'allowed_unit_ids':[unit['unit_id']], 'simulation':True}
            prompt+=('还需调用skills.read读取explain-lesson，以及knowledge.read读取资料编号'+unit['unit_id']+
                     '。课程资料只用作模拟读取验证，不推断家庭已绑定此课。')
        prompt+=('最后对最新入站消息用message.interpret记录单个须回复意图，读取最新state.read取得版本，'
                 '使用wechat.send_message(action_kind=reply)仅回复实际学情行数，并查询原operation确认。')
    else:
        prompt+='本次只读，不发送、不解释或修改消息。'

if not args.materials:
    prompt=prompt.replace('读取plan-proactive-service Skill。','').replace('以及plan-proactive-service Skill','')
token=app.open_session(family,role,**({'report_id':'probe-report','revision':1} if role=='planner_progress' else {}))
business=ToolRegistry(app,token)
if args.case=='cancel':
    prompt='请调用state.read，然后读取所有已提供的只读工具目录并详细解释每项范围。不要发送。'
    def cancel():
        while not timer_stop.wait(0.05):
            if any((p/'tools.jsonl').exists() for p in (state/'runs-cancel').glob('*')):
                app.revoke_session(token); return
    timer=Thread(target=cancel,daemon=True);timer.start()

runner=HermesHost(root,state/('runs-cancel' if args.case=='cancel' else 'runs'),120)
operations_before=len(app.store.list('operation',family))
family_version_before=app.store.family(family)['version']
result=runner.run(business,prompt,options=options)
timer_stop.set()
if timer: timer.join(timeout=1)
app.revoke_session(token)
checks={}
if args.case in ('a1','a2','b'):
    word='海鸥校验词' if args.case=='b' else '青松校验词'
    other='青松校验词' if args.case=='b' else '海鸥校验词'
    checks['family_isolated']=word in result.get('final_response','') and other not in result.get('final_response','')
    checks['sql_scoped_three_rows']=any(t['tool']=='learning.query_sql' and t['ok'] and
        t.get('returned_rows')==3 and t.get('authorized_uid_only') for t in result.get('tool_trace',[]))
    if args.case=='a1':
        for tool in (('skills.read','knowledge.read') if args.materials else ()) + ('wechat.send_message','wechat.get_operation'):
            checks[tool]=any(t['tool']==tool and t['ok'] for t in result.get('tool_trace',[]))
        checks['one_confirmed_operation']=len(app.store.list('operation',family))==1 and app.store.list('operation',family)[0]['stage']=='sent'
        operation=app.store.list('operation',family)[0] if app.store.list('operation',family) else {}
        reply=operation.get('body','')
        checks['reply_supported_by_data']=('3' in reply or '三' in reply) and other not in reply and 'mock-uid-B' not in reply
    else:
        checks['final_text_not_sent']=len(app.store.list('operation',family))==operations_before
        checks['read_only_family_version']=app.store.family(family)['version']==family_version_before
elif args.case=='daily':
    checks['one_plan_one_goal']=len(app.store.list('plan',family))==len(app.store.list('event',family))==1
    goal=app.store.list('event',family)[0] if app.store.list('event',family) else {}
    checks['goal_matches_task']=goal.get('goal_key')=='mock-reading' and goal.get('completion',{}).get('lesson_refs')==['mock-lesson-1']
elif args.case=='progress':
    current=app.event(family,event['id'])
    checks['original_goal_preserved']=len(app.store.list('event',family))==1 and current['status']=='in_progress' and all(
        current.get(k)==event.get(k) for k in ('goal_key','goal_text','completion','remaining_work'))
    checks['report_consumed']=app.store.list('report',family)[0]['resolution']=='applied'
else:
    checks['native_cancelled']=result.get('interrupted') is True and result.get('completed') is False
    checks['no_extra_operation']=len(app.store.list('operation',family))==1
checks['internal_route']=result.get('output_route')=='internal'
checks['no_generic_tools']=all(n.startswith('dt_') for n in result.get('tools',[])) and bool(result.get('tools'))
passed=all(checks.values()) and (result.get('completed') or args.case=='cancel')
assert source_hashes=={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (root/'backend').rglob('*.py') if '__pycache__' not in p.parts}, 'Source changed during native run'
record={'case':args.case,'passed':passed,'checks':checks,'native':result,'real_model':True,'real_wechat':False,
        'internal_materials_enabled':args.materials,'source_sha256':source_hashes}
(out/(args.case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'case':args.case,'passed':passed,'checks':checks,'run_id':result['run_id'],'error':result.get('error')},ensure_ascii=False))
app.close()
raise SystemExit(0 if passed else 1)
