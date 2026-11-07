"""Fresh S03 facts, bound to the completion and overdue lessons of this task."""
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
    if not state or not task or task['skill']!='remind-historical-makeup' or task['kind'] not in ('signal','action'):
        raise ValueError('仅当前绑定的S03任务可读取补读资料')
    day=day or state['as_of'][:10]
    if task['run_id']!=state['run_id'] or day!=task['day'] or day!=state['as_of'][:10]:
        raise ValueError('补读资料必须使用当前任务和业务日期')
    sources=[]
    def source(name,args,operation):
        value=audit.invoke(name,args,lambda:agent_view.project(operation()),origin='aggregate')
        sources.append(name);return value
    family=source('family_profile',{},s.family_profile)
    def bound_candidates():
        value=task_context.candidates(day)
        return {'candidates':[{k:x[k] for k in ('id','skill','status','reason','evidence') if k in x}
            for x in (value.get('actions') or value.get('candidates',[])) if b.original(x.get('id'))==task['candidate_id']]}
    rows=source('action_candidates',{'day':day},bound_candidates)['candidates']
    candidate=rows[0] if len(rows)==1 else None
    evidence=(candidate or {}).get('evidence',{})
    completed_id=evidence.get('lesson',{}).get('lesson_id')
    missing_ids={x.get('lesson_id') for x in evidence.get('overdue_lessons',[]) if x.get('lesson_id')}
    def learning_facts():
        current=tm.snapshot(day).get('lessons_as_of',[])
        completed=next((x for x in current if x.get('lesson_id')==completed_id),None)
        overdue=sorted([x for x in current if x.get('lesson_id') in missing_ids and x.get('overdue') and not x.get('completed_at')],
                       key=lambda x:(x.get('scheduled_date',''),x.get('lesson_no') or 0,x['lesson_id']))
        return {'completed_lesson':completed,'overdue_lessons':overdue,'suggested_lesson':overdue[0] if overdue else None}
    learning=source('learning_query',{'day':day},learning_facts)
    lesson=learning['suggested_lesson']
    course={'status':'not_requested','reason':'待补课节或班型尚未核实'}
    if lesson and family.get('class_type') and lesson.get('lesson_no'):
        args={'class_type':family['class_type'],'lesson_no':lesson['lesson_no']}
        course=source('course_knowledge',args,lambda:course_service.course_knowledge(**args))
        course={**course,'bound_lesson_id':lesson['lesson_id']}
    def context():
        value=task_context.daily_context(day)
        return {k:value[k] for k in ('recent_conversation','conversation_window','long_term_memory','preferences','contact_constraints','previous_deliveries') if k in value}
    recent=source('daily_context',{'day':day},context)
    latest=tm.state();active=ap.active()
    if not latest or not active or active['id']!=task['id'] or any(latest.get(k)!=state.get(k) for k in ('run_id','as_of')):
        raise ValueError('补读资料读取期间任务或业务时间变化，请重新读取')
    result={'scope':'task_historical_makeup','as_of':state['as_of'],'business_day':day,
            'family':family,'candidate':candidate,'learning':learning,'course':course,
            'entry':{'status':evidence.get('entry_status','unknown')},'context':recent,'fact_tools':sources}
    result['snapshot_id']=hashlib.sha256(json.dumps({'run_id':state['run_id'],'task_id':task['id'],'facts':result},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return result
