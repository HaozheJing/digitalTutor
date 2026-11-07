"""Read-only separation of a reporting obligation from dated learning evidence."""
from .domain import BusinessError, SHANGHAI, timestamp


def reporting_period(event):
    identity = event.get('source_identity') or {}
    if identity.get('kind') != 'periodic_text_delivery':
        return None
    try:
        start = timestamp(identity['period_start']).astimezone(SHANGHAI)
        end = timestamp(identity['period_end']).astimezone(SHANGHAI)
        if start >= end:
            raise ValueError('Invalid reporting period')
    except (BusinessError, ValueError, TypeError, KeyError):
        return {'status': 'unknown', 'source': 'event.source_identity',
                'is_learning_activity_evidence': False}
    return {'status': 'known', 'source': 'event.source_identity',
            'timezone': 'Asia/Shanghai', 'start': start.isoformat(), 'end': end.isoformat(),
            'is_learning_activity_evidence': False,
            'meaning': '这是原反馈合同的时间范围，不证明源学习记录发生在该范围；对外日期使用此北京时间，不能截取UTC身份字段的日期。'}


def calendar_date_presence(app, family):
    """Only family-scoped date-field presence, not course/period attribution."""
    uid = app.store.family(family)['uid']
    rows = app.store.connection.execute(
        "SELECT batch_id, count(*) AS total_rows, "
        "sum(CASE WHEN source_learning_date IS NULL OR trim(source_learning_date)='' THEN 1 ELSE 0 END) AS missing_date_rows "
        "FROM learning_days WHERE uid=? GROUP BY batch_id ORDER BY batch_id LIMIT 13", (uid,)).fetchall()
    return {'source': 'learning_days.source_learning_date', 'limit': 12, 'has_more': len(rows)>12,
            'batches': [{'batch_id': row['batch_id'], 'total_rows': row['total_rows'],
                         'missing_date_rows': row['missing_date_rows'],
                         'nonempty_date_rows': row['total_rows']-row['missing_date_rows']} for row in rows[:12]],
            'period_attribution': 'not_established_by_field_presence',
            'meaning': '仅凭这些缺少日期的行，尚不能建立周期归属。其他获准原始来源若提供可核实发生日期与身份关联，仍须读取核验。非空也不表示日期有效、课节匹配或属于该周期；须读原字段核对。标签、导入时间、覆盖截至时间和day序号不自动证明学习发生日期。'}
