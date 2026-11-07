"""Transactional maintenance of declarative memories; no model client or sender."""
import json
import uuid
import preference_memory as m
import memory_protocol as p


def migrate(c):
    additions={'basis':"TEXT NOT NULL DEFAULT 'inferred'",'applicability':"TEXT NOT NULL DEFAULT 'uncertain'",
               'scope_note':"TEXT NOT NULL DEFAULT '旧版候选，需结合原始来源复核'",'expires_at':'TEXT',
               'revision':'INTEGER NOT NULL DEFAULT 0'}
    columns={r[1] for r in c.execute('PRAGMA table_info(memory_candidates)')}
    for key,kind in additions.items():
        if key not in columns:c.execute('ALTER TABLE memory_candidates ADD COLUMN '+key+' '+kind)
    columns={r[1] for r in c.execute('PRAGMA table_info(memory_runs)')}
    if 'repair_of' not in columns:c.execute('ALTER TABLE memory_runs ADD COLUMN repair_of TEXT')
    c.execute('''CREATE TABLE IF NOT EXISTS memory_prepared(token TEXT PRIMARY KEY,event_id TEXT,scope TEXT,
        revision INTEGER,cursor INTEGER,payload TEXT,evidence_hash TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS memory_history(id TEXT PRIMARY KEY,scope TEXT,memory_id TEXT,
        revision INTEGER,event_id TEXT,action TEXT,before_json TEXT,after_json TEXT,reason TEXT)''')


def memories(c,sc,keys=None,include_closed=False):
    result=[]
    for row in c.execute('SELECT * FROM memory_candidates WHERE scope=? ORDER BY revision DESC,rowid DESC',(sc,)):
        item=dict(row)
        if keys is not None and item['key'] not in keys:continue
        expired=item['expires_at'] and m.stamp(item['expires_at'])<=m.clock()
        if not include_closed and (item['status'] not in ('candidate','reported','confirmed') or expired):continue
        item['evidence']=json.loads(item['evidence'])
        item['usage']='context_only_not_permission_or_confirmed_preference'
        result.append(item)
    return result


def check_text(value,path):
    if len(value.strip())<6 or value.strip().lower() in ('test','testing','placeholder','已完成总结','没有任何变化','待补充内容'):
        raise ValueError(path+': substantive_text_required')


def read_evidence(event_id,sequences):
    p.validate({'event_id':event_id,'sequences':sequences},dict((n,sc) for n,_,sc in p.tools(m.KEYS))['memory_evidence_read'])
    with m.database() as (c,sc):
        run=m.run_row(c,sc,event_id)
        return {'items':[source(c,sc,run,seq) for seq in sequences]}


def source(c,sc,run,seq):
    if type(seq) is not int or seq>run['to_seq']:raise ValueError('memory_evidence_outside_window')
    row=c.execute('SELECT * FROM memory_journal WHERE scope=? AND seq=?',(sc,seq)).fetchone()
    if not row:raise ValueError('memory_evidence_unavailable')
    item=dict(row);item['body']=json.loads(item['body'])
    if item['body'].get('redacted'):raise ValueError('memory_evidence_unavailable')
    if row['kind']=='message':
        live=c.execute('SELECT body,visible FROM messages WHERE source_id=?',(row['source_id'],)).fetchone()
        if not live or not live['visible'] or live['body']!=item['body'].get('body'):raise ValueError('memory_source_changed')
    return item


def validate_batch(c,sc,run,packet):
    if packet['expected_revision']!=m.revision(c,sc) or run['revision']!=packet['expected_revision']:
        raise ValueError('memory_context_stale')
    if run['reviewed_seq']!=run['to_seq'] or packet['reviewed_through_sequence']!=run['to_seq']:
        raise ValueError('memory_pages_not_reviewed')
    if not packet['entries'] and not packet['changes']:check_text(packet['no_change_reason'],'no_change_reason')
    seen=set();hashes={}
    for kind in ('entries','changes'):
        for i,item in enumerate(packet[kind]):
            path=f'{kind}[{i}]';check_text(item['note'],path+'.note')
            refs=item['evidence']
            if len(set(refs))!=len(refs):raise ValueError(path+'.evidence: duplicate sequence')
            if not any(run['from_seq']<seq<=run['to_seq'] for seq in refs):raise ValueError(path+'.evidence: new_window_evidence_required')
            for seq in refs:
                row=source(c,sc,run,seq);hashes[str(seq)]=m.digest(row['body'])
                if kind=='changes' and (row['kind']!='message' or row['body'].get('speaker')!='user'):
                    raise ValueError(path+'.evidence: candidate_requires_user_evidence')
            if kind=='entries':continue
            check_text(item['scope_note'],path+'.scope_note');check_text(item['reason'],path+'.reason')
            boundary=c.execute("SELECT MAX(source_row) FROM preference_versions WHERE scope=? AND subject=? AND key=? AND status='forgotten'",(sc,item['subject'],item['key'])).fetchone()[0]
            if boundary is not None:
                fresh=[]
                for seq in refs:
                    evidence=source(c,sc,run,seq)
                    live=c.execute("SELECT rowid FROM messages WHERE source_id=? AND speaker='user' AND visible=1",(evidence['source_id'],)).fetchone()
                    fresh.append(bool(live and live[0]>boundary))
                if item['basis']!='reported' or not all(fresh):raise ValueError('forgotten_key_cannot_be_inferred')
            if item.get('expires_at'):
                at=m.stamp(item['expires_at'])
                if item['action'] not in ('expire','retract') and at<=m.clock():raise ValueError(path+'.expires_at: must be future')
            if item['action']=='add':
                if item.get('memory_id'):raise ValueError(path+'.memory_id: forbidden for add')
                marker=(item['subject'],item['key'],item['note'].strip())
                if marker in seen:raise ValueError(path+': duplicate_memory')
                seen.add(marker)
                if c.execute("SELECT 1 FROM memory_candidates WHERE scope=? AND subject=? AND key=? AND note=? AND status IN ('candidate','reported','confirmed')",(sc,*marker)).fetchone():
                    raise ValueError(path+': existing_memory_requires_update')
            else:
                mid=item.get('memory_id')
                if not mid:raise ValueError(path+'.memory_id: required')
                if mid in seen:raise ValueError(path+'.memory_id: duplicate mutation')
                seen.add(mid)
                old=c.execute('SELECT * FROM memory_candidates WHERE scope=? AND id=?',(sc,mid)).fetchone()
                if not old:raise ValueError(path+'.memory_id: memory_not_in_scope')
                if old['subject']!=item['subject'] or old['key']!=item['key']:raise ValueError(path+': memory_identity_changed; retract and add instead')
                if old['status'] not in ('candidate','reported','confirmed'):raise ValueError(path+': memory_already_closed')
    return m.digest(hashes)


def prepare(event_id,expected_revision,reviewed_through_sequence,entries,changes,no_change_reason):
    packet=dict(event_id=event_id,expected_revision=expected_revision,reviewed_through_sequence=reviewed_through_sequence,
                entries=entries,changes=changes,no_change_reason=no_change_reason)
    p.validate(packet,dict((n,sc) for n,_,sc in p.tools(m.KEYS))['memory_consolidation_prepare'])
    with m.database() as (c,sc):
        run=m.run_row(c,sc,event_id)
        evidence_hash=validate_batch(c,sc,run,packet)
        token=uuid.uuid4().hex
        c.execute('INSERT INTO memory_prepared VALUES(?,?,?,?,?,?,?)',(token,event_id,sc,expected_revision,m.cursor(c),m.s.encoded(packet),evidence_hash))
        return {'status':'validated','prepared_token':token,'event_id':event_id,'changes':len(changes),'entries':len(entries),
                'through_sequence':run['to_seq'],'applied':False,
                'review':{'instruction':'提交前逐条对照拟写记忆和原始证据，复核主体、专名、时间范围和判断强度；今天没做不能写成不再做。update整体替换：对照previous_memories保留仍有效的事实、限定和来源；没有新依据不得提升basis。发现错误重新prepare，确认忠实后才commit。',
                          'proposed_changes':changes,
                          'previous_memories':[item for item in memories(c,sc) if item['id'] in {x.get('memory_id') for x in changes}],
                          'evidence':[{'sequence':seq,**source(c,sc,run,seq)['body']} for seq in sorted({seq for item in changes for seq in item['evidence']})]}}



def record_history(c,sc,mid,rev,event,action,before,after,reason):
    c.execute('INSERT INTO memory_history VALUES(?,?,?,?,?,?,?,?,?)',
              (uuid.uuid4().hex,sc,mid,rev,event,action,m.s.encoded(before),m.s.encoded(after),reason))


def retract_from_fast(c,sc,ids,rev,key,subject,reason):
    for mid in ids:
        old=c.execute('SELECT * FROM memory_candidates WHERE id=? AND scope=?',(mid,sc)).fetchone()
        if not old or old['subject']!=subject or old['key']!=key:raise ValueError('replacement_memory_not_in_subject_key')
        if old['status'] not in ('candidate','reported','confirmed'):raise ValueError('replacement_memory_already_closed')
        c.execute("UPDATE memory_candidates SET status='retracted',revision=? WHERE id=?",(rev,mid))
        record_history(c,sc,mid,rev,'current_dialogue','retract',dict(old),{'status':'retracted'},reason)


def write_contextual(c,sc,item,rev):
    """Unknown end time stays contextual, never an unlimited operational rule."""
    ids=item.get('replaces_memory_ids',[])
    mid=ids[0] if len(ids)==1 else 'memory-'+uuid.uuid4().hex
    old=c.execute('SELECT * FROM memory_candidates WHERE id=? AND scope=?',(mid,sc)).fetchone()
    seq=c.execute("SELECT MAX(seq) FROM memory_journal WHERE scope=? AND kind='message' AND source_id=?",(sc,item['source_message_id'])).fetchone()[0]
    note=item['quote'] if m.KEYS[item['key']] else item['value']
    values=(sc,'current_dialogue',item['key'],note,m.s.encoded([seq]),'reported',item['subject'],'reported',
            'temporary',item['reason'],None,rev)
    if old:
        c.execute('''UPDATE memory_candidates SET scope=?,event_id=?,key=?,note=?,evidence=?,status=?,subject=?,
            basis=?,applicability=?,scope_note=?,expires_at=?,revision=? WHERE id=?''',(*values,mid))
    else:
        c.execute('''INSERT INTO memory_candidates(id,scope,event_id,key,note,evidence,status,subject,basis,
            applicability,scope_note,expires_at,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(mid,*values))
    after=dict(c.execute('SELECT * FROM memory_candidates WHERE id=?',(mid,)).fetchone())
    record_history(c,sc,mid,rev,'current_dialogue','update' if old else 'add',dict(old) if old else None,after,item['reason'])


def commit(event_id,prepared_token):
    with m.database() as (c,sc):
        prior=c.execute('SELECT * FROM memory_runs WHERE event_id=? AND scope=?',(event_id,sc)).fetchone()
        if prior and prior['result_hash']:
            if prior['result_hash']!=m.digest(prepared_token):raise ValueError('immutable_consolidation_result')
            return {**json.loads(prior['result']),'duplicate':True}
        row=m.run_row(c,sc,event_id)
        prepared=c.execute('SELECT * FROM memory_prepared WHERE token=? AND event_id=? AND scope=?',(prepared_token,event_id,sc)).fetchone()
        if not prepared:raise ValueError('validated_prepared_token_required')
        if prepared['cursor']!=m.cursor(c):raise ValueError('memory_context_stale')
        packet=json.loads(prepared['payload'])
        if validate_batch(c,sc,row,packet)!=prepared['evidence_hash']:raise ValueError('memory_evidence_changed')
        rev=m.revision(c,sc)+1;ids=[]
        for item in packet['changes']:
            mid=item.get('memory_id') or 'memory-'+uuid.uuid4().hex
            old=c.execute('SELECT * FROM memory_candidates WHERE id=? AND scope=?',(mid,sc)).fetchone()
            state={'retract':'retracted','expire':'expired'}.get(item['action'],'reported' if item['basis']=='reported' else 'candidate')
            values=(sc,event_id,item['key'],item['note'].strip(),m.s.encoded(item['evidence']),state,item['subject'],
                    item['basis'],item['applicability'],item['scope_note'],item.get('expires_at'),rev)
            if old:
                c.execute('''UPDATE memory_candidates SET scope=?,event_id=?,key=?,note=?,evidence=?,status=?,subject=?,
                    basis=?,applicability=?,scope_note=?,expires_at=?,revision=? WHERE id=?''',(*values,mid))
            else:
                c.execute('''INSERT INTO memory_candidates(id,scope,event_id,key,note,evidence,status,subject,basis,
                    applicability,scope_note,expires_at,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(mid,*values))
            after=dict(c.execute('SELECT * FROM memory_candidates WHERE id=?',(mid,)).fetchone())
            record_history(c,sc,mid,rev,event_id,item['action'],dict(old) if old else None,after,item['reason']);ids.append(mid)
        # Summaries remain an audit artifact, not a competing source of recall.
        if row['repair_of']:c.execute('DELETE FROM memory_summaries WHERE event_id=? AND scope=?',(row['repair_of'],sc))
        c.execute('INSERT OR REPLACE INTO memory_summaries VALUES(?,?,?)',(event_id,sc,m.s.encoded(packet['entries'])))
        result={'status':'committed','event_id':event_id,'through_sequence':row['to_seq'],'revision':rev,
                'memory_ids':ids,'active_preferences_changed':False,'repair_of':row['repair_of']}
        c.execute('UPDATE preference_scopes SET revision=? WHERE scope=?',(rev,sc))
        c.execute('UPDATE memory_runs SET result_hash=?,result=? WHERE event_id=?',(m.digest(prepared_token),m.s.encoded(result),event_id))
        c.execute('UPDATE memory_checkpoints SET seq=MAX(seq,?) WHERE scope=?',(row['to_seq'],sc))
        c.execute("UPDATE events SET status='done',completed_at=? WHERE id=?",(m.s.now(),event_id))
        return result


def repair(event_id,reason):
    """Operator-only audited reprocessing; never exposed as a model tool."""
    check_text(reason,'reason')
    with m.database() as (c,sc):
        old=c.execute('SELECT * FROM memory_runs WHERE event_id=? AND scope=?',(event_id,sc)).fetchone()
        if not old or not old['result_hash']:raise ValueError('completed_memory_run_required')
        if c.execute("SELECT 1 FROM memory_runs r JOIN events e ON e.id=r.event_id WHERE r.scope=? AND e.status<>'done'",(sc,)).fetchone():
            raise ValueError('memory_run_pending')
        ident='memory-repair:'+uuid.uuid4().hex
        c.execute('''INSERT INTO memory_runs(event_id,scope,day,from_seq,to_seq,reviewed_seq,revision,repair_of)
            VALUES(?,?,?,?,?,?,?,?)''',(ident,sc,old['day']+':repair:'+ident,old['from_seq'],old['to_seq'],old['from_seq'],m.revision(c,sc),event_id))
        __import__('live_runtime').enqueue(c,ident,m.INTERNAL,{'memory_scope':sc,'repair_of':event_id,'reason':reason,
            'from_seq':old['from_seq'],'to_seq':old['to_seq']})
        return {'status':'queued','event_id':ident,'repair_of':event_id}
