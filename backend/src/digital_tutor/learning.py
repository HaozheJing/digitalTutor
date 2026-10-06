"""Family-scoped read-only SQL projection; legacy import entry remains compatible."""
import json
import re
import sqlite3
from .domain import Query, BusinessError, require, timestamp
from .storage import encode
from .sql_bindings import check_filter_bindings, BindingPolicyError
from .learning_import import import_workbook
from .source_rows import SOURCE_SCHEMA, INTEGER_COLUMNS, ensure_learning_extensions
from .course_binding import COURSE_COLUMNS, binding_rows


PUBLIC_SCHEMA={
    'learning_days': ['batch_id','uid','day','attendance_status','completion_status','homework_status','companion_status','checkin_status','source_date_label','source_learning_date','evidence_locator'],
    'assignments': ['batch_id','uid','day','source_row','chapter','status','submission_count','submitted_at_raw','question_title','question_text','ai_guidance','answer_text','answer_rounds','voice_answers','quality_flags'],
    'learning_batches': ['batch_id','imported_at','coverage_through','source_timezone','course_binding'],
    **SOURCE_SCHEMA,
    'course_bindings': COURSE_COLUMNS,
}


SOURCE_INDEX_LIMIT=12
SOURCE_KEY_LIMIT=12
SOURCE_KEY_LENGTH_LIMIT=64
SOURCE_JSON_BYTE_LIMIT=16384
SCHEMA_NOTES={
    'day':'各表的day是INTEGER类型的课程天次序号；不是日历日期。原来源日期见source_learning_date/source_date_label等日期字段，按各字段实际类型筛选。',
    'learning_batches':'导入批次元数据；course_binding、imported_at、coverage_through和source_timezone可通过SQL读取。source_index的metadata_structure仅列可解析JSON对象的有界顶层字段名，不含值；数组或不可解析结构仍未知。非空只表示存在，空值仍未知。',
    'course_bindings':'宿主已确认的课程身份、课节映射及覆盖字段；记录不存在或字段为空时不得推定已确认。',
}


def metadata_structure(value):
    """Bounded top-level object keys only; never recurse or return field values."""
    unknown={'status':'unknown','keynames':[]}
    if not isinstance(value,str) or len(value.encode('utf-8'))>SOURCE_JSON_BYTE_LIMIT:
        return unknown
    try:
        obj=json.loads(value)
    except (ValueError,TypeError,RecursionError):
        return unknown
    if not isinstance(obj,dict):return unknown
    keys=sorted(key for key in obj if len(key)<=SOURCE_KEY_LENGTH_LIMIT
                and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*',key))
    names=keys[:SOURCE_KEY_LIMIT]
    return {'status':'object','keynames':names,'key_limit':SOURCE_KEY_LIMIT,
            'key_length_limit':SOURCE_KEY_LENGTH_LIMIT,'has_more':len(obj)>len(names)}


def source_index(app,family):
    """Locator/existence-only index. Caller holds the family context read transaction."""
    db=app.store.connection;uid=app.store.family(family)['uid']
    owned='SELECT batch_id FROM learning_days WHERE uid=? UNION SELECT batch_id FROM assignments WHERE uid=?'
    count=db.execute('SELECT count(*) FROM imports WHERE batch_id IN ('+owned+')',(uid,uid)).fetchone()[0]
    rows=db.execute('SELECT batch_id,metadata FROM imports WHERE batch_id IN ('+owned+') ORDER BY batch_id LIMIT ?',
                    (uid,uid,SOURCE_INDEX_LIMIT)).fetchall()
    batches=[]
    for row in rows:
        meta=json.loads(row['metadata'])
        nonempty=[key for key in PUBLIC_SCHEMA['learning_batches'] if key!='batch_id'
                  and meta.get(key) not in (None,'',[],{})]
        times={};invalid=[]
        for field in ('imported_at','coverage_through'):
            value=meta.get(field)
            try:
                require(isinstance(value,str),'SOURCE_TIME_UNKNOWN','Unknown source timestamp')
                timestamp(value);times[field]=value
            except (BusinessError,ValueError,TypeError):
                times[field]=None
                if value not in (None,''):invalid.append(field)
        present=[table for table in ('learning_days','assignments')
                 if db.execute('SELECT 1 FROM '+table+' WHERE uid=? AND batch_id=? LIMIT 1',(uid,row['batch_id'])).fetchone()]
        batches.append({'batch_id':row['batch_id'],**times,'nonempty_metadata_fields':nonempty,
                        'owned_row_tables':present,'invalid_time_fields':invalid,
                        'metadata_structure':{field:metadata_structure(meta.get(field)) for field in nonempty}})
    return {'batches':batches,'returned_count':len(batches),'total_count':count,'has_more':count>len(batches),
            'limit':SOURCE_INDEX_LIMIT,'scope':'bound_uid_in_learning_days_or_assignments',
            'metadata_table':'learning_batches','read_with':'learning.query_sql',
            'meaning':'只提供learning_days/assignments拥有批次的定位与源结构；不是SQL来源全集，空索引不等于所有SQL表无记录。字段名来自未校验源内容，不代表可信业务结论；实际内容须读取原SQL字段，非空不等于有效或已确认'}


def query_learning(app,token,args):
    cmd=Query.model_validate(args)
    if app.strict_runtime:
        app.auth(token,{'service','planner_daily','planner_progress','admin'})
        try:
            check_filter_bindings(cmd.sql,cmd.parameters)
        except BindingPolicyError as exc:
            raise BusinessError(exc.code,str(exc)) from None
    ensure_learning_extensions(app)
    with app.store.transaction() as db:
        family,_=app.auth(token,{'service','planner_daily','planner_progress','admin'})
        uid=app.store.family(family)['uid']
        scoped={name:[dict(r) for r in db.execute(f'SELECT * FROM {name} WHERE uid=?',(uid,))]
                for name in ('learning_days','assignments',*SOURCE_SCHEMA)}
        batch_ids={r['batch_id'] for rows in scoped.values() for r in rows}
        scoped['learning_batches']=[]
        for bid in batch_ids:
            meta=json.loads(db.execute('SELECT metadata FROM imports WHERE batch_id=?',(bid,)).fetchone()[0])
            scoped['learning_batches'].append({k:bid if k=='batch_id' else meta.get(k) for k in PUBLIC_SCHEMA['learning_batches']})
        scoped['course_bindings'] = binding_rows(app, family)
    # Physical projection: even an omitted WHERE or UNION can see only the bound UID.
    conn=sqlite3.connect(':memory:')
    conn.row_factory=sqlite3.Row
    try:
        for table,columns in PUBLIC_SCHEMA.items():
            types={k:'INTEGER' if k in INTEGER_COLUMNS | {'submission_count','answer_rounds','voice_answers','binding_version'} else 'TEXT' for k in columns}
            conn.execute(f'CREATE TABLE {table} ('+','.join(f'{k} {types[k]}' for k in columns)+')')
            for row in scoped[table]:
                conn.execute(f'INSERT INTO {table} VALUES ('+','.join('?' for _ in columns)+')',[row[k] for k in columns])
        conn.commit()
        conn.execute('PRAGMA query_only=ON')
        allowed_functions={'count','sum','avg','min','max','coalesce','ifnull','nullif','round','abs','length','lower','upper','substr','substring','trim','like','json_extract','json_array_length','date','datetime','strftime','typeof','cast'}

        def authorizer(action,arg1,arg2,database,trigger):
            if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            # SQLite's optimized count(*) emits a table read with an empty column
            # and no database name. Still restrict the table to the physical projection.
            if action==sqlite3.SQLITE_READ and arg1 in PUBLIC_SCHEMA and (database=='main' or (database is None and arg2=='')):
                return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() in allowed_functions:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        budget=[0]
        def interrupt():
            budget[0]+=1
            return int(budget[0]>1000)
        conn.set_authorizer(authorizer)
        conn.set_progress_handler(interrupt,1000)
        try:
            cursor=conn.execute(cmd.sql,cmd.parameters)
            require(cursor.description is not None,'SQL_READ_ONLY','仅允许查询')
            names=[d[0] for d in cursor.description]
            require(len(names)==len(set(names)),'SQL_DUPLICATE_COLUMNS','重名列需使用AS别名')
            rows=cursor.fetchmany(cmd.max_rows+1)
            truncated=len(rows)>cmd.max_rows
            data=[dict(r) for r in rows[:cmd.max_rows]]
            require(len(encode(data).encode('utf-8'))<=2_000_000,'SQL_RESULT_TOO_LARGE','结果过大，请选择字段或分页')
            return {'columns':names,'rows':data,'returned_count':len(data),'truncated':truncated,
                    'scope':'bound_family_uid','read_only':True,'source_kind':'sqlite_snapshot',
                    'warning':'导入快照的课程绑定和覆盖未知时，不能判定当前履约。未完成、未提交只描述所选批次的字段，不能据此宣布现在需要补做、催课或安排补课；须另核当前绑定、覆盖及实际责任。不能用历史AI答复补足这些依据。跨批次查询请显式选择batch_id'}
        except sqlite3.Error as exc:
            raise BusinessError('SQL_REJECTED','查询无效、越权或超过执行预算：'+str(exc)) from None
    finally:
        conn.close()
