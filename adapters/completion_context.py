"""Fresh facts for the exact lesson bound to the current S02 task, never a day cache."""
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
    state=tm.state();task=ap.active()
    if not state or not task or task['skill']!='celebrate-lesson-completion' or task['kind'] not in ('signal','action'):
        raise ValueError('仅当前绑定的S02任务可读取完课资料')
    day=day or state['as_of'][:10]
    if day!=task['day'] or day!=state['as_of'][:10]:raise ValueError('完课资料必须使用当前任务业务日期')
    sources=[]
    def source(name,args,operation):
        value=audit.invoke(name,args,lambda:agent_view.project(operation()),origin='aggregate')
        sources.append(name);return value
    family=source('family_profile',{},s.family_profile)
    def bound_candidates():
        value=task_context.candidates(day)
        # Project only this task's candidate; never expose other same-day completions.
        return {'candidates':[{k:x[k] for k in ('id','skill','status','reason','evidence') if k in x}
                for x in (value.get('actions') or value.get('candidates',[])) if b.original(x.get('id'))==task['candidate_id']]}
    candidates=source('action_candidates',{'day':day},bound_candidates)['candidates']
    candidate=candidates[0] if len(candidates)==1 else None
    evidence=(candidate or {}).get('evidence',{})
    lesson_id=evidence.get('lesson',{}).get('lesson_id')
    def bound_learning():
        value=tm.snapshot(day)
        return {key:[x for x in value.get(key,[]) if lesson_id and x.get('lesson_id')==lesson_id]
                for key in ('lessons_as_of','quiz_attempts','expression_assignments')}
    learning=source('learning_query',{'day':day},bound_learning)
    lessons=learning['lessons_as_of'];lesson=lessons[0] if len(lessons)==1 else None
    course={'status':'not_requested','reason':'绑定课节或班型尚未核实'}
    if lesson and family.get('class_type') and lesson.get('lesson_no'):
        args={'class_type':family['class_type'],'lesson_no':lesson['lesson_no']}
        course=source('course_knowledge',args,lambda:course_service.course_knowledge(**args))
    def context():
        value=task_context.daily_context(day)
        return {k:value[k] for k in ('recent_conversation','conversation_window','long_term_memory','preferences','contact_constraints','previous_deliveries') if k in value}
    recent=source('daily_context',{'day':day},context)
    latest=tm.state();active=ap.active()
    if not latest or not active or active['id']!=task['id'] or any(latest.get(k)!=state.get(k) for k in ('run_id','as_of')):
        raise ValueError('完课资料读取期间任务或业务时间变化，请重新读取')
    # Course content explains the lesson; it is never evidence of this child's answer.
    result={'scope':'task_completed_lesson','as_of':state['as_of'],'business_day':day,
            'family':family,'candidate':candidate,'learning':learning,'course':course,'context':recent,'fact_tools':sources}
    result['snapshot_id']=hashlib.sha256(json.dumps({'run_id':state['run_id'],'task_id':task['id'],'facts':result},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return result
