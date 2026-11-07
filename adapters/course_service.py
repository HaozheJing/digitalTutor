"""Stable schedule adapter and exact OpenViking course resolver."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import knowledge_search as knowledge

HERE=Path(__file__).parent
SCHEDULE=HERE/'course_schedule_config.json'
CATALOG=HERE/'course_catalog.json'


def binding(uid):
    return json.loads(SCHEDULE.read_text(encoding='utf8'))['bindings'].get(uid,{})


def course_schedule(date,class_type,cohort):
    """Replace this provider with the business API without changing tool inputs."""
    if not isinstance(date,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):raise ValueError('日期需为YYYY-MM-DD')
    day=dt.date.fromisoformat(date)
    if not isinstance(class_type,str) or not isinstance(cohort,(str,int)) or isinstance(cohort,bool):raise ValueError('班型和期次无效')
    level=class_type.strip().upper();term=str(cohort).strip().removesuffix('期')
    config=json.loads(SCHEDULE.read_text(encoding='utf8'))
    rows=[x for x in config['calendars'] if x['class_type']==level and x['cohort']==term]
    base={'date':date,'class_type':level,'cohort':term,'lesson_no':None}
    if config['provider']!='weekday_calendar' or len(rows)!=1:return {**base,'status':'unknown','reason':'班型期次尚未配置课表'}
    rule=rows[0];start=dt.date.fromisoformat(rule['start_date'])
    if rule.get('known_until') and date>rule['known_until']:return {**base,'status':'unknown','reason':'超出已登记课程范围'}
    if day<start:return {**base,'status':'not_started','reason':'尚未开课'}
    if day.weekday() not in rule['weekdays']:return {**base,'status':'no_lesson','reason':'当天无新课'}
    days=(day-start).days+1;weeks,remainder=divmod(days,7)
    number=weeks*len(rule['weekdays'])+sum((start.weekday()+i)%7 in rule['weekdays'] for i in range(remainder))
    if rule.get('lesson_count') and number>rule['lesson_count']:return {**base,'status':'unknown','reason':'超出已登记课次数量'}
    from course_navigation import entry_points
    points=entry_points(level,number)
    return {**base,'status':'scheduled','lesson_no':number,'lesson_id':f'{level}-{number:03d}',
            **({'knowledge':points} if points else {})}


def entry(class_type,lesson_no):
    if not isinstance(class_type,str) or type(lesson_no) is not int or lesson_no<1:raise ValueError('需要班型和正整数课次')
    matches=[x for x in json.loads(CATALOG.read_text(encoding='utf8'))['courses'] if x['class_type']==class_type.strip().upper() and x['lesson_no']==lesson_no]
    return matches[0] if len(matches)==1 else None


def course_knowledge(class_type,lesson_no):
    item=entry(class_type,lesson_no)
    if not item:return {'status':'no_match','class_type':class_type,'lesson_no':lesson_no,'reason':'该班型课次尚无登记课程资料'}
    try:
        value=knowledge.cli(['read',item['uri']])
        if isinstance(value,dict):value=value.get('content')
        if not isinstance(value,str):raise ValueError('正文不可读')
        if hashlib.sha256(value.encode()).hexdigest()!=item['sha256']:
            return {**item,'status':'changed','reason':'课程版本发生变化，需重新核对索引'}
        # Keep full registered body and review status. No semantic summary or
        # different book may silently stand in for this lesson.
        return {**item,'status':'ok','body':value,'live_read':True,'exact_match':True}
    except Exception:
        return {**item,'status':'unavailable','reason':'课程知识库暂时不可读'}
