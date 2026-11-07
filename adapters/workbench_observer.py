"""Human-only fact/expectation projection. Never imported by model-facing tools.

Expected S02 is calculated independently of candidate generation so a missing
adapter cannot turn a genuinely missing action into a reassuring empty view.
All reads are side-effect free; opening the workbench does not schedule or send.
"""
import datetime as dt
import json
import sqlite3
from contextlib import closing
import fixture_query as fq
import test_mode as tm
import sql_tools as s
import proactive


HIGHLIGHT_STATES = {
    'forecast': '预计', 'due': '到点可核验', 'triggered': '已触发',
    'accepted': '已受理待回执', 'sent': '已确认发送', 'blocked': '受阻',
    'covered': '已被更具体服务覆盖', 'expired': '窗口已过', 'skipped': '本次无需联系',
}


def _highlight_counts(items):
    return {'total': len(items), **{key: sum(x['state'] == key for x in items)
                                  for key in HIGHLIGHT_STATES}}


def _highlight_runtime(state):
    """One read-only snapshot. Never refresh candidates or synchronize tasks."""
    with closing(sqlite3.connect('file:' + str(s.DB).replace('\\', '/') + '?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN')
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tasks = [dict(r) for r in c.execute('SELECT * FROM service_tasks WHERE run_id=?',
                 (state['run_id'],))] if 'service_tasks' in tables else []
        candidates = []
        if 'proactive_candidates' in tables:
            for row in c.execute('SELECT * FROM proactive_candidates'):
                try:
                    value = json.loads(row['body'])
                except (TypeError, ValueError):
                    continue
                # Old runs and unscoped legacy candidates cannot describe this run.
                if not str(value.get('dedupe_key', '')).startswith(state['run_id'] + ':'):
                    continue
                candidates.append({**value, 'id': row['id'], 'day': row['day'],
                                   'rule': row['rule'], 'status': row['status']})
        receipts = [dict(r) for r in c.execute('''SELECT pr.candidate_id,pr.dedupe_key,
            d.action_key,d.status,d.body FROM proactive_receipts pr
            LEFT JOIN deliveries d ON pr.delivery_key=d.action_key WHERE pr.dedupe_key LIKE ?''',
            (state['run_id'] + ':%',))] if {'proactive_receipts', 'deliveries'} <= tables else []
        task_ids = {t['id'] for t in tasks}
        task_receipts = [dict(r) for r in c.execute('''SELECT l.task_id,d.action_key,d.status,d.body
            FROM service_links l JOIN deliveries d ON l.action_key=d.action_key''')
            if r['task_id'] in task_ids] if {'service_links', 'deliveries'} <= tables else []
        constraints = [dict(r) for r in c.execute('SELECT * FROM contact_constraints')] if 'contact_constraints' in tables else []
        return candidates, tasks, receipts, task_receipts, constraints


def _highlight_projection(raw, state):
    """Operator forecasts from the selected script, not executable candidates."""
    items = []
    assets = {h['id']: h for h in raw.get('highlight_assets', [])}
    for asset in assets.values():
        target = assets.get(asset.get('covered_by'))
        due = asset.get('candidate_at') or (target or {}).get('candidate_at')
        if not due or asset.get('trigger_status') not in ('eligible_fixture', 'covered_by_combination'):
            continue
        number = int(asset['id'].rsplit('-', 1)[1])
        # A selected acceptance scenario may change the profile birthday.
        birthday = raw.get('profile', {}).get('birthday')
        if number == 1 and (not birthday or birthday[5:] != due[5:10]):
            continue
        rule = tm.RULE_MAP.get(number, 'highlight-first-perfect-answer' if number == 4 else
                               'celebrate-learning-streak' if number == 8 else 'highlight')
        covered_by = asset.get('covered_by')
        reason = '当日高光接口触发后，宿主直接发送已有文案和图片；未来预期不提前发送，回执按当前轮次去重。'
        if covered_by:
            reason = asset.get('evidence') or '由组合高光覆盖，不单独重复触发。'
        items.append({'id': asset['id'], 'title': asset['medal_name'], 'rule': rule,
                      'category': 'interface_highlight',
                      'day': due[:10], 'due_at': due,
                      'expires_at': asset.get('expires_at') or (target or {}).get('expires_at'),
                      'source': 'fixture_schedule', 'covered_by': covered_by,
                      'scope_lesson_id': None, 'reason': reason,
                      'evidence': asset.get('evidence', ''),
                      'evidence_basis': 'selected_fixture_projection',
                      'candidate_id': 'proactive:fixture:' + state['run_id'] + ':' + asset['id']})
    return items


def _regular_action_projection(raw, state):
    """Conventional service expectations never count as interface highlights."""
    items = []
    birthday = raw.get('profile', {}).get('birthday')
    if birthday:
        for calendar in raw['course_calendar']:
            day = calendar['date']
            if day[5:] == birthday[5:] and not any(x['day'] == day and x['rule'] == 'celebrate-important-day' for x in items):
                items.append({'id': 'birthday:' + day, 'title': '生日关怀', 'rule': 'celebrate-important-day',
                    'day': day, 'due_at': day + 'T09:30:00+08:00', 'expires_at': day + 'T23:59:59+08:00',
                    'source': 'profile_calendar_projection', 'covered_by': None, 'scope_lesson_id': None,
                    'reason': '根据所选测试档案预计生日关怀；尚无该日贺卡素材，实际交付须核验。',
                    'evidence': '测试档案生日：' + birthday, 'evidence_basis': 'selected_fixture_projection',
                    'candidate_id': None})
    # These are fact-based expectations only. The opt-in model planner remains
    # responsible for deciding whether to create an executable service action.
    import completion_events
    completed = [l for l in raw['learning_daily'] if l.get('completed_at')]
    for lesson in completed:
        enrollment = completion_events.enrollment(raw, lesson)
        stamp = lesson['completed_at']; day = stamp[:10]
        if day not in {r['date'] for r in raw['course_calendar']}:
            continue
        due = (dt.datetime.fromisoformat(stamp) + dt.timedelta(minutes=30)).isoformat()
        if due[:10] != day:
            continue  # No cross-day outreach from a late-night completion.
        same_enrollment = [l for l in raw['learning_daily']
                           if completion_events.enrollment(raw, l)['id'] == enrollment['id']]
        def full(date):
            rows = [l for l in same_enrollment if l['date'] == str(date)]
            expected = next((r.get('new_lesson_count') for r in raw['course_calendar'] if r['date'] == str(date)), None)
            return bool(rows) and len(rows) == expected and all(l.get('completed_at') and
                   l['completed_at'][:10] == str(date) and l['completed_at'] <= stamp for l in rows)
        anchor = dt.date.fromisoformat(day)
        while full(anchor):
            anchor -= dt.timedelta(days=1)
        streak = (dt.date.fromisoformat(day) - anchor).days
        threshold = streak == 3
        first_week = 0 <= (dt.date.fromisoformat(day) - dt.date.fromisoformat(enrollment['course_start_date'])).days < 7
        for code, rule, title in [('S02', 'celebrate-lesson-completion', '首周完课表扬'),
                                  ('S04', 'celebrate-learning-streak', '连续三日完课鼓励')]:
            if (code == 'S02' and not first_week) or (code == 'S04' and not threshold):
                continue
            covered_by = ('highlight-course-start' if code == 'S02' and lesson['lesson_no'] == 1 and
                          day == enrollment['course_start_date'] else
                          'celebrate-learning-streak' if code == 'S02' and threshold else None)
            items.append({'id': 'completion:' + code + ':' + lesson['id'], 'title': title, 'rule': rule,
                'day': day, 'due_at': due, 'expires_at': day + 'T23:59:59+08:00',
                'source': 'completion_rule_projection', 'covered_by': covered_by,
                'scope_lesson_id': lesson['id'], 'candidate_id': None,
                'reason': ('首课由首日纪念覆盖。' if covered_by == 'highlight-course-start' else
                           '同次完课由连续三日鼓励覆盖，避免重复表扬。' if covered_by else
                           '依据该日完课事实预计可核验鼓励；实际是否联系、排期由运行时判断。'),
                'evidence': ('第' + str(lesson['lesson_no']) + '课于' + stamp + '完成' +
                             ('；连续三个自然日完成当日应学课程' if code == 'S04' else '；处于开课首周')),
                'evidence_basis': 'selected_fixture_projection'})
    return [{**item, 'category': 'regular_action'} for item in items]


def _daily_overview(day=None, start=None, end=None, category='interface_highlight'):
    """Read-only operator calendar; forecasts never enter model-facing context.

    Every recorded state is scoped to this run and the item's own business day.
    Without such evidence, past dates remain projections instead of invented
    missed executions. Future rows never borrow today's candidates or receipts.
    """
    state = tm.state()
    if not state:
        raise ValueError('首月测试模式未启用')
    raw = tm.fixture_data(state)
    current = state['as_of'][:10]
    selected_day = day or current
    first, last = start or raw['window']['start'], end or raw['window']['end']
    try:
        first_date, last_date = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
        dt.date.fromisoformat(selected_day)
    except (ValueError, TypeError):
        raise ValueError('高光预览需要有效的 YYYY-MM-DD 日期') from None
    if not raw['window']['start'] <= first <= last <= raw['window']['end'] or not first <= selected_day <= last:
        raise ValueError('高光预览日期超出当前首月窗口')
    candidates, tasks, receipts, task_receipts, constraints = _highlight_runtime(state)
    items = (_highlight_projection(raw, state) if category == 'interface_highlight'
             else _regular_action_projection(raw, state))
    days = []
    for offset in range((last_date - first_date).days + 1):
        target_day = (first_date + dt.timedelta(days=offset)).isoformat()
        scope = 'current' if target_day == current else 'future' if target_day > current else 'past'
        projected = []
        for source in (x for x in items if x['day'] == target_day):
            item = dict(source)
            cs = [x for x in candidates if scope != 'future' and x.get('day') == target_day and
                  (x['id'] == item['candidate_id'] if item['source'] == 'fixture_schedule' else
                   x.get('rule') == item['rule'] and (not item['scope_lesson_id'] or
                   x.get('evidence', {}).get('lesson', {}).get('lesson_id') == item['scope_lesson_id']))]
            ids = {x['id'] for x in cs}
            if item['candidate_id']:
                ids.add(item['candidate_id'])
            jobs = [t for t in tasks if scope != 'future' and t['day'] == target_day and
                    (t.get('candidate_id') in ids or (item['source'] != 'fixture_schedule' and
                    t.get('skill') == item['rule'] and not item['scope_lesson_id']))]
            linked = [r for r in receipts if scope != 'future' and r['candidate_id'] in ids]
            linked += [r for r in task_receipts if r['task_id'] in {t['id'] for t in jobs} and
                       r['action_key'] not in {r['action_key'] for r in linked}]
            statuses = {r['status'] for r in linked}
            if statuses:
                status = ('sent' if statuses == {'sent'} else 'accepted' if statuses <= {'sent', 'accepted'} else 'blocked')
            elif any(x['status'] == 'merged_into_higher_priority' for x in cs):
                status = 'covered'
            elif any(t['status'] in ('blocked', 'expired', 'cancelled') for t in jobs):
                status = 'expired' if all(t['status'] == 'expired' for t in jobs) else 'blocked'
            elif any(t['status'] == 'no_contact' for t in jobs) or any(x['status'] == 'not_eligible' for x in cs):
                status = 'skipped'
            elif any(t['status'] in ('running', 'awaiting_delivery', 'done') or t.get('attempts', 0) > 0 for t in jobs) or any(x['status'] in ('eligible', 'already_delivered') for x in cs):
                status = 'triggered'
            elif item['covered_by'] or any(x['status'] == 'merged_into_higher_priority' for x in cs):
                status = 'covered'
            elif scope == 'current' and any(c['day'] == target_day for c in constraints):
                status = 'blocked'
                item['reason'] += ' 本日存在暂停联系约束。'
            elif scope == 'current' and item.get('expires_at') and state['as_of'] > item['expires_at']:
                status = 'expired'
            elif scope == 'current' and state['as_of'] >= item['due_at']:
                status = 'due'
            else:
                status = 'forecast'
            if scope != 'current' and not (cs or jobs or linked):
                item['reason'] += ' 此日仅为所选模拟脚本预览，无本轮执行或发送证据。'
            runtime_reasons = [x.get('reason') for x in cs if x.get('reason')]
            for task in jobs:
                try:
                    result = json.loads(task.get('result') or '{}')
                except (TypeError, ValueError):
                    result = {}
                if result.get('reason'):
                    runtime_reasons.append(result['reason'])
            if runtime_reasons:
                item['reason'] += ' 本轮记录：' + '；'.join(dict.fromkeys(runtime_reasons))
            item.update(state=status, state_label=HIGHLIGHT_STATES[status], temporal_scope=scope,
                        evidence_basis='selected_fixture_projection',
                        candidates=[{k: c.get(k) for k in ('id', 'status', 'reason')} for c in cs],
                        tasks=[{k: t.get(k) for k in ('id', 'status', 'due_at', 'result')} for t in jobs],
                        deliveries=linked)
            projected.append(item)
        projected.sort(key=lambda x: (x['due_at'], x['id']))
        days.append({'date': target_day, 'is_current': scope == 'current', 'is_future': scope == 'future',
                     'temporal_scope': scope, 'items': projected, 'counts': _highlight_counts(projected)})
    today = next((row for row in days if row['date'] == current), None)
    return {'as_of': state['as_of'], 'selected_day': selected_day, 'days': days, 'category': category,
            'selected': next(row for row in days if row['date'] == selected_day),
            'summary': today['counts'] if today else _highlight_counts([]),
            'range_summary': _highlight_counts([item for row in days for item in row['items']]),
            'read_only': True, 'operator_only': True,
            'catalog': [{'id': h['id'], 'title': h['medal_name'], 'trigger_status': h.get('trigger_status'),
                         'reason': h.get('evidence'), 'covered_by': h.get('covered_by')}
                        for h in raw.get('highlight_assets', [])] if category == 'interface_highlight' else [],
            'notice': ('高光仅来自highlight接口模拟数据。程序监控接口触发，发送器直接交付接口原文和图片；未来脚本仅供预览，最终回执确认送达。首周表扬等常规动作在行动计划中处理，不计入高光。'
                       if category == 'interface_highlight' else
                       '常规动作依据学情与Skill判断，模型按需生成回复；此处为只读预期，实际执行及回执见行动计划。')}


def daily_highlight_overview(day=None, start=None, end=None):
    return _daily_overview(day, start, end, 'interface_highlight')


def daily_regular_overview(day=None, start=None, end=None):
    return _daily_overview(day, start, end, 'regular_action')


def view():
    state=tm.state();day=state['as_of'][:10];at=state['as_of']
    raw=tm.fixture_data(state);snap=fq.query(at,data=raw)
    start=min(e['course_start_date'] for e in raw['enrollments'])
    birthday=raw['profile'].get('birthday')
    timeline=[]
    names={'attendance':'到课','completion':'完课','quiz':'答题提交','checkin':'打卡','expression':'表达提交'}
    for event in raw['learning_events']:
        when=event['occurred_at']
        if when[:10]!=day:continue
        timeline.append({'at':when,'title':names.get(event['kind'],event['kind']),
                         'detail':event.get('lesson_id',''),'visible':when<=at,'source':'模拟学习事件'})
    for item in raw['expression_assignments']:
        when=item.get('submitted_at')
        if when and when[:10]==day:
            timeline.append({'at':when,'title':'表达作业提交','detail':item.get('lesson_id',''),
                             'visible':when<=at,'source':'模拟表达作业'})
    birthday_today=bool(birthday and birthday[5:]==day[5:])
    if birthday_today:timeline.append({'at':day+'T00:00:00+08:00','title':'侨侨生日',
        'detail':'测试生日 '+birthday,'visible':True,'source':'测试档案'})
    assets=[h for h in raw['highlight_assets'] if h.get('candidate_at') and h['candidate_at'][:10]==day
            and h['trigger_status']=='eligible_fixture']
    for h in assets:
        timeline.append({'at':h['candidate_at'],'title':h['medal_name'],'detail':h['evidence'],
                         'visible':h['candidate_at']<=at,'source':'已有高光接口素材'})
    with closing(sqlite3.connect('file:'+str(s.DB).replace('\\','/')+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row
        candidates=tm.decisions(c,day)
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tasks=[dict(r) for r in c.execute('SELECT * FROM service_tasks WHERE run_id=? AND day=? ORDER BY created_at',
            (state['run_id'],day))] if 'service_tasks' in tables else []
        receipts=[dict(r) for r in c.execute('''SELECT pr.candidate_id,d.action_key,d.status,d.body
            FROM proactive_receipts pr JOIN deliveries d ON pr.delivery_key=d.action_key''')]
        task_receipts=[dict(r) for r in c.execute('''SELECT l.task_id,d.action_key,d.status,d.body
            FROM service_links l JOIN deliveries d ON l.action_key=d.action_key''')] if 'service_links' in tables else []
        task_column='task_id' if 'tool_audit' in tables and 'task_id' in {r[1] for r in c.execute('PRAGMA table_info(tool_audit)')} else 'NULL AS task_id'
        audits=[dict(r) for r in c.execute('SELECT id,name,status,output,'+task_column+' FROM tool_audit WHERE run_id=? AND business_time LIKE ? ORDER BY started_at',
            (state['run_id'],day+'%'))] if 'tool_audit' in tables else []
        constraints=[dict(r) for r in c.execute('SELECT * FROM contact_constraints WHERE day=?',(day,))]
    actions=[]
    def add(code,title,rule,expectation,reason,due=None,capability='connected',fixed=None):
        cs=[x for x in candidates if x['rule']==rule]
        ids={x['id'] for x in cs}
        linked=[r for r in receipts if r['candidate_id'] in ids]
        jobs=[t for t in tasks if (t['candidate_id'] in ids if t['candidate_id'] else False) or (fixed and t['kind']==fixed)]
        delivery_keys={r['action_key'] for r in linked}
        linked_tasks={r['task_id'] for r in task_receipts if r['action_key'] in delivery_keys}
        jobs=[t for t in tasks if t in jobs or t['id'] in linked_tasks]
        for receipt in task_receipts:
            if receipt['task_id'] in {t['id'] for t in jobs} and receipt['action_key'] not in delivery_keys:
                linked.append(receipt);delivery_keys.add(receipt['action_key'])
        checks=[]
        for a in audits:
            if a['name'] in ('check_proactive','check_s01'):
                output=json.loads(a['output'] or '{}')
                if output.get('id') in ids or output.get('candidate_id') in ids or output.get('rule')==rule or a['task_id'] in {t['id'] for t in jobs}:
                    checks.append({'audit_id':a['id'],'status':output.get('status'), 'reason':output.get('reason')})
        active_cs=[x for x in cs if x['status']=='eligible']
        due_now=bool(due and due<=at)
        if linked:
            delivery_states={r['status'] for r in linked}
            result='sent' if delivery_states=={'sent'} else 'accepted' if delivery_states<={'sent','accepted'} else 'delivery_unconfirmed'
        elif jobs and jobs[-1]['status']=='running':result='running'
        elif jobs and jobs[-1]['status'] in ('blocked','expired','no_contact','cancelled'):result=jobs[-1]['status']
        elif expectation=='not_applicable':result='not_triggered'
        elif expectation=='expected' and due_now and not cs and not fixed:result='missing_candidate'
        elif expectation=='expected' and due_now and active_cs and not jobs and (fixed or rule in __import__('completion_service').SKILLS):result='missing_task'
        elif jobs and jobs[-1]['status'] in ('planned','deferred'):result='waiting' if due_now else 'not_due'
        else:result='not_due' if expectation in ('expected','forecast') and not due_now else 'not_triggered'
        actions.append({'code':code,'title':title,'rule':rule,'expectation':expectation,'reason':reason,
            'due_at':due,'capability':capability,'result':result,
            'candidates':[{'id':x['id'],'status':x['status'],'earliest':x['time_window']['earliest'],
                           'reason':x.get('reason',x.get('purpose','')),'event_id':x.get('evidence',{}).get('event_id')} for x in cs],
            'tasks':[{'id':t['id'],'status':t['status'],'due_at':t['due_at'],
                      'reason':json.loads(t['result'] or '{}').get('reason',''),'request_id':t['native_request_id']} for t in jobs],
            'deliveries':linked,'checks':checks})
    today=[r for r in snap['lessons_as_of'] if r['scheduled_date']==day]
    add('S01','今日课前提醒','remind-reading','conditional',
        '06:30邀请当天阅读；交付前即时复核并配当天原图。',day+'T06:30:00+08:00',fixed='s01')
    first_week=0<=(dt.date.fromisoformat(day)-dt.date.fromisoformat(start)).days<7
    done=[r for r in today if r['completed_at']]
    planned_done=[r for r in raw['learning_daily'] if r['date']==day and r.get('completed_at') and r['completed_at'][:10]==day]
    due=None
    if first_week and done:
        due=(dt.datetime.fromisoformat(done[0]['completed_at'])+dt.timedelta(minutes=30)).isoformat()
        expectation='merged' if day==start else 'expected'
        reason='首课表扬由首日纪念覆盖。' if day==start else '首周第'+str(done[0]['lesson_no'])+'课已于'+done[0]['completed_at'][11:16]+'完成；30分钟后应有文字表扬候选，无需图片。'
    elif first_week and planned_done:
        due=(dt.datetime.fromisoformat(planned_done[0]['completed_at'])+dt.timedelta(minutes=30)).isoformat()
        expectation='forecast';reason='模拟脚本预计今日完课；尚未发生，智能体不可提前使用。'
    else:expectation='not_applicable';reason='当前不符合首周逐课完课表扬条件。'
    covered=[x for x in candidates if x['rule']=='celebrate-lesson-completion' and x['status']=='merged_into_higher_priority']
    if covered:expectation='merged';reason=covered[0]['reason']
    add('S02','首周完课表扬','celebrate-lesson-completion',expectation,reason,due)
    from completion_service import RULES as completion_rules, TITLES
    for code,rule in completion_rules.items():
        if code=='S02':continue
        cs=[x for x in candidates if x['rule']==rule]
        actionable=[x for x in cs if x['status'] in ('eligible','already_delivered')]
        merged=[x for x in cs if x['status']=='merged_into_higher_priority']
        expectation='expected' if actionable else 'merged' if merged else 'not_applicable'
        explanation='；'.join(dict.fromkeys(x.get('reason',x.get('purpose','')) for x in (actionable or merged or cs))) or '当前没有符合条件的完课事件。'
        due=min((x['time_window']['earliest'] for x in (actionable or merged)),default=None)
        add(code,TITLES[code],rule,expectation,explanation,due)
    # These actions have their own fixed runtime entries and candidate adapters.
    # Link the actual task even when its current candidate is ineligible, so a
    # completed check or delivery is not replaced by a stale capability warning.
    import festival_service
    occasion=festival_service.event(state)
    weekday=dt.date.fromisoformat(day).weekday()
    fixed_actions=[
        ('S05','阅读节奏初次关怀','support-zero-completion',None,True,'19:00'),
        ('S07','周三阅读习惯检查','rebuild-reading-rhythm','s07',weekday==2,'19:00'),
        ('S08','周五当周收尾检查','close-week-reading-gap','s08',weekday==4,'18:00'),
        ('I01–I04','周互动','invite-reading-interaction','interaction',weekday==4,'19:00'),
        ('I05-festival','节日图文祝福','send-festival-wishes','festival',bool(occasion),'09:30'),
    ]
    fixed_rules={row[2] for row in fixed_actions}
    for code,title,rule,kind,applicable,hour in fixed_actions:
        cs=[x for x in candidates if x['rule']==rule]
        due=day+'T'+hour+':00+08:00' if applicable else None
        if cs:
            statuses={x['status'] for x in cs}
            expectation=('expected' if statuses & {'eligible','already_delivered'} else
                         'not_applicable' if statuses=={'not_eligible'} else 'unknown')
            reason='；'.join(dict.fromkeys(x.get('reason',x.get('purpose','')) for x in cs))
        else:
            expectation='conditional' if applicable else 'not_applicable'
            reason='到点读取实际学情和候选再判断是否联系。' if applicable else '今日不在该动作的固定检查日期。'
        if occasion and kind=='festival':
            reason=occasion['title']+'；'+('本轮授权模拟节点。' if occasion['simulated'] else '已核验节日日期。')+reason
        add(code,title,rule,expectation,reason,due,fixed=kind if applicable else None)
    titles={r[0]:r[1] for r in proactive.RULES}
    implemented=set(tm.RULE_MAP.values())|{'plan-weekend-makeup','weekly-learning-review','celebrate-lesson-completion'}
    codes={'celebrate-important-day':'生日','celebrate-learning-streak':'S04','close-week-reading-gap':'S08',
           'plan-weekend-makeup':'S09','weekly-learning-review':'S10'}
    for rule,title in titles.items():
        if rule in completion_rules.values() or rule in fixed_rules:continue
        cs=[x for x in candidates if x['rule']==rule]
        relevant=[h for h in assets if tm.RULE_MAP.get(int(h['id'].rsplit('-',1)[1]))==rule]
        capability='connected' if rule in implemented else 'not_connected'
        due=None;fixed=None
        if cs:
            expectation='expected';reason='当前业务接口已给出候选，仍须核验频控、去重与实际交付。';due=min(x['time_window']['earliest'] for x in cs)
        elif relevant:
            expectation='forecast';reason='今日模拟高光尚未进入可见窗口。';due=relevant[0]['candidate_at']
        else:
            expectation='unknown' if capability=='not_connected' else 'not_applicable'
            reason='模拟适配尚未接入；不能将没有候选当作条件不满足。' if capability=='not_connected' else '当前未返回该动作候选。'
        if rule=='celebrate-important-day':
            expectation='expected' if birthday_today else 'not_applicable';due=day+'T09:30:00+08:00' if birthday_today else None
            reason='今天是测试档案中的生日，09:30核验生日关怀。' if birthday_today else '测试生日为'+str(birthday)+'，今天不是生日。'
            fixed='calendar'
        if rule in ('plan-weekend-makeup','weekly-learning-review'):fixed='weekly' if cs else None
        if rule=='celebrate-learning-streak':reason+=' 连续学习由统一完课事件S04核验，旧预设高光合并，不重复触达。'
        add(codes.get(rule,'高光/服务'),title,rule,expectation,reason,due,capability,fixed)
    for code,title,reason in [('H13','成长视频','未接入视频交付能力')]:
        add(code,title,code,'unknown',reason,capability='not_connected')
    return {'day':day,'as_of':at,'operator_only':True,'birthday':birthday,'birthday_today':birthday_today,
        'service_catalog':__import__('service_catalog').catalog(),
        'timeline':sorted(timeline,key=lambda x:x['at']),'actions':actions,
        'constraints':{'auto_enabled':state.get('auto_highlights',False),'paused_today':bool(constraints)},
        'tier':next((json.loads(a['output']) for a in reversed(audits) if a['name']=='student_tier' and a['status']=='ok'),
                    {'status':'not_queried','reason':'本日尚无层级接口查询回执；不根据学情推断SABCD层级。'}),
        'notice':'预期来自规则与模拟数据；候选、任务、工具核验及微信回执分别来自实际运行。预期不代表已发送。'}
