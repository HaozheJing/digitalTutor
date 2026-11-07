"""Friday final-lesson evidence and send-time validation; wakeups are unconditional."""
import datetime as dt

RULE = 'close-week-reading-gap'


def eligibility(summary, day):
    if dt.date.fromisoformat(day).weekday() != 4:
        return 'not_eligible', '非周五，不开展当周收尾提醒'
    current = summary['current_week']
    if not current['coverage_complete']:
        return 'unknown', '本周课表或逐课状态不完整，不能推断只差今日一节'
    lessons = current['lessons']
    missing = [r for r in lessons if r['status'] != 'completed']
    today = [r for r in lessons if r['scheduled_date'] == day]
    if not today:
        return 'not_eligible', '今日无课，无需当周收尾提醒'
    if len(missing) != 1 or missing[0]['scheduled_date'] != day:
        return 'not_eligible', '本周不满足只差今日一节未完成'
    return 'eligible', '本周仅今日一节未完成，其余应学课均已完成'


def candidate_id(run, day):
    return 'proactive:' + run + ':S08:' + day


def preparation(trace, day):
    """Require the timetable, visible lesson and matching course material at a chosen depth."""
    from s01_contract import preparation as lesson_preparation
    schedule = next((t.get('result', {}) for t in reversed(trace)
                     if t['name'] == 'course_schedule' and t.get('arguments', {}).get('date') == day), {})
    if schedule.get('status') != 'scheduled' or not schedule.get('class_type') or not schedule.get('cohort'):
        return '请先核实今日班型、期次和课次'
    reason = lesson_preparation(trace, schedule, day, full_only=False)
    if reason:
        return reason
    from course_navigation import matching_material
    material = matching_material(trace,schedule['class_type'],schedule['lesson_no']) or {}
    if material.get('status') != 'ok' or not material.get('body'):
        return '今日课程资料尚未核实，不能借其他课文组织收尾提醒；可自主选择对应摘要、概览或正文'
    return None


def decision(connection, state, snapshot):
    day = state['as_of'][:10]
    if dt.date.fromisoformat(day).weekday() != 4:
        return None
    summary = snapshot['weekly_learning']
    status, reason = eligibility(summary, day)
    scope = summary['current_week']['start']
    dedupe = state['run_id'] + ':S08:' + scope
    if connection.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?', (dedupe,)).fetchone():
        status = 'already_delivered'
    ident = candidate_id(state['run_id'], day)
    return {'id': ident, 'candidate_id': ident, 'day': day, 'rule': RULE, 'skill': RULE,
            'title': '周五当周收尾检查', 'scope': scope, 'status': status, 'reason': reason,
            'dedupe_key': dedupe, 'purpose': '智能体先查本周逐课学情，只差今日一节时结合对应课程与家庭安排给出收尾建议',
            'evidence': {'weekly_learning': summary,
                         'overdue_lessons': [r for r in summary['current_week']['lessons'] if r['status'] != 'completed']},
            'priority': 40, 'contacts_user': True, 'delivery_format': 'text',
            'time_window': {'earliest': day + 'T18:00:00+08:00', 'latest': day + 'T23:59:00+08:00'}}
