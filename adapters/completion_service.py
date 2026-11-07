"""Completion-driven checks, separate from transport, prose and model runtime."""
import datetime as dt
import hashlib
import json
import completion_events as events

RULES = {'S02': 'celebrate-lesson-completion', 'S03': 'remind-historical-makeup',
         'S04': 'celebrate-learning-streak', 'S06': 'prevent-lesson-skipping'}
SKILLS = frozenset([*RULES.values(), 'welcome-back-to-reading'])
TITLES = {'S02': '首周完课表扬', 'S03': '缺课补读提醒', 'S04': '连续完课鼓励', 'S06': '阅读衔接提醒'}


def decisions(c, state, snapshot, raw):
    day = state['as_of'][:10]
    date = dt.date.fromisoformat(day)
    week = (date - dt.timedelta(days=date.weekday())).isoformat()
    result = []
    visible = snapshot['lessons_as_of']
    all_events = events.records(state, raw, c)
    receipts = {r[0] for r in c.execute('SELECT dedupe_key FROM proactive_receipts')}
    prior_streak = c.execute('''SELECT 1 FROM proactive_receipts pr JOIN proactive_candidates pc
        ON pr.candidate_id=pc.id WHERE pc.rule=? AND pc.day>=? AND pc.day<=? AND pr.dedupe_key LIKE ?''',
        (RULES['S04'],week,day,state['run_id']+':%')).fetchone()
    for event in all_events:
        stamp = event['completed_at']
        if stamp[:10] != day:
            continue
        original = next((l for l in raw['learning_daily'] if l['id'] == event['lesson_id']), None)
        if not original:
            continue
        registration = events.enrollment(raw, original)
        ids = {l['id'] for l in raw['learning_daily'] if events.enrollment(raw, l)['id'] == registration['id']}
        lessons = sorted([l for l in visible if l['lesson_id'] in ids], key=lambda l: l['lesson_no'])
        lesson = next((l for l in lessons if l['lesson_id'] == event['lesson_id'] and l['completed_at']), None)
        if not lesson:
            continue
        after = (dt.datetime.fromisoformat(stamp) + dt.timedelta(minutes=30)).isoformat()
        end = day + 'T23:59:59+08:00'
        # No cross-day backlog: a late-night check outside this day's window is expired.
        base = {'event_id': event['event_id'], 'completed_at': stamp, 'lesson': lesson,
                'enrollment_id': registration['id'], 'course_start_date': registration['course_start_date']}
        def add(code, eligible, reason, scope, evidence=None, first=None, last=None):
            skill = RULES[code]
            ident = ('proactive:fixture:' + state['run_id'] + ':' + skill + ':' + lesson['lesson_id']
                     if code == 'S02' else 'proactive:' + state['run_id'] + ':' + code + ':' +
                     hashlib.sha256((registration['id'] + ':' + lesson['lesson_id']).encode()).hexdigest()[:24])
            dedupe = (state['run_id'] + ':' + skill + ':' + lesson['lesson_id'] if code == 'S02'
                      else state['run_id'] + ':' + code + ':' + registration['id'] + ':' + scope)
            status = 'already_delivered' if dedupe in receipts else 'eligible' if eligible else 'not_eligible'
            if code == 'S04' and prior_streak:
                status = 'already_delivered'
            result.append({'id': ident, 'candidate_id': ident, 'day': day, 'rule': skill, 'skill': skill,
                           'title': TITLES[code], 'scope': scope, 'status': status, 'reason': reason,
                           'dedupe_key': dedupe, 'purpose': reason, 'evidence': {**base, **(evidence or {})},
                           'priority': {'S02': 70, 'S03': 40, 'S04': 80, 'S06': 60}[code],
                           'contacts_user': True, 'delivery_format': 'text',
                           'time_window': {'earliest': first or after, 'latest': last or end}})
            return result[-1]
        first_week = 0 <= (date - dt.date.fromisoformat(registration['course_start_date'])).days < 7
        praise = add('S02', first_week, '首周逐课完成后给予具体肯定' if first_week else '已过开课首周', lesson['lesson_id'])
        first_card_visible=any(str(h.get('id','')).endswith('-06') and h.get('candidate_at','')<=state['as_of']<=h.get('expires_at','') for h in snapshot.get('available_highlights',[]))
        if first_week and lesson['lesson_no'] == 1 and day == registration['course_start_date'] and first_card_visible and praise['status'] == 'eligible':
            praise.update(status='merged_into_higher_priority', reason='首课由首日完课纪念覆盖，不重复表扬')
        missing = [l for l in lessons if l['scheduled_date'] < day and l['overdue']]
        makeup = add('S03', bool(missing) and lesson['scheduled_date'] == day,
                    '当日新课已完成，历史仍有待补课程' if missing else '历史欠课已补齐，无需提醒', week,
                    {'overdue_lessons': missing, 'entry_status': 'not_connected'})
        # A streak uses fully completed scheduled natural days; same-day catch-up
        # cannot fabricate completion on the originally scheduled dates.
        by_day = {}
        for row in lessons:
            by_day.setdefault(row['scheduled_date'], []).append(row)
        def full(d):
            rows = by_day.get(str(d), [])
            expected = next((x.get('new_lesson_count') for x in raw['course_calendar'] if x['date'] == str(d)), None)
            return bool(rows) and expected == len(rows) and all(r['completed_at'] and r['completed_at'][:10] == str(d) for r in rows)
        anchor = date
        while full(anchor):
            anchor -= dt.timedelta(days=1)
        anchor += dt.timedelta(days=1)
        count = (date - anchor).days + 1 if full(date) else 0
        today_rows = by_day.get(day, [])
        threshold_event = bool(count == 3 and today_rows and stamp == max(r['completed_at'] or '' for r in today_rows))
        streak = add('S04', threshold_event, '连续三个自然日完成当日应学课程' if threshold_event else '尚未形成新的连续三日完课节点',
                     week, {'streak_from': str(anchor), 'streak_days': count,
                            'streak_lessons': [r for r in lessons if str(anchor) <= r['scheduled_date'] <= day]})
        if threshold_event and streak['status'] in ('eligible', 'already_delivered') and praise['status'] == 'eligible':
            praise.update(status='merged_into_higher_priority', reason='本次完课同时达到连续三日节点，由S04具体鼓励覆盖')
        # A hole must have completed lessons on both sides in the same ordered
        # enrollment. A missing sequence row is unknown, never an inferred gap.
        prior = [r for r in lessons if r['lesson_no'] < lesson['lesson_no'] and r['completed_at'] and r['completed_at'] <= stamp]
        gaps = []
        if prior:
            left = max(r['lesson_no'] for r in prior)
            between = [r for r in lessons if left < r['lesson_no'] < lesson['lesson_no']]
            if len(between) == lesson['lesson_no'] - left - 1:
                gaps = [r for r in between if not r['completed_at'] and r['unlocked']]
        gap_key = hashlib.sha256('|'.join(sorted(r['lesson_id'] for r in gaps)).encode()).hexdigest()[:24]
        skipping = add('S06', bool(gaps), '前后课程已完成，中间仍有未读课节' if gaps else '未发现前后已完、中间缺课的模式',
                       gap_key, {'overdue_lessons': gaps, 'preceding_lesson': max(prior, key=lambda r:r['lesson_no']) if prior else None}, first=stamp, last=min(after, end))
        # One reminder per learning event. After a narrow S06 window expires,
        # unsent S03 may still be useful; a sent S06 keeps the duplicate suppressed.
        if makeup['status'] == 'eligible' and gaps and (skipping['status'] == 'already_delivered' or state['as_of'] <= after):
            makeup.update(status='merged_into_higher_priority', reason='同一次完课的缺口由S06阅读衔接提醒优先处理')
    returning=__import__('s11_service').decisions(c,state,snapshot,raw)
    covered={x['evidence']['event_id'] for x in returning if x['status'] in ('eligible','already_delivered')}
    for item in result:
        if item['evidence']['event_id'] in covered and item['status']=='eligible':
            item.update(status='merged_into_higher_priority',reason='同次恢复学习由S11欢迎覆盖，避免表扬后又催补读')
    result.extend(returning)
    # Multiple completions on one day share the same weekly/gap receipt key.
    # Materialize only one actionable check per key, retaining other explanations.
    selected = set()
    for item in result:
        if item['status'] != 'eligible':
            continue
        if item['dedupe_key'] in selected:
            item.update(status='merged_into_higher_priority', reason='相同事项已有完课事件安排，沿用首次检查')
        else:
            selected.add(item['dedupe_key'])
    return __import__('service_hours').limit_candidates(result)
