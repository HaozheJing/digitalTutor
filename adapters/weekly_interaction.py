"""One Friday invitation shared by four model-selected conversation angles."""
import datetime as dt

RULE = 'invite-reading-interaction'


def decision(connection, state, snapshot):
    day = state['as_of'][:10]
    if dt.date.fromisoformat(day).weekday() != 4:
        return None
    week = snapshot['weekly_learning']['current_week']
    learned = [r for r in week['lessons'] if r['status'] == 'completed']
    status = 'eligible' if learned else 'not_eligible' if week['coverage_complete'] else 'unknown'
    dedupe = state['run_id'] + ':weekly-interaction:' + week['start']
    if connection.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?', (dedupe,)).fetchone():
        status = 'already_delivered'
    ident = 'proactive:' + state['run_id'] + ':weekly-interaction:' + week['start']
    return {'id': ident, 'candidate_id': ident, 'day': day, 'rule': RULE, 'skill': RULE,
            'title': '周互动', 'scope': week['start'], 'status': status,
            'reason': '从本周已学内容自主选择互动角度' if learned else '本周暂无可核实的已学课程',
            'dedupe_key': dedupe, 'purpose': '增进与孩子和家庭的交流，邀请表达并认真承接',
            'evidence': {'weekly_learning': snapshot['weekly_learning'], 'learned_lessons': learned},
            'priority': 35, 'contacts_user': True, 'delivery_format': 'text',
            'time_window': {'earliest': day + 'T19:00:00+08:00', 'latest': day + 'T23:59:00+08:00'}}


def preparation(trace, snapshot):
    from course_navigation import matching_material
    import course_service
    binding = course_service.binding(snapshot['profile']['uid'])
    if not binding:
        return '先核实当前课程绑定'
    for lesson in snapshot['weekly_learning']['current_week']['lessons']:
        if lesson['status'] != 'completed':
            continue
        material = matching_material(trace, binding['class_type'], lesson['lesson_no'], full_only=True) or {}
        if material.get('status') == 'ok' and material.get('body'):
            return None
    return '请读取本周一节已学课程的对应正文，再组织有依据的互动'
