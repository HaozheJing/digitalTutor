"""Read-only XLSX ingestion with atomic, backwards-compatible provenance upgrade."""
import hashlib
import json
from pathlib import Path
import re
from .domain import require
from .storage import encode
from .source_rows import (EXTENSION_VERSION, ensure_learning_extensions, build_sources,
                          save_sources, text_or_none)

STATUS_MAPS = {
    '到课': {'已到课':'attended','未到课':'not_attended'},
    '完课': {'已完课':'completed','未完课':'incomplete'},
    '作业': {'已提交':'submitted','未提交':'not_submitted','无作业':'not_configured'},
    'AI伴读': {'已提交':'submitted','未提交':'not_submitted','无AI作业':'not_configured'},
    '打卡': {'已打卡':'checked','未打卡':'not_checked'},
}


def import_workbook(app, path):
    """Trusted local import: never associates real learners with mock conversations."""
    import openpyxl
    from openpyxl.utils import get_column_letter

    source = Path(path).resolve()
    raw = source.read_bytes()
    batch = hashlib.sha256(raw).hexdigest()
    ensure_learning_extensions(app)
    with app.store.lock:
        existing = app.store.connection.execute('SELECT metadata FROM imports WHERE batch_id=?',(batch,)).fetchone()
        extension = app.store.connection.execute('SELECT version FROM learning_extensions WHERE batch_id=?',(batch,)).fetchone()
    if existing and extension and extension['version'] == EXTENSION_VERSION:
        return {'outcome':'duplicate', **json.loads(existing['metadata'])}
    book = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        required=set(STATUS_MAPS)|{'汇总','作业-按天','作业明细'}
        require(required.issubset(book.sheetnames),'IMPORT_SCHEMA','缺少预期工作表')
        tables={s.title:list(s.iter_rows(values_only=True)) for s in book if s.title in required}
    finally:
        book.close()
    users={str(r[0]):r for r in tables['汇总'][1:] if r[0] is not None}
    require(len(users)==len(tables['汇总'])-1,'IMPORT_SCHEMA','汇总身份空缺或重复')
    days={}
    raw_statuses={}
    flags=[]
    for sheet, mapping in STATUS_MAPS.items():
        rows=tables[sheet]
        require(rows[0][0]=='用户ID' and rows[0][1]=='分组','IMPORT_SCHEMA','身份表头不匹配')
        columns=[(i,int(re.fullmatch(r'第(\d+)天',str(h)).group(1))) for i,h in enumerate(rows[0]) if re.fullmatch(r'第(\d+)天',str(h))]
        require(len(columns)==240 and {d for _,d in columns}==set(range(1,241)),'IMPORT_SCHEMA','教学日列缺失或重复')
        data=[(index,r) for index,r in enumerate(rows[2:],3) if r[0] is not None]
        require({str(r[0]) for _,r in data}==set(users) and len(data)==len(users),'IMPORT_SCHEMA','工作表用户集合不一致')
        raw_statuses[sheet]={}
        for rownum,row in data:
            uid=str(row[0])
            for col,day in columns:
                key=(uid,day)
                days.setdefault(key,{'values':{},'source_date_label':text_or_none(rows[1][col]),'source_learning_date':None,'refs':{}})
                value=row[col]
                days[key]['values'][sheet]=mapping.get(value,'unknown')
                days[key]['refs'][sheet]=f'{sheet}!{get_column_letter(col+1)}{rownum}'
                raw_statuses[sheet][key]=value
                if value not in mapping:
                    flags.append({'code':'unknown_status','sheet':sheet,'cell':f'{get_column_letter(col+1)}{rownum}'})
    detail=tables['作业明细']
    expected=['用户ID','分组','第N天','学习日期','章节','提交状态','提交次数','提交时间','作业题目','AI表达题类型','AI表达题名称','题干','AI引导语','用户回答','回答轮数','含语音回答数']
    require(list(detail[0])==expected,'IMPORT_SCHEMA','作业明细字段变化，需要重新映射')
    assignments=[]
    keys=set()
    for rownum,row in enumerate(detail[1:],2):
        uid=str(row[0]); day=int(row[2])
        require((uid,day) in days,'IMPORT_SCHEMA','作业明细缺少学情关联')
        key=(uid,day,str(row[4]),str(row[8]))
        require(key not in keys,'IMPORT_SCHEMA','明细键重复，禁止静默覆盖')
        keys.add(key)
        record_flags=[]
        if row[5]=='已提交' and row[6]==0:
            record_flags.append('submission_count_conflict')
        if not row[11]:
            record_flags.append('question_text_missing')
        status=STATUS_MAPS['作业'].get(row[5],'unknown')
        if status != days[(uid,day)]['values']['作业']:
            record_flags.append('submission_status_conflict')
        days[(uid,day)]['source_learning_date']=str(row[3]) if row[3] is not None else None
        assignments.append((batch,uid,day,rownum,row[4],status,row[6],text_or_none(row[7]),row[8],row[11],row[12],row[13],row[14],row[15],encode(record_flags)))
        flags.extend({'code':f,'sheet':'作业明细','row':rownum} for f in record_flags)
    sources, source_flags = build_sources(batch, tables, users, days, STATUS_MAPS)
    flags.extend(source_flags)
    require(hashlib.sha256(source.read_bytes()).hexdigest()==batch,'SOURCE_CHANGED','读取期间源文件发生变化')
    metadata={'batch_id':batch,'source_name':source.name,'source_sha256':batch,'imported_at':app.clock.iso(),
              'exported_at':None,'coverage_through':None,'source_timezone':None,'course_binding':None,
              'user_count':len(users),'learning_day_count':len(days),'assignment_count':len(assignments),
              'quality_flags':flags,'source_kind':'xlsx_snapshot','source_unchanged':True,
              'extension_version':EXTENSION_VERSION,'source_sheet_count':len(tables),
              'source_row_count':len(sources['learning_source_rows'])}
    with app.store.transaction() as db:
        existing=db.execute('SELECT metadata FROM imports WHERE batch_id=?',(batch,)).fetchone()
        extension=db.execute('SELECT version FROM learning_extensions WHERE batch_id=?',(batch,)).fetchone()
        if existing and extension and extension['version'] == EXTENSION_VERSION:
            return {'outcome':'duplicate',**json.loads(existing['metadata'])}
        if existing:
            previous = json.loads(existing['metadata'])
            metadata = {**previous, 'quality_flags':flags, 'extension_version':EXTENSION_VERSION,
                        'source_sheet_count':len(tables), 'source_row_count':len(sources['learning_source_rows'])}
            db.execute('UPDATE imports SET metadata=? WHERE batch_id=?',(encode(metadata),batch))
        else:
            db.execute('INSERT INTO imports VALUES(?,?)',(batch,encode(metadata)))
            for (uid,day),value in days.items():
                db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (batch,uid,day,*(value['values'][s] for s in STATUS_MAPS),value['source_date_label'],value['source_learning_date'],encode(value['refs'])))
            db.executemany('INSERT INTO assignments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',assignments)
        save_sources(db, batch, sources)
        # Imported facts are new inputs to explicitly bound readers, never create families.
        for family in db.execute('SELECT id,uid FROM families').fetchall():
            if family['uid'] in users:
                app.store.bump(family['id'])
    return {'outcome':'upgraded' if existing else 'applied',**metadata}


