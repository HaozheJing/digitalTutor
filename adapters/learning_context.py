"""Current-day facts through one entry point, with real underlying read receipts.

The host and model consume the same projected results. Aggregation is not a
model tool call for each source and never substitutes for send-time checks.
"""
import hashlib
import json


def read(day=None):
    import agent_view
    import observability as audit
    import sql_tools as s
    import test_mode as tm
    import course_service
    import task_context

    import agent_plan
    task = agent_plan.active()
    if task and task.get("skill") == "celebrate-lesson-completion":
        return __import__("completion_context").read(day)
    if task and task.get("skill") == "remind-historical-makeup":
        return __import__("makeup_context").read(day)

    if task and task.get("skill") == "prevent-lesson-skipping":
        return __import__("skip_context").read(day)

    if task and task.get("skill") == "support-zero-completion":
        return __import__("zero_context").read(day)

    if task and task.get("skill") == "celebrate-learning-streak":
        return __import__("streak_context").read(day)

    if task and task.get("skill") == "rebuild-reading-rhythm":
        return __import__("rhythm_context").read(day)

    if task and task.get('skill') == 'close-week-reading-gap':
        return __import__('closing_context').read(day)

    state = tm.state()
    if not state:
        raise ValueError('当前学情来源未启用')
    today = state['as_of'][:10]
    day = day or today
    s.validate_day(day)
    if day != today:
        raise ValueError('统一当日学情仅接受当前业务日期')

    sources = []
    def source(name, args, operation):
        value = audit.invoke(name, args, lambda: agent_view.project(operation()), origin='aggregate')
        sources.append(name)
        return value

    family = source('family_profile', {}, s.family_profile)

    def current_learning():
        value = tm.snapshot(day)
        return {'business_time': state['as_of'], 'business_day': day,
                'lessons_as_of': [x for x in value.get('lessons_as_of', []) if x.get('scheduled_date') == day]}

    learning = source('learning_query', {'day': day}, current_learning)
    binding = {k: family.get(k) for k in ('class_type', 'cohort')}
    if all(binding.values()):
        args = {'date': day, **binding}
        schedule = source('course_schedule', args, lambda: course_service.course_schedule(**args))
    else:
        schedule = {'status': 'unknown', 'date': day, 'reason': '班型或期次尚未核实'}

    # Resolve and read the exact current lesson on EVERY request, including
    # retries on the same day. Completion is checked by the sender, not used
    # as a prerequisite to assembling the morning invitation.
    course = {'status': 'not_requested', 'reason': '今日课次尚未确定'}
    if schedule.get('status') == 'scheduled':
        args = {'class_type': schedule['class_type'], 'lesson_no': schedule['lesson_no']}
        course = source('course_knowledge', args, lambda: course_service.course_knowledge(**args))

    def relevant_context():
        value = task_context.daily_context(day)
        keys = ('business_day', 'business_time', 'timezone', 'recent_conversation',
                'conversation_window', 'long_term_memory', 'preferences',
                'contact_constraints', 'previous_deliveries')
        return {k: value[k] for k in keys if k in value}

    context = source('daily_context', {'day': day}, relevant_context)
    guide = source('reading_guide_context', {'day': day}, lambda: __import__('s01_reading_guides').planned(schedule,day)) if task and task.get('skill')=='remind-reading' and __import__('s01_reading_guides').required() else None
    latest = tm.state()
    if not latest or any(latest.get(k) != state.get(k) for k in ('run_id', 'as_of')):
        raise ValueError('学情读取期间业务时间或轮次变化，请重新读取')
    result = {'scope': 'today', 'as_of': state['as_of'], 'business_day': day,
              'family': family, 'schedule': schedule, 'learning': learning,
              'course': course,
              'context': context, 'fact_tools': sources}
    if guide is not None:result['reading_guide']=guide
    result['snapshot_id'] = hashlib.sha256(json.dumps(
        {'run_id': state['run_id'], 'facts': result}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result
