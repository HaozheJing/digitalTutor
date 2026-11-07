"""Operator-controlled frozen business clock; transport keeps wall-clock timestamps."""
import datetime as dt
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path
import sql_tools as s
import fixture_query

TZ = dt.timezone(dt.timedelta(hours=8))


def state():
    if not Path(s.DB).exists():
        return None
    with closing(sqlite3.connect(s.DB)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone():
            return None
        row = c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
    return json.loads(row[0]) if row else None


def clock():
    value = state()
    return dt.datetime.fromisoformat(value['as_of']) if value else dt.datetime.now(TZ)


def fixture_data(value=None, include_events=True):
    """Use the same selected fixture for model facts and operator projections."""
    value=state() if value is None else value
    if value.get('learning_scenario'):
        import re
        name=value['learning_scenario']
        if not re.fullmatch(r'acceptance-s07-[a-z0-9-]+\.json',name):raise ValueError('Invalid learning scenario')
        data=json.loads((fixture_query.FILE.parent/name).read_text(encoding='utf8'))
    else:
        data=json.loads(fixture_query.FILE.read_text(encoding='utf8'))
    if include_events:
        from completion_events import overlay
        return __import__('family_lab_bridge').learning_overlay(overlay(data,value),value)
    return data


def snapshot(day=None):
    value = state()
    if not value:
        raise ValueError('首月测试模式未启用')
    data=fixture_data(value)
    result = fixture_query.query(value['as_of'], day, data=data)
    lab=__import__('family_lab_bridge').configuration()
    result['profile'] = {**result['profile'], 'binding_status': 'bound_lab_student' if lab else 'bound_test_wechat',
                         'family_id': lab['family_id'] if lab else 'live-family'}
    result['test_run_id'] = value['run_id']
    import course_service
    course=course_service.binding(value['uid'])
    result['profile'].update(course)
    from course_navigation import entry_points
    for lesson in result['lessons_as_of']:
        points=entry_points(course.get('class_type'),lesson['lesson_no'])
        if points:lesson['knowledge_uri']=points['lesson_uri']
    points=entry_points(course.get('class_type'),1)
    if points:result['course_knowledge']={k:points[k] for k in ('book_title','revision','book_uri','available_details')}
    for enrollment in result['enrollments']:
        if enrollment.get('level')==course.get('class_type'):enrollment.update(course)
    import s07_service
    calendar=data['course_calendar']
    result['weekly_learning']=s07_service.weekly_learning(result,calendar)
    return __import__('highlight_events').overlay(result,value)


def activate(as_of, auto_highlights=None):
    """Called by the host only while its model is idle; backup before replacing a run."""
    if len(as_of) == 10:
        as_of += 'T20:30:00+08:00'
    elif len(as_of) == 16:
        as_of = as_of.replace(' ', 'T') + ':00+08:00'
    stamp = dt.datetime.fromisoformat(as_of)
    if stamp.tzinfo is None:
        raise ValueError('需要带时区的时间，或 YYYY-MM-DD HH:MM')
    as_of = stamp.astimezone(TZ).isoformat(timespec='seconds')
    fixture_query.query(as_of)  # Validate BEFORE any database mutation.
    previous=state()
    if auto_highlights is not None and type(auto_highlights) is not bool:
        raise ValueError('auto_highlights must be boolean')
    automatic=auto_highlights if auto_highlights is not None else (previous or {}).get('auto_highlights',False)
    if automatic and __import__('manual_workbench').configured():raise ValueError('此工作台仅供手动检验，不能开启自动发送')
    run_id = uuid.uuid4().hex
    backup = Path(s.DB).parent / 'test-archives' / (run_id + '.sqlite')
    backup.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(s.DB)) as c:
        from console_control import idle
        idle(c,receipt_complete=True)
        with closing(sqlite3.connect(backup)) as dest:
            c.backup(dest)
    value = {'uid': '00001', 'as_of': as_of, 'run_id': run_id, 'frozen': True,
             'auto_highlights':automatic,
             'service_workflow_mode':__import__('service_workflow').MODE if __import__('service_workflow').default_mode() else 'legacy',
             'completion_planning_mode':'ledger-v1' if __import__('completion_planner').default_mode() else 'legacy',
             'agent_planning':(previous or {}).get('agent_planning',False),
             'switched_at': s.now(), 'archive': str(backup)}
    if (previous or {}).get('task_scope') in (['s07'],['s08']):value['task_scope']=previous['task_scope']
    with closing(sqlite3.connect(s.DB)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        idle(c,receipt_complete=True)
        for table in ['messages','events','memories','memory_reviews','daily_plans',
                      'plan_candidates','context_snapshots','internal_executions','deliveries',
                      'task_state','contact_constraints','proactive_candidates','proactive_receipts',
                      'proactive_pending','business_ids','test_delivery_texts',
                      'test_delivery_bundles','test_delivery_emojis','test_outbox_parts',
                      'service_links','message_hook_deliveries','emoji_turns','service_hours_drafts']:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                c.execute('DELETE FROM ' + table)
        c.execute("INSERT OR REPLACE INTO metadata VALUES('test_mode',?)", (s.encoded(value),))
        c.execute("DELETE FROM metadata WHERE key='active_service_task'")
    return value


def advance(as_of):
    """Advance the business clock, retaining history across days."""
    state_now=state()
    if len(as_of)==16:as_of=as_of.replace(' ','T')+':00+08:00'
    stamp=dt.datetime.fromisoformat(as_of)
    if stamp.tzinfo is None:raise ValueError('需要北京时间或带时区的时间')
    as_of=stamp.astimezone(TZ).isoformat(timespec='seconds')
    fixture_query.query(as_of)
    if as_of<=state_now['as_of']:
        raise ValueError('只能推进到更晚的时刻；回退请开启新一轮')
    prior_day=state_now['as_of'][:10]
    state_now['as_of']=as_of
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(s.encoded(state_now),))
    if state_now.get('agent_planning'):
        import agent_plan
        agent_plan.sync()
        # A new day waits for its 06:30 occurrence. Same-day changes may revise an existing plan.
        if not __import__('service_workflow').enabled() and prior_day==as_of[:10] and as_of[11:16]>='06:30' and s.read(
            'SELECT 1 FROM service_plan_versions WHERE run_id=? AND day=?',
            (state_now['run_id'],prior_day)):
            agent_plan.request('plan')
    return state_now


def context(day):
    view = snapshot(day)
    value = state()
    if 'context_delivery_rowid' in value:
        # Active deliveries use business time, passive replies use wall time.
        # Insertion order, unlike either timestamp, is a valid reset boundary.
        deliveries=s.read('SELECT * FROM deliveries WHERE rowid>? ORDER BY rowid DESC LIMIT 10',
                          (value['context_delivery_rowid'],))
    else:
        # Compatibility for snapshots saved before the insertion cursor existed.
        deliveries=s.read('SELECT * FROM deliveries WHERE created_at>=? ORDER BY created_at DESC LIMIT 10',
                          (value.get('context_reset_at',''),))
    return {'business_day': value['as_of'][:10], 'business_time': value['as_of'],
            'timezone': 'Asia/Shanghai', 'identity': view['profile'],
            'learning': view,
            'recent_conversation': list(reversed(s.read(
                'SELECT source_id,speaker,body,created_at FROM messages WHERE visible=1 ORDER BY created_at DESC LIMIT 20'))),
            'previous_deliveries': deliveries,
            'service_guidance': '以business_time为当前服务时间，学情以可见学习记录为准。'
            '高光通过highlight_lookup读取已有素材，使用reply_with_highlights交付图片。'
            '通过service_schedule落实服务安排并save_plan；排期不等于已执行。',
            'knowledge_fulltext': [],
            'course_material_status': '学情knowledge_uri和课表knowledge提供课程知识入口。可按需要用course_browse查看目录、course_search定位相关内容、course_read选择摘要/概览/正文；也可用course_knowledge直接读某课全文。未查询不等于资料缺失。'}


RULE_MAP = {1:'celebrate-important-day',2:'highlight-expressive-answer',3:'highlight-ranking-progress',4:'highlight-first-perfect-answer',
            5:'highlight-quiz-ranking',6:'highlight-course-start',9:'celebrate-learning-streak',
            10:'highlight-seven-day-completion',11:'highlight-book-completion',16:'review-learning-progress'}


def decisions(c, day):
    value = state()
    if day != value['as_of'][:10]:
        return []
    decisions = []
    for h in snapshot()['available_highlights']:
        external=h.get('authority')=='external_program_confirmed'
        num = None if external else int(h['id'].rsplit('-',1)[1])
        ident = ('proactive:external:' if external else 'proactive:fixture:') + value['run_id'] + ':' + h['id']
        dedupe = value['run_id'] + ':' + h['dedupe_key']
        delivered = c.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?',(dedupe,)).fetchone()
        # A late original streak card must not duplicate the already delivered
        # S04 encouragement for the same natural week. An accepted/uncertain
        # send also reserves that matter until its original receipt resolves.
        covered=False
        if num==9:
            date=dt.date.fromisoformat(day);monday=(date-dt.timedelta(days=date.weekday())).isoformat()
            covered=bool(c.execute("SELECT 1 FROM proactive_receipts pr JOIN proactive_candidates pc ON pc.id=pr.candidate_id WHERE pc.rule='celebrate-learning-streak' AND pc.day BETWEEN ? AND ? AND pr.dedupe_key LIKE ? AND json_extract(pc.body,'$.evidence.event_id') IS NOT NULL",(monday,day,value['run_id']+':%')).fetchone())
        if num==6:
            covered=bool(c.execute("SELECT 1 FROM proactive_receipts pr JOIN proactive_candidates pc ON pc.id=pr.candidate_id WHERE pc.rule='celebrate-lesson-completion' AND pc.day=? AND pr.dedupe_key LIKE ? AND json_extract(pc.body,'$.evidence.lesson.lesson_no')=1",(day,value['run_id']+':%')).fetchone())
        decisions.append({'id':ident,'candidate_id':ident,'day':day,'rule':h['skill'] if external else RULE_MAP[num],
            'skill':h['skill'] if external else RULE_MAP[num],'title':h['medal_name'],'scope':h['id'],
            'status':'already_delivered' if delivered else 'merged_into_higher_priority' if covered else 'not_due' if value['as_of']<h['candidate_at'] else 'eligible','dedupe_key':dedupe,
            'purpose':h['evidence'],'evidence':h,'priority':80,'contacts_user':True,
            'delivery_format':'text_and_existing_image','delivery_owner':'host_highlights',
            'time_window':{'earliest':h['candidate_at'],'latest':h['expires_at']}})
    if value.get('agent_planning'):
        snap=snapshot()
        import s05_service
        s05=s05_service.decision(c,value,snap)
        if s05:decisions.append(s05)
        import s07_service
        s07=s07_service.decision(c,value,snap)
        if s07:decisions.append(s07)
        import s08_service
        s08=s08_service.decision(c,value,snap)
        if s08:decisions.append(s08)
        import s09_service
        s09=s09_service.decision(c,value,snap)
        if s09:decisions.append(s09)
        import weekly_interaction
        interaction=weekly_interaction.decision(c,value,snap)
        if interaction:decisions.append(interaction)
        import festival_service
        festival=festival_service.decision(c,value)
        if festival:decisions.append(festival)
        from completion_service import decisions as completion_decisions
        event_decisions=completion_decisions(c,value,snap,fixture_data(value,include_events=False))
        # Triggered existing assets are delivered by the host. The same-day
        # S04 text encouragement is covered by its original highlight card.
        if any(x['rule']=='celebrate-learning-streak' for x in decisions):
            for item in event_decisions:
                if item['rule']=='celebrate-learning-streak' and item['status']=='eligible':
                    item.update(status='merged_into_higher_priority',reason='当日已触发的连续学习高光由宿主直接发送原文原图，避免重复鼓励')
        event_decisions=__import__('completion_planner').filter_candidates(c,value,event_decisions)
        decisions.extend(event_decisions)
        # Additional already-enabled Skills, derived solely from current visible facts.
        date=dt.date.fromisoformat(day);monday=(date-dt.timedelta(days=date.weekday())).isoformat()
        visible=snapshot()['lessons_as_of']
        week=[r for r in visible if monday<=r['scheduled_date']<=day]
        overdue=[r for r in week if r['overdue']]
        def add(rule,title,evidence):
            ident='proactive:fixture:'+value['run_id']+':'+rule
            dedupe=value['run_id']+':'+rule+':'+monday
            delivered=c.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?',(dedupe,)).fetchone()
            decisions.append({'id':ident,'candidate_id':ident,'day':day,'rule':rule,'skill':rule,
              'title':title,'scope':monday,'status':'already_delivered' if delivered else 'eligible',
              'dedupe_key':dedupe,'purpose':title,'evidence':evidence,'priority':60,
              'contacts_user':True,'delivery_format':'text',
              'time_window':{'earliest':day+'T09:00:00+08:00','latest':day+'T23:59:00+08:00'}})
        item=__import__('s10_service').decision(c,value,snap)
        if item:decisions.append(item)
    return decisions


def highlight_lookup(highlight_id=None):
    visible = snapshot()['available_highlights']
    if highlight_id:
        return next((h for h in visible if h['id']==highlight_id),
                    {'status':'not_available_at_current_time'})
    return {'as_of':state()['as_of'],'highlights':visible}


def status_text():
    v = snapshot()
    return ('UID 00001 · 侨侨 · L2\n测试时间：' + v['as_of'].replace('T',' ') +
            '\n已排%d课，已完成%d课，尚欠%d课。' %
            (v['summary_as_of']['scheduled'],v['summary_as_of']['completed'],v['summary_as_of']['overdue']) +
            '\n当前可查询高光：' + ('、'.join(h['medal_name'] for h in v['available_highlights']) or '无') +
            '\n命令：测试日期 2025-10-24 20:30｜测试高光｜测试状态｜测试重置')


if __name__ == '__main__':
    command = sys.argv[1]
    try:
        result = activate(sys.argv[2],json.loads(sys.argv[3]) if len(sys.argv)>3 else None) if command=='activate' else advance(sys.argv[2]) if command=='advance' else state()
    except ValueError as error:
        result = {'status':'rejected','error':str(error)}
    print(s.encoded(result))
