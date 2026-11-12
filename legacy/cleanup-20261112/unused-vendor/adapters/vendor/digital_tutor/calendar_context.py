"""Calendar arithmetic over already-visible structured dates; no business authority."""
from datetime import date,datetime,timedelta
from .domain import SHANGHAI

DATE_KEYS={'now','due_at','execute_at','review_at','occurred_at','received_at',
           'business_date','valid_from','valid_until','coverage_through'}
WEEKDAYS=('星期一','星期二','星期三','星期四','星期五','星期六','星期日')

def calendar_context(envelope):
    dates=set()
    def parse(value):
        if not isinstance(value,str):return None
        try:
            if len(value)==10:return date.fromisoformat(value)
            value=datetime.fromisoformat(value)
            return value.astimezone(SHANGHAI).date() if value.tzinfo else None
        except (ValueError,OverflowError):return None
    def visit(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in DATE_KEYS or (key=='value' and value.get('precision') in {'date','datetime'} and value.get('meaning') in {'schedule','completion_deadline'}):
                    day=parse(item)
                    if day:dates.add(day)
                if isinstance(item,(dict,list)):visit(item)
        elif isinstance(value,list):
            for item in value:visit(item)
    visit(envelope)
    today=parse(envelope.get('now'))
    if today:
        dates.add(today)
        if today<date.max:dates.add(today+timedelta(days=1))
    return {'timezone':'Asia/Shanghai','today':today.isoformat() if today else None,
        'date_weekdays':[{'date':d.isoformat(),'iso_weekday':d.isoweekday(),'weekday':WEEKDAYS[d.weekday()]} for d in sorted(dates)],
        'scope':'Calendar arithmetic only for visible structured dates plus today/tomorrow; not proof of source truth, an event occurring, an accepted schedule or contact authority. Free text dates are not parsed.'}
