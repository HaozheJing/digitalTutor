"""Business-day boundaries independent of the timestamp's original offset."""
from datetime import datetime, timedelta
from .domain import SHANGHAI, timestamp, require


def business_day(at):
    return (timestamp(at) if isinstance(at, str) else at).astimezone(SHANGHAI).date().isoformat()


def day_end(at):
    dt = (timestamp(at) if isinstance(at, str) else at).astimezone(SHANGHAI)
    return datetime.combine(dt.date() + timedelta(days=1), datetime.min.time(), tzinfo=SHANGHAI).isoformat()


def expiry(scope, supplied, source_at):
    if scope == 'source_day':
        end = day_end(source_at)
        require(supplied is None or timestamp(supplied) == timestamp(end),
                'INVALID_EXPIRY', 'source_day截止必须是来源业务日的次日零点')
        return end
    if scope == 'until':
        require(supplied is not None, 'TIME_REQUIRED', 'until必须指定截止时间')
        require(timestamp(supplied) > timestamp(source_at), 'INVALID_EXPIRY', '截止必须晚于来源时间')
        return timestamp(supplied).astimezone(SHANGHAI).isoformat()
    require(scope in {'persistent', 'indefinite'} and supplied is None,
            'INVALID_EXPIRY', '持续状态不能同时指定截止')
    return None
