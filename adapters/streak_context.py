"""Fresh, task-bound S04 streak facts; no full-week or course-body preload."""
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
    state=tm.state();task=ap.active()
    if not state or not task or task['skill']!='celebrate-learning-streak' or task['kind'] not in ('signal','action'):
        raise ValueError('仅当前绑定的S04任务可读取连续学习资料')
    day=day or state['as_of'][:10]
    if task['run_id']!=state['run_id'] or day!=task['day'] or day!=state['as_of'][:10]:
        raise ValueError('连续学习资料必须使用当前任务和业务日期')
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
    ids={x.get('lesson_id') for x in evidence.get('streak_lessons',[]) if x.get('lesson_id')}
    def learning_facts():
        value=tm.snapshot(day)
        fields=('lesson_id','lesson_no','title','scheduled_date','completed_at','status')
        lessons=sorted([{k:x.get(k) for k in fields} for x in value.get('lessons_as_of',[]) if x.get('lesson_id') in ids],
                       key=lambda x:(x.get('scheduled_date') or '',x.get('lesson_no') or 0,x['lesson_id']))
        daily=[]
        for date in sorted({x['scheduled_date'] for x in lessons}):
            on_day=[x for x in lessons if x['scheduled_date']==date]
            daily.append({'date':date,'recorded_lessons':len(on_day),
                'completed_on_day':sum(bool(x.get('completed_at') and x['completed_at'][:10]==date) for x in on_day)})
        return {'lessons_as_of':lessons,'daily':daily}
    learning=source('learning_query',{'day':day},learning_facts)
    def context():
        value=task_context.daily_context(day)
        return {k:value[k] for k in ('recent_conversation','conversation_window','long_term_memory','preferences','contact_constraints','previous_deliveries') if k in value}
    recent=source('daily_context',{'day':day},context)
    latest=tm.state();active=ap.active()
    if not latest or not active or active['id']!=task['id'] or any(latest.get(k)!=state.get(k) for k in ('run_id','as_of')):
        raise ValueError('连续学习资料读取期间任务或业务时间变化，请重新读取')
    # Evidence remains in the actual action_candidates audit; present each set of
    # lesson facts once in the model envelope. Eligibility stays sender-owned.
    if candidate:
        candidate={**candidate,'evidence':{k:v for k,v in evidence.items() if k not in ('lesson','streak_lessons')}}
    result={'scope':'task_learning_streak','as_of':state['as_of'],'business_day':day,
            'family':family,'candidate':candidate,'streak':{'basis':'natural_days','from':evidence.get('streak_from'),
                'through':day,'days':evidence.get('streak_days'),'completed_lesson_id':evidence.get('lesson',{}).get('lesson_id')},
            'learning':learning,'context':recent,'fact_tools':sources}
    result['snapshot_id']=hashlib.sha256(json.dumps({'run_id':state['run_id'],'task_id':task['id'],'facts':result},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return result
