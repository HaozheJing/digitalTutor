"""Admission counts actual charged operations plus already promised unsent steps."""
from .domain import SHANGHAI, timestamp
from .delivery_retry import charged as is_charged
from .scheduling import step_key, operation_step_id
from datetime import date, timedelta


def committed_contacts(app, family, day, exclude_event=None):
    charged = [op for op in app.store.list('operation',family)
               if op.get('event_id') and is_charged(app,op)
               and op.get('business_date',op.get('occurred_at','')[:10])==day]
    baseline = {item['event_id'] for plan in app.store.list('plan',family)
                if plan.get('business_date')==day and plan.get('baseline_committed',True)
                for item in plan.get('items',[]) if item.get('authority','baseline')=='baseline'}
    reservations = 0
    for event in app.store.list('event',family):
        if event['id']==exclude_event or event['status'] not in {'pending','in_progress'}:
            continue
        step = event.get('next_action') or {}
        if step.get('action_kind') not in {'remind_makeup','deliver_feedback','clarify_existing'}:
            continue
        execution_day = timestamp(step['execute_at']).astimezone(SHANGHAI).date().isoformat() if step.get('execute_at') else day
        if execution_day!=day:
            continue
        grant = event.get('accepted_commitment') or {}
        granted = grant.get('status')=='active' and timestamp(grant['valid_from']).astimezone(SHANGHAI).date().isoformat()==day
        if not (granted or event['id'] in baseline):
            continue
        if any(op.get('event_id')==event['id'] and operation_step_id(op)==step_key(event) for op in charged):
            continue
        reservations += 1
    return len(charged)+reservations


def committed_reminders(app, family, day, lessons, exclude_event=None):
    wanted=date.fromisoformat(day);start=wanted-timedelta(days=wanted.weekday());end=start+timedelta(days=7)
    def same_week(value):return start.isoformat() <= value < end.isoformat()
    charged=[op for op in app.store.list('operation',family) if op.get('event_id') and is_charged(app,op)
             and op.get('action_kind')=='remind_makeup' and same_week(op.get('business_date',op.get('occurred_at','')[:10]))]
    counts={lesson:sum(lesson in op.get('lesson_refs',[]) for op in charged) for lesson in lessons}
    baseline={item['event_id']:plan['business_date'] for plan in app.store.list('plan',family)
              if same_week(plan.get('business_date','')) and plan.get('baseline_committed',True)
              for item in plan.get('items',[]) if item.get('authority','baseline')=='baseline'}
    for event in app.store.list('event',family):
        step=event.get('next_action') or {};grant=event.get('accepted_commitment') or {}
        if event['id']==exclude_event or event['status'] not in {'pending','in_progress'} or step.get('action_kind')!='remind_makeup':continue
        scheduled=timestamp(step['execute_at']).astimezone(SHANGHAI).date().isoformat() if step.get('execute_at') else baseline.get(event['id'],'')
        if not same_week(scheduled):continue
        granted=grant.get('status')=='active' and same_week(timestamp(grant['valid_from']).astimezone(SHANGHAI).date().isoformat())
        if not (granted or baseline.get(event['id'])==scheduled):continue
        if any(op.get('event_id')==event['id'] and operation_step_id(op)==step_key(event) for op in charged):continue
        for lesson in lessons:counts[lesson]+=int(lesson in event['completion']['lesson_refs'])
    return counts
