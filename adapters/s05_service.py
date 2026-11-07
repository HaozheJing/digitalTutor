"""Initial S05 contact only; complete schedule evidence and weekly dedupe."""
import datetime as dt

RULE = 'support-zero-completion'


def evidence(snapshot):
    current = snapshot['weekly_learning']['current_week']
    result = {'as_of': snapshot['as_of'], 'week_start': current['start'],
              'coverage_complete': current['coverage_complete'], 'dates': [], 'lessons': []}
    if not current['coverage_complete']:
        return 'unknown', '应学课表或逐课记录覆盖不完整', result
    days = sorted({x['scheduled_date'] for x in current['lessons']})
    day = snapshot['as_of'][:10]
    if len(days) < 2 or days[-1] != day:
        return 'not_eligible', '当周尚不足两个应学日或今日无课', result
    dates = days[-2:]
    lessons = [x for x in current['lessons'] if x['scheduled_date'] in dates]
    result.update(dates=dates, lessons=lessons)
    # A completed makeup lesson on either day is learning, even if its scheduled
    # date lies outside this week. Never describe that day as zero completion.
    if any(x['status'] == 'completed' for x in lessons) or any(
            x.get('completed_at') and x['completed_at'][:10] in dates
            for x in snapshot['lessons_as_of']):
        return 'not_eligible', '核验范围已有实际完课', result
    return 'eligible', '当周最近两个应学日截至当前均未完课', result


def decision(connection, state, snapshot):
    day = state['as_of'][:10]
    status, reason, facts = evidence(snapshot)
    scope = facts['week_start']
    dedupe = state['run_id'] + ':S05:' + scope
    if connection.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?', (dedupe,)).fetchone():
        status = 'already_delivered'
    ident = 'proactive:' + state['run_id'] + ':S05:' + day
    return {'id': ident, 'candidate_id': ident, 'day': day, 'rule': RULE, 'skill': RULE,
            'title': '阅读节奏初次关怀', 'scope': scope, 'status': status, 'reason': reason,
            'dedupe_key': dedupe, 'purpose': '关心实际困难并给一个可商量的补读起点',
            'evidence': {'zero_learning': facts, 'overdue_lessons': facts['lessons']},
            'priority': 65, 'contacts_user': True, 'delivery_format': 'text',
            'time_window': {'earliest': day + 'T19:00:00+08:00', 'latest': day + 'T21:00:00+08:00'}}
