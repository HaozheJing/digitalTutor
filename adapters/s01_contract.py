"""Pure S01 validation shared by the real sender and teacher simulations."""

def lesson_state(schedule, learning, day):
    if schedule.get('date')!=day:
        return {'status':'unknown','reason':'课表日期不对应当前业务日'}
    if schedule.get('status') in ('no_lesson','not_started'):
        return {'status':'not_eligible','reason':'今日无应学课程'}
    if schedule.get('status')!='scheduled':
        return {'status':'unknown','reason':'今日课次尚未核实'}
    rows=[x for x in learning.get('lessons_as_of',[]) if x.get('scheduled_date')==day]
    if len(rows)!=1 or rows[0].get('lesson_id')!=schedule.get('lesson_id') or rows[0].get('lesson_no')!=schedule.get('lesson_no'):
        return {'status':'unknown','reason':'课表课次与学情课节不一致'}
    if rows[0].get('status')=='completed':return {'status':'not_eligible','reason':'今日课程已完成'}
    if rows[0].get('status') not in ('not_started','in_progress','absent'):
        return {'status':'unknown','reason':'今日完课状态尚未核实'}
    return {'status':'eligible','evidence':rows,'schedule':schedule,'business_day':day}


def preparation(trace,schedule,day,full_only=True):
    """Return a repair reason, never manufacture or silently rewrite a message."""
    def last(name,match):
        return next((x.get('result',{}) for x in reversed(trace) if x['name']==name and
                     all(x.get('arguments',{}).get(k)==v for k,v in match.items())),None)
    plan=last('course_schedule',{'date':day,'class_type':schedule['class_type'],'cohort':schedule['cohort']})
    compact_match=bool(plan and plan.get('class_type')==schedule['class_type'] and plan.get('cohort')==schedule['cohort'] and any(x.get('date')==day and x.get('has_lesson') is True and x.get('lesson_no')==schedule['lesson_no'] for x in plan.get('days',[])))
    if not compact_match and (not plan or any(plan.get(k)!=schedule.get(k) for k in ('status','lesson_id','lesson_no'))):
        return '请先查询当前日期、班型和期次的课表'
    learning=last('learning_query',{'day':day})
    if not learning or lesson_state(schedule,learning,day)['status']!='eligible':
        return '请先查询今日课次的明确未完课学情'
    from course_navigation import matching_material
    material=matching_material(trace,schedule['class_type'],schedule['lesson_no'],full_only=full_only)
    if not material or material.get('status') not in ('ok','no_match','unavailable','changed'):
        return '请先读取今日课次对应的课程知识，可用course_knowledge或course_read；不能将未查询当作资料缺失'
    if material['status']=='ok' and not material.get('body'):
        return '课程查询未返回正文，请重新核实'
    return None
