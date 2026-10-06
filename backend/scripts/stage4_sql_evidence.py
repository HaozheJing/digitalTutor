"""Runner-only evidence for the frozen 4B1 learning case, never model input."""
from collections import Counter
import json
import sqlite3

from digital_tutor.learning import PUBLIC_SCHEMA
from digital_tutor.source_rows import INTEGER_COLUMNS, SOURCE_SCHEMA
from digital_tutor.course_binding import binding_rows
from digital_tutor.sql_bindings import check_filter_bindings, BindingPolicyError, _TOKEN


def family_sources(app, family):
    """Read the same family source projection, without changing the live database."""
    db=app.store.connection; uid=app.store.family(family)['uid']
    tables={name:[dict(row) for row in db.execute('SELECT * FROM '+name+' WHERE uid=?',(uid,))]
            for name in ('learning_days','assignments',*SOURCE_SCHEMA)}
    batches={row['batch_id'] for rows in tables.values() for row in rows}
    tables['learning_batches']=[]
    for batch in sorted(batches):
        metadata=json.loads(db.execute('SELECT metadata FROM imports WHERE batch_id=?',(batch,)).fetchone()[0])
        tables['learning_batches'].append({key:batch if key=='batch_id' else metadata.get(key)
                                           for key in PUBLIC_SCHEMA['learning_batches']})
    tables['course_bindings']=binding_rows(app,family)
    return tables


def _rows(sql, parameters, tables, max_rows):
    # Same physical schema and authorizer restrictions as query_learning.
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    try:
        integers=INTEGER_COLUMNS|{'submission_count','answer_rounds','voice_answers','binding_version'}
        for table,columns in PUBLIC_SCHEMA.items():
            db.execute('CREATE TABLE '+table+' ('+','.join(k+(' INTEGER' if k in integers else ' TEXT') for k in columns)+')')
            db.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in columns)+')',
                           [[row.get(k) for k in columns] for row in tables.get(table,[])])
        db.commit();db.execute('PRAGMA query_only=ON')
        functions={'count','sum','avg','min','max','coalesce','ifnull','nullif','round','abs','length','lower','upper','substr','substring','trim','like','json_extract','json_array_length','date','datetime','strftime','typeof','cast'}
        def authorize(action,a,b,database,trigger):
            if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_RECURSIVE):return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and a in PUBLIC_SCHEMA and (database=='main' or (database is None and b=='')):return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_FUNCTION and (b or '').lower() in functions:return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        budget=[0]
        def interrupt():
            budget[0]+=1
            return int(budget[0]>1000)
        db.set_authorizer(authorize);db.set_progress_handler(interrupt,1000)
        cursor=db.execute(sql,parameters)
        names=[column[0] for column in cursor.description]
        if len(names)!=len(set(names)):raise ValueError('duplicate_columns')
        rows=[dict(row) for row in cursor.fetchmany(max_rows+1)]
        if len(rows)>max_rows:raise ValueError('truncated_replay')
        return rows
    finally:db.close()


def _bag(rows):
    return Counter(json.dumps(row,sort_keys=True,ensure_ascii=False) for row in rows)


def learning_query_evidence(calls, family, tables, target):
    """Conservative target-row contribution check; not a semantic pass.

    Arbitrary selected columns/aliases are retained. Aggregate/ambiguous equal
    projections, LIMIT/OFFSET and unproven indirect dataflow may be uncovered.
    """
    match=lambda row:all(row.get(key)==value for key,value in target.items())
    targets=[row for row in tables['learning_days'] if match(row)]
    result={'observed':False,'target':target,'attempts':[],'semantic_status':'not_reviewed'}
    if len(targets)!=1:
        result['reason']='oracle_target_not_unique';return result
    only={**tables,'learning_days':targets}
    without={**tables,'learning_days':[row for row in tables['learning_days'] if not match(row)]}
    for call in calls:
        if call.get('family_id')!=family:continue
        for index,trace in enumerate(call.get('native',{}).get('tool_trace',[])):
            if trace.get('tool')!='learning.query_sql' or trace.get('ok') is not True:continue
            attempt={'model_index':call.get('index'),'trace_index':index,'accepted':False}
            result['attempts'].append(attempt)
            args=trace.get('arguments',{});sql=args.get('sql','');params=args.get('parameters',{})
            payload=trace.get('result',{})
            actual=payload.get('result',{}) if payload.get('ok') is True else {}
            try:
                names=check_filter_bindings(sql,params)['direct_predicate_parameters']
                attempt['predicate_parameters']=names
                if not names:raise ValueError('no_bound_predicate')
                if any(m.lastgroup=='word' and m.group().upper() in {'LIMIT','OFFSET'} for m in _TOKEN.finditer(sql)):
                    raise ValueError('limit_or_offset_not_covered')
                if actual.get('scope')!='bound_family_uid' or actual.get('read_only') is not True:
                    raise ValueError('missing_scoped_read_receipt')
                if not actual.get('rows') or actual.get('truncated'):raise ValueError('empty_or_truncated_result')
                limit=args.get('max_rows',200)
                replay=_rows(sql,params,tables,limit)
                if _bag(actual['rows'])!=_bag(replay):raise ValueError('trace_replay_mismatch')
                full=_bag(replay);target_rows=_bag(_rows(sql,params,only,limit))
                absent=_bag(_rows(sql,params,without,limit))
                witness=(full & target_rows)-absent
                if not witness:raise ValueError('target_row_contribution_unproven')
                influential=[]
                source_values=[value for value in targets[0].values() if value is not None]
                for name in names:
                    if params[name] not in source_values:continue
                    changed={**params,name:'__runner_oracle_nonmatching_value__'}
                    try:changed_rows=_bag(_rows(sql,changed,only,limit))
                    except (sqlite3.Error,ValueError,TypeError):continue
                    # A parameter in SELECT can change row content without filtering
                    # anything (WHERE :p=:p). Require loss of target-only rows.
                    if sum(changed_rows.values()) < sum(target_rows.values()):influential.append(name)
                if not influential:raise ValueError('relevant_parameter_influence_unproven')
                attempt.update(accepted=True,influential_parameters=influential,witness_rows=sum(witness.values()))
                result['observed']=True
            except BindingPolicyError as exc:attempt['reason']=exc.code
            except (sqlite3.Error,ValueError,TypeError,KeyError) as exc:attempt['reason']=str(exc)
    return result
