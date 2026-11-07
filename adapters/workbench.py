"""Read-only operator projection. Forecasts are never included in model context."""
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
import fixture_query as fq
import test_mode as tm
import sql_tools as s
import observability as audit
import test_scheduler

SCENARIOS = [
    ('2025-10-13', '首课完成', '侨侨今天学了第几课？完成得怎么样？'),
    ('2025-10-15', '首次全对', '前三课表现怎么样？有没有第一次全对？'),
    ('2025-10-19', '第一周回顾', '这周学完几课？今天还有新课吗？'),
    ('2025-10-21', '开始缺课', '这两天有没有缺课？哪一课没完成？'),
    ('2025-10-24', '频繁缺课', '第二周有哪些课需要补？有没有上过但没完成的？'),
    ('2025-10-26', '周末补课', '周末补课后还差哪一课？'),
    ('2025-10-28', '生日早晨', '今天是什么特别的日子？今天的课上了吗？'),
    ('2025-10-31', '月度成长', '这个月有哪些进步？还有哪些课没完成？'),
    ('2025-11-01', '补完第9课', '第9课补完了吗？现在还欠哪一课？'),
    ('2025-11-02', '正确率与晋级', '这周答题表现比上周好吗？欠课补齐了吗？'),
    ('2025-11-07', '末课与欠课核对', '第一本书读完了吗？这周表达有什么亮点？'),
    ('2025-11-09', '首月收官', '首月20课都完成了吗？是不是每节都按时完成？'),
]

def learning_overview(snap):
    day=snap['as_of'][:10]
    rows=snap['lessons_as_of']
    today=[r for r in rows if r['scheduled_date']==day]
    expected=snap['requested_day'].get('new_lesson_count',0)
    completed=sum(r['status']=='completed' for r in today)
    status=('no_lesson' if not expected else 'unknown' if len(today)!=expected else
            'completed' if completed==expected else 'in_progress' if any(r['attended_at'] for r in today)
            else 'overdue' if any(r['overdue'] for r in today) else 'not_started')
    import datetime as dt
    week_start=(dt.date.fromisoformat(day)-dt.timedelta(days=dt.date.fromisoformat(day).weekday())).isoformat()
    week=[r for r in rows if r['scheduled_date']>=week_start]
    profile=snap['profile']
    return {'as_of':snap['as_of'],
            'profile':{k:profile.get(k) for k in ('uid','interaction_nickname','child_nickname','grade','birthday')},
            'enrollments':[{k:e.get(k) for k in ('level','course_name','course_start_date')} for e in snap['enrollments']],
            'week_no':snap['requested_day']['week_no'],
            'today':{'status':status,'scheduled':expected,'completed':completed,'lessons':today},
            'week':{'scheduled':len(week),'completed':sum(r['status']=='completed' for r in week)},
            'overdue_lessons':[r for r in rows if r['overdue']]}


def view():
    state = tm.state()
    if not state:
        raise ValueError('首月测试模式未启用')
    snap = tm.snapshot()
    fixture = tm.fixture_data(state)
    scenarios = []
    for day, title, question in SCENARIOS:
        if not fixture['window']['start']<=day<=fixture['window']['end']:continue
        at = day + ('T09:30:00+08:00' if day == '2025-10-28' else 'T20:30:00+08:00')
        forecast = fq.query(at,data=fixture)
        scenarios.append({'as_of': at, 'title': title, 'question': question,
                          'summary': forecast['summary_as_of'],
                          'highlights': [h['medal_name'] for h in forecast['available_highlights']]})
    receipts = s.read('SELECT dedupe_key FROM proactive_receipts')
    delivered = {r['dedupe_key'] for r in receipts}
    highlights = [{k: h[k] for k in ['id','medal_name','candidate_at','expires_at','evidence']}
                  | {'already_triggered': state['run_id'] + ':' + h['dedupe_key'] in delivered}
                  for h in snap['available_highlights']]
    return {
        'operator_alerts':__import__('operator_alerts').list_alerts(),
        'daily_highlights':__import__('workbench_observer').daily_highlight_overview(),
        'completion_events':__import__('completion_events').view(),
        'service_ledger':__import__('service_ledger').view(),
        'observer':__import__('workbench_observer').view(),
        'learning_overview':learning_overview(snap),
        'planning_recurrence':__import__('agent_plan').recurrence(),
        'clock': {k: state[k] for k in ['uid','as_of','run_id','frozen']},
        'context': {'reset_at':state.get('context_reset_at'),
                    'visible_messages':s.read('SELECT COUNT(*) AS n FROM messages WHERE visible=1')[0]['n'],
                    'active_memories':s.read("SELECT COUNT(*) AS n FROM memories WHERE status='active'")[0]['n']},
        'window':fixture['window'],
        'manual_only':__import__('manual_workbench').configured(),
        'delivery_mode':'allowlisted_test_wechat' if __import__('manual_workbench').configured() else 'configured',
        'auto_highlights':state.get('auto_highlights',False),
        'agent_planning':state.get('agent_planning',False),
        'summary': snap['summary_as_of'], 'today': snap['requested_day'],
        'lessons': snap['lessons_as_of'], 'highlights': highlights,
        'calendar': fixture['course_calendar'], 'scenarios': scenarios,
        'deliveries': s.read('SELECT created_at,status,body FROM deliveries ORDER BY created_at DESC LIMIT 8'),
        'inspection':inspection(), 'history':history(),
    }


def inspection():
    state=tm.state()
    has_parts=bool(s.read("SELECT 1 FROM sqlite_master WHERE name='test_outbox_parts'"))
    chats=[{'id':r['source_id'],'role':r['speaker'],'body':r['body'],'time':r['created_at'],
            'status':'received','parts':[]} for r in s.read(
        'SELECT * FROM messages WHERE visible=1 ORDER BY created_at DESC LIMIT 80')]
    for row in s.read('SELECT * FROM deliveries ORDER BY created_at DESC LIMIT 80'):
        parts=s.read('''SELECT o.message_type,o.status,o.body,o.created_at,o.updated_at,o.error
            FROM test_outbox_parts p JOIN test_outbox o ON p.operation_id=o.id
            WHERE p.action_key=? ORDER BY o.created_at''',(row['action_key'],)) if has_parts else []
        chats.append({'id':row['action_key'],'role':'assistant','body':row['body'],
                      'time':min((p['created_at'] for p in parts),default=row['created_at']),
                      'status':row['status'],'parts':parts})
    return {'clock':{k:state[k] for k in ['uid','as_of','run_id']},
            'service_ledger':__import__('service_ledger').view(),
            'operator_alerts':__import__('operator_alerts').list_alerts(),
            'chat':sorted(chats,key=lambda x:x['time'] or ''),
            'tools':audit.records(state['run_id']),
            'plan':test_scheduler.plan(),
            'events':s.read('SELECT id,kind,status,attempts,created_at,completed_at FROM events ORDER BY created_at DESC LIMIT 30')}


def history():
    result=[]
    directory=Path(s.DB).parent/'test-archives'
    for file in sorted(directory.glob('*.sqlite'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
        if not re.fullmatch('[a-f0-9]{32}',file.stem):continue
        try:
            with closing(sqlite3.connect(file.as_uri()+'?mode=ro',uri=True)) as c:
                row=c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
                if row:
                    state=json.loads(row[0])
                    result.append({'archive_id':file.stem,'run_id':state['run_id'],'as_of':state['as_of']})
        except (sqlite3.Error,ValueError,KeyError):
            continue
    return result


def archived(ident):
    if not re.fullmatch('[a-f0-9]{32}',ident) or ident not in {x['archive_id'] for x in history()}:
        raise ValueError('测试轮次不存在')
    original=s.DB
    try:
        s.DB=Path(original).parent/'test-archives'/(ident+'.sqlite')
        return inspection()
    finally:
        s.DB=original


def preview(as_of):
    """Human-only time travel. Never activates a run or materializes tasks."""
    import datetime as dt
    import agent_plan as ap
    state=tm.state()
    source=None
    if state.get('learning_scenario'):
        name=state['learning_scenario']
        if not re.fullmatch(r'acceptance-s07-[a-z0-9-]+\.json',name):raise ValueError('Invalid learning scenario')
        source=json.loads((fq.FILE.parent/name).read_text(encoding='utf8'))
    snap=fq.query(as_of,data=tm.fixture_data(state))
    day=snap['as_of'][:10];weekday=dt.date.fromisoformat(day).weekday()
    checks=[]
    for kind,hour,title,skill in ap.FIXED:
        if kind=='s07' and weekday!=2:continue
        if kind in ('s08','interaction') and weekday!=4:continue
        if kind=='weekly' and weekday not in (5,6):continue
        if kind=='festival':
            from festival_service import event
            if not event({'as_of':snap['as_of']}):continue
        checks.append({'time':hour,'title':title,'skill':skill,'kind':kind})
    plan=test_scheduler.plan(day)
    # Recorded plans are current-run history, not predictions at the selected time.
    return {'as_of':snap['as_of'],'learning_overview':learning_overview(snap),
            'daily_highlights':__import__('workbench_observer').daily_highlight_overview(day),
            'summary':snap['summary_as_of'],'lessons':snap['lessons_as_of'],
            'checks':sorted(checks,key=lambda x:x['time']),
            'recorded_plan':plan,'read_only':True,
            'note':'固定检查安排仅表示到点核验；下方已保存计划属于当前测试轮次，不是预先生成的计划。'}


if __name__ == '__main__':
    command=sys.argv[1] if len(sys.argv)>1 else 'state'
    print(s.encoded(archived(sys.argv[2]) if command=='history' else preview(sys.argv[2]) if command=='preview' else test_scheduler.plan(sys.argv[2]) if command=='plan' else view()))
