"""Additive learning provenance tables; no rewrite of stage-one business tables."""
from datetime import date, datetime
import re

from .domain import require
from .storage import encode

EXTENSION_VERSION = 1
SOURCE_SCHEMA = {
    'learning_source_rows': ['batch_id', 'uid', 'sheet_name', 'source_row', 'source_group',
                             'headers_json', 'values_json', 'metadata_json', 'evidence_locator'],
    'learning_summaries': ['batch_id', 'uid', 'source_group', 'metric', 'raw_value',
                           'numerator', 'denominator', 'quality_flags', 'evidence_locator'],
    'assignment_details': ['batch_id', 'uid', 'day', 'source_row', 'source_group',
                           'ai_expression_type', 'ai_expression_name', 'evidence_locator'],
    'homework_days': ['batch_id', 'uid', 'day', 'source_group', 'status', 'raw_status',
                      'source_date_label', 'question_title', 'quality_flags', 'evidence_locator'],
}
INTEGER_COLUMNS = {'day', 'source_row', 'numerator', 'denominator'}


def ensure_learning_extensions(app):
    """An independent additive schema is safe to open against existing v1/v2 DBs."""
    with app.store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS learning_extensions('
                   'batch_id TEXT PRIMARY KEY REFERENCES imports(batch_id), version INTEGER NOT NULL)')
        keys = {
            'learning_source_rows': 'batch_id,sheet_name,source_row',
            'learning_summaries': 'batch_id,uid,metric',
            'assignment_details': 'batch_id,source_row',
            'homework_days': 'batch_id,uid,day',
        }
        for name, columns in SOURCE_SCHEMA.items():
            definitions = [f'{column} {"INTEGER" if column in INTEGER_COLUMNS else "TEXT"}'
                           for column in columns]
            db.execute(f'CREATE TABLE IF NOT EXISTS {name} (' + ','.join(definitions)
                       + f', PRIMARY KEY({keys[name]}), FOREIGN KEY(batch_id) REFERENCES imports(batch_id))')
            db.execute(f'CREATE INDEX IF NOT EXISTS {name}_uid ON {name}(uid,batch_id)')


def source_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def text_or_none(value):
    value = source_value(value)
    return None if value is None else str(value)


def build_sources(batch, tables, users, days, status_maps):
    """Preserve all eight sheet rows, and materialize useful normalized fields."""
    from openpyxl.utils import get_column_letter

    result = {name: [] for name in SOURCE_SCHEMA}
    flags = []
    starts = {'汇总': 1, '作业明细': 1, '作业-按天': 3, **{name: 2 for name in status_maps}}
    for sheet, start in starts.items():
        rows = tables[sheet]
        require(len(rows) > start and rows[0][0:2] == ('用户ID', '分组'),
                'IMPORT_SCHEMA', '来源表头或元数据行缺失')
        require(all(row[0] is None for row in rows[1:start]),
                'IMPORT_SCHEMA', '日期或题目元数据行不能包含学员身份')
        data = [(number, row) for number, row in enumerate(rows[start:], start + 1) if row[0] is not None]
        require({str(row[0]) for _, row in data} == set(users), 'IMPORT_SCHEMA', '来源表用户集合不一致')
        if sheet != '作业明细':
            require(len(data) == len(users), 'IMPORT_SCHEMA', '来源表身份重复')
        headers = encode([source_value(x) for x in rows[0]])
        metadata = encode([[source_value(x) for x in row] for row in rows[1:start]])
        for number, row in data:
            group = text_or_none(row[1])
            result['learning_source_rows'].append((batch, str(row[0]), sheet, number, group,
                headers, encode([source_value(x) for x in row]), metadata,
                f'{sheet}!A{number}:{get_column_letter(len(row))}{number}'))
            if group != text_or_none(users[str(row[0])][1]):
                flags.append({'code': 'source_group_conflict', 'sheet': sheet, 'row': number})

    metrics = [('到课', '到课'), ('完课', '完课'), ('作业(提交/配置)', '作业'),
               ('AI伴读(提交/配置)', 'AI伴读'), ('打卡', '打卡')]
    require(list(tables['汇总'][0]) == ['用户ID', '分组'] + [label for label, _ in metrics],
            'IMPORT_SCHEMA', '汇总字段变化，需要重新映射')
    positive = {'到课': 'attended', '完课': 'completed', '作业': 'submitted',
                'AI伴读': 'submitted', '打卡': 'checked'}
    for number, row in enumerate(tables['汇总'][1:], 2):
        uid = str(row[0])
        for col, (_, metric) in enumerate(metrics, 2):
            raw = text_or_none(row[col])
            match = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', raw or '')
            numerator, denominator = (int(x) for x in match.groups()) if match else (None, None)
            quality = [] if match else ['summary_unparsed']
            values = [record['values'][metric] for (learner, _), record in days.items() if learner == uid]
            if 'unknown' in values:
                quality.append('summary_unverifiable')
            elif match and (numerator != values.count(positive[metric])
                            or denominator != sum(value != 'not_configured' for value in values)):
                quality.append('summary_status_conflict')
            result['learning_summaries'].append((batch, uid, text_or_none(row[1]), metric, raw,
                numerator, denominator, encode(quality), f'汇总!{get_column_letter(col + 1)}{number}'))
            flags.extend({'code': code, 'sheet': '汇总', 'row': number, 'metric': metric} for code in quality)

    for number, row in enumerate(tables['作业明细'][1:], 2):
        result['assignment_details'].append((batch, str(row[0]), int(row[2]), number,
            text_or_none(row[1]), text_or_none(row[9]), text_or_none(row[10]), f'作业明细!A{number}:P{number}'))

    rows = tables['作业-按天']
    columns = [(col, int(match.group(1))) for col, header in enumerate(rows[0])
               if (match := re.fullmatch(r'第(\d+)天', str(header)))]
    require(columns and len(columns) == len({day for _, day in columns}),
            'IMPORT_SCHEMA', '按天作业教学日缺失或重复')
    for number, row in enumerate(rows[3:], 4):
        if row[0] is None:
            continue
        uid = str(row[0])
        for col, day in columns:
            require((uid, day) in days, 'IMPORT_SCHEMA', '按天作业无法关联学情')
            status = status_maps['作业'].get(row[col], 'unknown')
            quality = [] if status != 'unknown' else ['unknown_status']
            if status != days[(uid, day)]['values']['作业']:
                quality.append('homework_day_status_conflict')
            result['homework_days'].append((batch, uid, day, text_or_none(row[1]), status,
                text_or_none(row[col]), text_or_none(rows[1][col]), text_or_none(rows[2][col]),
                encode(quality), f'作业-按天!{get_column_letter(col + 1)}{number}'))
            flags.extend({'code': code, 'sheet': '作业-按天', 'row': number, 'day': day} for code in quality)
    return result, flags


def save_sources(db, batch, sources):
    """Caller owns the import transaction, including original stage-one records."""
    for table, columns in SOURCE_SCHEMA.items():
        db.executemany(f'INSERT INTO {table} VALUES (' + ','.join('?' for _ in columns) + ')', sources[table])
    db.execute('INSERT INTO learning_extensions VALUES(?,?)', (batch, EXTENSION_VERSION))
