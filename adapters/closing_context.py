"""Live, audited S08 facts and one matching course body per execution."""
import hashlib
import json


def read(day=None):
    import agent_plan as ap
    import agent_view
    import business as b
    import observability as audit
    import sql_tools as s
    import test_mode as tm
    import task_context
    import course_service
    import s08_service
    state = tm.state(); task = ap.active()
    if not state or not ap.closing_task(task):
        raise ValueError('仅当前绑定的S08任务可读取本周收尾资料')
    day = day or state['as_of'][:10]
    if task['run_id'] != state['run_id'] or day != task['day'] or day != state['as_of'][:10]:
        raise ValueError('S08资料必须使用当前任务和业务日期')
    sources = []
    def source(name, args, operation):
        value = audit.invoke(name, args, lambda: agent_view.project(operation()), origin='aggregate')
        sources.append(name)
        return value
    family = source('family_profile', {}, s.family_profile)
    def bound_candidates():
        value = task_context.candidates(day)
        return [{k: x[k] for k in ('id', 'skill', 'status', 'reason') if k in x}
                for x in (value.get('actions') or value.get('candidates', []))
                if x.get('skill') == task['skill'] and (not task.get('candidate_id') or b.original(x.get('id')) == task['candidate_id'])]
    rows = source('action_candidates', {'day': day}, bound_candidates)
    candidate = rows[0] if len(rows) == 1 else None
    def learning_facts():
        snap = tm.snapshot(day)
        summary = snap['weekly_learning']
        status, reason = s08_service.eligibility(summary, day)
        fields = ('lesson_id','lesson_no','title','scheduled_date','status','attended_at','completed_at')
        current = summary['current_week']
        lessons = [{k:x.get(k) for k in fields} for x in current['lessons']]
        return {'as_of':snap['as_of'], 'counting_basis':summary['counting_basis'],
                'current_week':{**current, 'lessons':lessons}, 'lessons_as_of':lessons,
                'status':status, 'reason':reason}
    learning = source('learning_query', {'day': day}, learning_facts)
    missing = [x for x in learning['current_week']['lessons'] if x['status'] != 'completed']
    lesson = missing[0] if learning['status'] == 'eligible' else None
    schedule = {'status':'unknown','reason':'班型或期次尚未核实'}
    if family.get('class_type') and family.get('cohort'):
        args = {'date':day,'class_type':family['class_type'],'cohort':family['cohort']}
        schedule = source('course_schedule', args, lambda:course_service.course_schedule(**args))
    course = {'status':'not_requested','reason':'未核实适用候选与匹配的今日课节'}
    if candidate and candidate['status'] == 'eligible' and lesson and schedule.get('status') == 'scheduled' and schedule.get('lesson_id') == lesson['lesson_id'] and schedule.get('lesson_no') == lesson['lesson_no']:
        args = {'class_type':schedule['class_type'],'lesson_no':lesson['lesson_no']}
        course = source('course_knowledge', args, lambda:course_service.course_knowledge(**args))
        course = {**course, 'bound_lesson_id':lesson['lesson_id']}
    def context():
        value = task_context.daily_context(day)
        return {k: value[k] for k in ('recent_conversation','conversation_window','long_term_memory',
                'preferences','contact_constraints','previous_deliveries') if k in value}
    recent = source('daily_context', {'day': day}, context)
    latest = tm.state(); active = ap.active()
    if not latest or not active or active['id'] != task['id'] or any(latest.get(k) != state.get(k) for k in ('run_id','as_of')):
        raise ValueError('S08资料读取期间任务或业务时间变化，请重新读取')
    result = {'scope': 'task_week_closing', 'as_of': state['as_of'], 'business_day': day,
              'family': family, 'candidate': candidate, 'learning': learning, 'suggested_lesson': lesson,
              'schedule': schedule, 'course': course, 'context': recent, 'fact_tools': sources}
    result['snapshot_id'] = hashlib.sha256(json.dumps({'run_id':state['run_id'], 'task_id':task['id'],
            'facts':result}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result
