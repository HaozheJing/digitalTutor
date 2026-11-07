"""Small model-facing schedule contract; replace provider independently later."""
import datetime as dt
import json
import course_service

def date_value(value):
    if not isinstance(value,str):raise ValueError('date_requires_YYYY_MM_DD')
    result=dt.date.fromisoformat(value)
    if result.isoformat()!=value:raise ValueError('date_requires_YYYY_MM_DD')
    return result

def read(class_type,cohort,date=None,start_date=None,end_date=None):
    if not isinstance(class_type,str) or not class_type.strip() or not isinstance(cohort,str) or not cohort.strip():raise ValueError('class_type_and_cohort_required')
    level=class_type.strip().upper();term=cohort.strip().removesuffix('期')
    config=json.loads(course_service.SCHEDULE.read_text())
    matches=[x for x in config['calendars'] if x['class_type']==level and x['cohort']==term]
    rule=matches[0] if len(matches)==1 and config.get('provider')=='weekday_calendar' else None
    if date is not None and (start_date is not None or end_date is not None):raise ValueError('choose_single_date_or_range')
    if (start_date is None)!=(end_date is None):raise ValueError('both_range_dates_required')
    if date is not None:start=end=date_value(date)
    elif start_date is not None:start,end=date_value(start_date),date_value(end_date)
    else:
        if not rule:return {'status':'unknown','source':'mock_schedule','class_type':level,'cohort':term,'days':[]}
        year=date_value(rule['start_date']).year;start,end=dt.date(year,1,1),dt.date(year,12,31)
    if start>end or (end-start).days>365:raise ValueError('schedule_range_requires_1_to_366_days')
    days=[];at=start
    known_start=date_value(rule['start_date']) if rule else None
    known_end=date_value(rule['known_until']) if rule and rule.get('known_until') else None
    while at<=end:
        present=number=None
        if rule and known_end and known_start<=at<=known_end:
            present=at.weekday() in rule['weekdays']
            if present:
                span=(at-known_start).days+1;weeks,extra=divmod(span,7)
                number=weeks*len(rule['weekdays'])+sum((known_start.weekday()+i)%7 in rule['weekdays'] for i in range(extra))
                if rule.get('lesson_count') is not None and number>rule['lesson_count']:present=number=None
        days.append({'date':at.isoformat(),'has_lesson':present,'lesson_no':number});at+=dt.timedelta(days=1)
    return {'status':'ok' if rule else 'unknown','source':'mock_schedule','class_type':level,'cohort':term,'days':days}
