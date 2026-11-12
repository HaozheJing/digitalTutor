"""Family-scoped frozen daily inputs, staged proposals and atomic publication.

Messages/tasks remain authoritative. No transaction spans inference. Coverage is
per immutable source digest, never just max(seq), so holes and edits survive.
"""
from copy import deepcopy
from datetime import timedelta
from typing import Literal
import json
from pydantic import Field
from .domain import Strict, ID, require, timestamp, BusinessError
from .storage import digest, encode
from .business_time import business_day, expiry
from .service_context import message_source_digest

RECIPE='daily-memory-v1'
ROLE='memory_daily'
CHUNK_CHARS=6000


def optional(app,kind,key,family):
    row=app.store.connection.execute('SELECT 1 FROM records WHERE kind=? AND id=? AND family_id=?',(kind,key,family)).fetchone()
    return app.store.get(kind,key,family) if row else None


def state(app,family):
    return optional(app,'memory_state',family,family) or {'revision':0,'covered':{},'handoff':None}


def source_rows(app,family):
    rows=app.store.connection.execute("SELECT i.seq,r.id,r.version,r.data FROM message_index i JOIN records r ON r.kind='message' AND r.id=i.message_id WHERE i.family_id=? ORDER BY i.seq",(family,))
    result=[]
    for row in rows:
        m={'id':row['id'],'version':row['version'],**json.loads(row['data'])}
        if m.get('historical_reference') or timestamp(m['occurred_at'])>app.clock.now() or timestamp(m.get('received_at',m['occurred_at']))>app.clock.now():continue
        if m['direction']=='outbound' and not m.get('actual_agent_delivery',True):continue
        result.append({'seq':row['seq'],'digest':message_source_digest(m),'day':business_day(m.get('received_at',m['occurred_at'])),'message':m})
    return result


def memory_signature(app,family):
    return digest({kind:[(m['id'],m['version']) for m in app.store.list(kind,family)]
        for kind in ('memory','memory_suppression')})


def suppressed(app,family):
    return {s for row in app.store.list('memory_suppression',family) for s in row['source_ids']}


def task_signature(app,family):
    from .workflow import enabled
    # Source digests below fence the frozen prefix. Appending a message or
    # interpreting it does not change the task/control ledger read by this batch.
    kinds=('event','pending','control')
    return digest({k:[(r['id'],r['version']) for r in app.store.list(k,family)] for k in kinds})


def valid_memory(app,family,m):
    if m.get('status')!='active' or (m.get('valid_until') and timestamp(m['valid_until'])<=app.clock.now()):return False
    for ref in m.get('source_refs',[]):
        current=optional(app,'message',ref['id'],family)
        if not current or message_source_digest(current)!=ref['digest']:return False
    return True


def coverage(app,family):
    s=state(app,family);rows=source_rows(app,family);covered=s.get('covered',{})
    missing=[r for r in rows if covered.get(str(r['seq']))!=r['digest']]
    prefix=0
    for r in rows:
        if covered.get(str(r['seq']))!=r['digest']:break
        prefix=r['seq']
    yesterday=[r for r in missing if r['day']<business_day(app.clock.now())]
    return {'revision':s['revision'],'continuous_covered_seq':prefix,'missing_count':len(missing),
        'older_missing_count':len(yesterday),'oldest_missing_day':missing[0]['day'] if missing else None,
        'missing_source_ids':[r['message']['id'] for r in yesterday[:20]],
        'covered_at':s.get('published_at'),'read_tool':'resource.read',
        'uncovered_history_signature':digest([(r['seq'],r['digest']) for r in yesterday]),
        'personalization_verified':not yesterday,'coverage_is_not_semantic_quality':True}


def invalidate(app,family,old=None,deleting=False):
    """Called in the same memory write transaction; no derived old text survives."""
    if deleting and old:
        refs={r['id'] for r in old.get('source_refs',[])}|{old.get('source_message_id')}
        for row in app.store.connection.execute("SELECT data FROM history WHERE kind='memory' AND id=? AND family_id=?",(old['id'],family)):
            previous=json.loads(row[0]);refs.update(r['id'] for r in previous.get('source_refs',[]))
            refs.add(previous.get('source_message_id'))
        refs.discard(None);key='suppress:'+digest([family,old['id']])
        existing=optional(app,'memory_suppression',key,family)
        app.store.put('memory_suppression',key,family,{'memory_id':old['id'],'source_ids':sorted(refs),
            'topic_key':old.get('topic_key'),'deleted_at':app.clock.iso()},existing['version'] if existing else None)
        # Model candidates and diary copies are derived data, not raw chat archives.
        for kind in ('memory_batch','memory_diary'):
            for row in app.store.list(kind,family):
                row.pop('proposal',None);row.pop('handoff',None)
                if kind=='memory_batch' and row['status']!='published':row['status']='superseded'
                saved=app.store.put(kind,row['id'],family,row,row['version'])
                app.store.connection.execute('UPDATE history SET data=? WHERE kind=? AND id=? AND family_id=?',
                    (encode({k:v for k,v in saved.items() if k not in {'id','version'}}),kind,row['id'],family))
    s=state(app,family);s.update(revision=s['revision']+1,handoff=None,invalidated_at=app.clock.iso())
    saved=app.store.put('memory_state',family,family,s,s.get('version'))
    if deleting:
        app.store.connection.execute("UPDATE history SET data=? WHERE kind='memory_state' AND id=? AND family_id=?",
            (encode({k:v for k,v in saved.items() if k not in {'id','version'}}),family,family))


def freeze(app,family,day):
    """Caller holds transaction. Freeze only missing/revised source rows that day."""
    require(day<=business_day(app.clock.now()),'FUTURE_SOURCE','不能整理未来工作日')
    s=state(app,family);rows=[r for r in source_rows(app,family) if r['day']==day and s.get('covered',{}).get(str(r['seq']))!=r['digest']]
    if not rows:return None
    # Publish a bounded prefix, then schedule the remaining day. Coverage remains
    # incomplete until every prefix is published; never silently skip a source.
    bounded=[];chars=0
    for row in rows:
        size=len(row['message']['body'])
        if bounded and (chars+size>48000 or len(bounded)>=100):break
        require(size<=48000,'MEMORY_SOURCE_TOO_LARGE','单条原文超过日终批次容量，保留待整理并显式报告')
        bounded.append(row);chars+=size
    rows=bounded
    base=memory_signature(app,family);tasks=task_signature(app,family)
    refs=[{'id':r['message']['id'],'seq':r['seq'],'digest':r['digest']} for r in rows]
    key='batch:'+digest([family,day,RECIPE,refs,base,tasks])
    old=optional(app,'memory_batch',key,family)
    if old:return old
    chunks=[]
    for r in rows:
        body=r['message']['body']
        for start in range(0,max(1,len(body)),CHUNK_CHARS):
            chunks.append({'id':r['message']['id'],'offset':start,'end':min(len(body),start+CHUNK_CHARS)})
    return app.store.put('memory_batch',key,family,{'business_day':day,'recipe':RECIPE,'base_signature':base,'task_signature':tasks,
        'source_refs':refs,'chunks':chunks,'read_chunks':[],'cutoff_seq':max(r['seq'] for r in rows),
        'status':'frozen','created_at':app.clock.iso(),'attempts':0,'read_run_id':None})


def snapshot_current(app,family,batch):
    require(batch['base_signature']==memory_signature(app,family),'MEMORY_BASE_CHANGED','当前记忆已更正或删除，重新冻结输入')
    require(batch.get('task_signature')==task_signature(app,family),'MEMORY_TASK_CHANGED','任务或控制已变化，重新冻结日终输入')
    for ref in batch['source_refs']:
        m=app.store.get('message',ref['id'],family)
        require(message_source_digest(m)==ref['digest'],'MEMORY_SOURCE_CHANGED','来源正文已变，需要新代际')


STALE_CODES={'MEMORY_BASE_CHANGED','MEMORY_TASK_CHANGED','MEMORY_SOURCE_CHANGED','MEMORY_BATCH_CLOSED'}


def supersede(app,family,batch_id,reason):
    from contextlib import nullcontext
    # Claim already owns a transaction, whereas host completion does not.
    # Hold the connection lock before detecting which caller owns the boundary.
    with app.store.lock:
        with nullcontext() if app.store.connection.in_transaction else app.store.transaction():
            b=app.store.get('memory_batch',batch_id,family)
            if b['status'] not in {'published','superseded'}:
                b.update(status='superseded',superseded_reason=reason,superseded_at=app.clock.iso())
                app.store.put('memory_batch',b['id'],family,b,b['version'])


def bound(app,token,batch_id):
    family,binding=app.auth(token,{ROLE})
    require(binding.get('memory_batch_id')==batch_id,'MEMORY_BATCH_SCOPE','批次不属于当前运行')
    batch=app.store.get('memory_batch',batch_id,family)
    require(batch['status'] not in {'published','superseded'},'MEMORY_BATCH_CLOSED','批次已发布或失效')
    snapshot_current(app,family,batch)
    return family,binding,batch


class BatchRead(Strict):
    batch_id: ID
    offset: int=Field(default=0,ge=0)
    limit: int=Field(default=32,ge=1,le=32)


def read_batch(app,token,args):
    cmd=BatchRead.model_validate(args)
    with app.store.transaction():
        family,binding,b=bound(app,token,cmd.batch_id)
        require(cmd.offset<=len(b['chunks']),'INVALID_OFFSET','分页位置超出范围')
        positions=[];chars=0
        for pos in range(cmd.offset,min(len(b['chunks']),cmd.offset+cmd.limit)):
            chunk=b['chunks'][pos];size=chunk['end']-chunk['offset']
            if positions and chars+size>12000:break
            positions.append(pos);chars+=size
        blocked=suppressed(app,family);records=[]
        for pos in positions:
            chunk=b['chunks'][pos];m=app.store.get('message',chunk['id'],family)
            from datetime import date
            source_day=date.fromisoformat(business_day(m['occurred_at']))
            monday=source_day-timedelta(days=source_day.weekday())
            records.append({'message_id':m['id'],'direction':m['direction'],'occurred_at':m['occurred_at'],
                'source_calendar':{'anchor_day':source_day.isoformat(),'iso_weekday':source_day.isoweekday(),
                    'this_week':{str(n+1):(monday+timedelta(days=n)).isoformat() for n in range(7)},
                    'rule':'本周以来源发生日为锚、周一开始；过去日期不能擅自滚到下周。日历只做算术，不授权日程或证明用户意图。'},
                'received_at':m.get('received_at',m['occurred_at']),'speaker_role':(m.get('source_metadata') or {}).get('speaker_role','unknown'),
                'offset':chunk['offset'],'end':chunk['end'],'total_chars':len(m['body']),
                'body':None if m['id'] in blocked else m['body'][chunk['offset']:chunk['end']],
                'suppressed_from_memory':m['id'] in blocked})
        read=set(b['read_chunks']) if b.get('read_run_id')==binding.get('run_id') else set()
        b.update(read_chunks=sorted(read|set(positions)),read_run_id=binding.get('run_id'))
        app.store.put('memory_batch',b['id'],family,b,b['version'])
        end=cmd.offset+len(positions)
        return {'records':records,'next_offset':end,'has_more':end<len(b['chunks']),
            'total_chunks':len(b['chunks']),'read_chunks':len(b['read_chunks']),'batch_id':b['id']}


class SourceRef(Strict):
    message_id: ID
    excerpt: str=Field(min_length=1,max_length=2000)


class TaskRead(Strict):
    batch_id: ID
    offset: int=Field(default=0,ge=0)
    limit: int=Field(default=20,ge=1,le=30)


def read_tasks(app,token,args):
    cmd=TaskRead.model_validate(args)
    with app.store.transaction():
        family,_,batch=bound(app,token,cmd.batch_id)
        rows=[{'kind':kind,**{k:v for k,v in row.items() if k in {'id','version','status','goal_text','disposition','valid_until','action_kinds','reason'}}}
              for kind in ('event','pending','control') for row in app.store.list(kind,family)]
        page=rows[cmd.offset:cmd.offset+cmd.limit];end=cmd.offset+len(page)
        return {'records':page,'total':len(rows),'next_offset':end,'has_more':end<len(rows),
                'snapshot':batch['task_signature'],'contract':'只读实时账本；记忆不能改变任务或控制状态。'}


class DailyItem(Strict):
    subject: Literal['parent','child','family','unknown']
    topic: str=Field(min_length=1,max_length=80)
    body: str=Field(min_length=1,max_length=1200)
    kind: Literal['stated_preference','reported_fact','service_interpretation']
    sources: list[SourceRef]=Field(min_length=1,max_length=8)
    memory_id: ID | None=None
    change: Literal['replace','retain'] | None=Field(default=None,description='已有记忆必须明确操作。retain由宿主保留原正文/期限/来源并追加本次确认依据；replace仅用于当前确实变化的属性。未提及的旧记忆不需要放进items。')
    expected_version: int | None=Field(default=None,ge=1)
    temporal_scope: Literal['source_day','until','persistent']='persistent'
    valid_until: str | None=None
    persistence_basis: Literal['explicit_general','repeated_pattern'] | None=Field(default=None,description='persistent必填：explicit_general是原文明确一般偏好/固定习惯，repeated_pattern是不同日期重复的事实；一次抱怨、临时感受或作文情节不能据此推广。')


class MemoryResolution(Strict):
    memory_id: ID
    expected_version: int=Field(ge=1)
    sources: list[SourceRef]=Field(min_length=1,max_length=8)


class Proposal(Strict):
    batch_id: ID
    items: list[DailyItem]=Field(default_factory=list,max_length=50)
    resolved_memories: list[MemoryResolution]=Field(default_factory=list,max_length=30,description='用本批新入站证据关闭已结束的遗留问题记忆。仅关闭记忆条目，不删除聊天、不修改任务/控制或标记业务已答。')
    handoff: str=Field(default='',max_length=1200)
    handoff_source_ids: list[ID]=Field(default_factory=list,max_length=30)
    unresolved_task_ids: list[ID]=Field(default_factory=list,max_length=20)


def validate_proposal(app,family,b,cmd):
    from .workflow import enabled
    snapshot_current(app,family,b);ids={r['id'] for r in b['source_refs']};blocked=suppressed(app,family)
    require(set(cmd.handoff_source_ids)<=ids-blocked,'MEMORY_SOURCE_SCOPE','交接来源越界或已删除')
    require(not cmd.handoff or cmd.handoff_source_ids,'MEMORY_EVIDENCE_REQUIRED','交接需来源依据')
    seen=set();updated=set()
    for item in cmd.items:
        key=digest([item.subject,item.topic.strip().casefold()])
        require(key not in seen,'MEMORY_DUPLICATE_TOPIC','同批同主体主题只能有一个当前结论');seen.add(key)
        times=[]
        for ref in item.sources:
            require(ref.message_id in ids-blocked,'MEMORY_SOURCE_SCOPE','记忆依据必须属于本批且未被删除抑制')
            m=app.store.get('message',ref.message_id,family)
            require(m['direction']=='inbound' and ref.excerpt in m['body'],'MEMORY_EVIDENCE_REQUIRED','记忆依据必须是入站原文片段')
            times.append(m['occurred_at'])
        newest=max(times,key=timestamp)
        if enabled(app) and item.temporal_scope=='persistent' and item.change!='retain':
            require(item.persistence_basis is not None,'MEMORY_PERSISTENCE_BASIS','长期记忆须说明明确一般偏好或跨日重复依据；一次感受用source_day，不推广为长期习惯。')
            if item.persistence_basis=='repeated_pattern':
                require(len({business_day(t) for t in times})>=2,'MEMORY_PERSISTENCE_BASIS','重复习惯须有至少两个不同日期的本批原文依据，否则用有期限的观察。')
        if item.kind=='service_interpretation':
            require(item.temporal_scope=='until' and item.valid_until is not None,'MEMORY_OBSERVATION_EXPIRY','推测须标明复核截止，最多30天')
            require(timestamp(item.valid_until)<=timestamp(newest)+timedelta(days=30),'MEMORY_OBSERVATION_EXPIRY','推测期限不能超过30天')
        expiry(item.temporal_scope,item.valid_until,newest)
        old=optional(app,'memory',item.memory_id,family) if item.memory_id else next((m for m in app.store.list('memory',family) if m.get('topic_key')==key and m.get('status')=='active'),None)
        if item.memory_id:
            require(old and old['status']=='active','MEMORY_DELETED','记忆不存在或已删除')
            require(item.memory_id not in updated,'MEMORY_DUPLICATE_TOPIC','一批不能重复更正同一记忆')
            updated.add(item.memory_id)
            require(old.get('subject','unknown')==item.subject,'MEMORY_SUBJECT_CHANGED','更正不能把家长事实转移给孩子')
        if enabled(app) and old:
            require(item.memory_id==old['id'] and item.expected_version==old['version'] and item.change is not None,
                    'MEMORY_CHANGE_REQUIRED','旧主题须引用memory_id和当前expected_version，明确replace或retain；不变时retain，未提及则省略。')
        if item.change=='retain':
            require(old and valid_memory(app,family,old),'MEMORY_RETAIN_INVALID','仅保留有效旧记忆，不能保留已删或失效内容')
        if old:require(timestamp(newest)>=max(timestamp(old['occurred_at']),timestamp(old.get('last_reaffirmed_at',old['occurred_at']))),'MEMORY_SOURCE_OUTDATED','迟到旧原文不能覆盖较新事实或更新的确认')
    for item in cmd.resolved_memories:
        old=optional(app,'memory',item.memory_id,family)
        require(old and valid_memory(app,family,old) and old['version']==item.expected_version,
                'MEMORY_RESOLUTION_STALE','待结清记忆不存在、已变更或已失效')
        require(item.memory_id not in updated and old.get('topic_key') not in seen,'MEMORY_DUPLICATE_TOPIC','同条记忆不能同时更正与结清')
        updated.add(item.memory_id)
        for ref in item.sources:
            require(ref.message_id in ids-blocked,'MEMORY_SOURCE_SCOPE','结清依据必须在当前批次且未受删除抑制')
            m=app.store.get('message',ref.message_id,family)
            require(m['direction']=='inbound' and ref.excerpt in m['body'] and timestamp(m['occurred_at'])>=max(timestamp(old['occurred_at']),timestamp(old.get('last_reaffirmed_at',old['occurred_at']))),
                    'MEMORY_EVIDENCE_REQUIRED','结清需晚于原记忆的真实入站原文依据')
    for task in cmd.unresolved_task_ids:
        require(any(optional(app,kind,task,family) for kind in ('event','pending','job')),'MEMORY_TASK_SCOPE','任务引用不存在于当前家庭')


def propose(app,token,args):
    cmd=Proposal.model_validate(args)
    with app.store.transaction():
        family,binding,b=bound(app,token,cmd.batch_id)
        require(b.get('read_run_id')==binding.get('run_id') and len(b['read_chunks'])==len(b['chunks']),
            'MEMORY_COVERAGE_INCOMPLETE','先分页读取本批全部来源，不能只处理第一页')
        from .workflow import enabled
        if enabled(app):
            existing={m['id']:m['version'] for m in app.store.list('memory',family) if valid_memory(app,family,m)}
            require(not existing or (b.get('memory_review_run_id')==binding.get('run_id') and
                    all(b.get('reviewed_memories',{}).get(k)==v for k,v in existing.items())),
                    'MEMORY_BASE_UNREAD','先memory.read分页核对全部当前有效记忆及版本，不能只读当天聊天后重写旧属性。')
        validate_proposal(app,family,b,cmd)
        b.update(status='proposed',proposal=cmd.model_dump(),proposed_at=app.clock.iso())
        saved=app.store.put('memory_batch',b['id'],family,b,b['version'])
        return {'batch_id':b['id'],'status':'proposed','version':saved['version'],'items':len(cmd.items),'published':False}


def publish(app,family,batch_id,token=None):
    """Host only, after the native run returns. All effects in one transaction."""
    with app.store.transaction():
        if token:
            current,_=app.auth(token,{ROLE})
            require(current==family,'MEMORY_BATCH_SCOPE','发布家庭不匹配')
        b=app.store.get('memory_batch',batch_id,family)
        if b['status']=='published':return {'duplicate':True,'revision':b['published_revision']}
        require(b['status']=='proposed','MEMORY_PROPOSAL_MISSING','没有完成的日终候选')
        cmd=Proposal.model_validate(b['proposal']);validate_proposal(app,family,b,cmd)
        for item in cmd.items:
            key=digest([item.subject,item.topic.strip().casefold()])
            old=optional(app,'memory',item.memory_id,family) if item.memory_id else next((m for m in app.store.list('memory',family) if m.get('topic_key')==key and m.get('status')=='active'),None)
            refs=[app.store.get('message',r.message_id,family) for r in item.sources]
            latest=max(refs,key=lambda m:timestamp(m['occurred_at']))
            mid=old['id'] if old else 'daily:'+digest([family,key,b['id']])
            if item.change=='retain':
                kept=deepcopy(old)
                combined={r['id']:r for r in kept.get('source_refs',[])}
                combined.update({m['id']:{'id':m['id'],'digest':message_source_digest(m)} for m in refs})
                kept.update(source_refs=list(combined.values()),updated_at=app.clock.iso(),origin_batch=b['id'],
                            last_reaffirmed_at=latest['occurred_at'])
                app.store.put('memory',mid,family,kept,old['version']);continue
            app.store.put('memory',mid,family,{'body':item.body,'subject':item.subject,'topic':item.topic,'topic_key':key,
                'kind':item.kind,'status':'active','certainty':'interpretation' if item.kind=='service_interpretation' else 'user_reported',
                'source_message_id':latest['id'],'source_version':latest['version'],
                'source_refs':[{'id':m['id'],'digest':message_source_digest(m)} for m in refs],
                'source_excerpt':next(r.excerpt for r in item.sources if r.message_id==latest['id']),
                'occurred_at':latest['occurred_at'],'updated_at':app.clock.iso(),'origin_batch':b['id'],
                'valid_until':expiry(item.temporal_scope,item.valid_until,latest['occurred_at']),
                'temporal_scope':item.temporal_scope,'persistence_basis':item.persistence_basis,'event_id':None},old['version'] if old else None)
        for item in cmd.resolved_memories:
            old=app.store.get('memory',item.memory_id,family)
            old.update(status='resolved',resolved_at=app.clock.iso(),resolution_batch=b['id'],
                       resolution_sources=[r.model_dump() for r in item.sources])
            app.store.put('memory',old['id'],family,old,old['version'])
        s=state(app,family);covered=deepcopy(s.get('covered',{}))
        covered.update({str(r['seq']):r['digest'] for r in b['source_refs']})
        handoff={'body':cmd.handoff,'source_refs':[r for r in b['source_refs'] if r['id'] in cmd.handoff_source_ids],
            'task_ids':cmd.unresolved_task_ids,'business_day':b['business_day'],'batch_id':b['id']}
        s.update(revision=s['revision']+1,covered=covered,published_at=app.clock.iso(),handoff=handoff)
        s['memory_signature']=memory_signature(app,family)
        app.store.put('memory_state',family,family,s,s.get('version'))
        app.store.put('memory_diary',b['id'],family,{'handoff':handoff,'published_at':app.clock.iso(),'revision':s['revision']})
        b.update(status='published',published_revision=s['revision']);b.pop('proposal',None)
        app.store.put('memory_batch',b['id'],family,b,b['version']);app.store.bump(family)
        hook=getattr(app,'daily_memory_before_commit',None)
        if hook:hook()
        return {'duplicate':False,'revision':s['revision'],'covered_sources':len(b['source_refs'])}


def service_view(app,family):
    from .family_memory import deletion_receipts
    s=state(app,family);cov=coverage(app,family);h=s.get('handoff')
    if h and any(not optional(app,'message',r['id'],family) or message_source_digest(app.store.get('message',r['id'],family))!=r['digest'] for r in h['source_refs']):h=None
    active=[m for m in app.store.list('memory',family) if valid_memory(app,family,m)]
    signature=digest([memory_signature(app,family),[(m['id'],m['version']) for m in active],cov['uncovered_history_signature'],s['revision'],h])
    portrait=[];chars=0
    for m in sorted(active,key=lambda m:m['updated_at'],reverse=True):
        if len(portrait)>=10 or chars+len(m['body'])>3000:break
        portrait.append({'id':m['id'],'subject':m.get('subject','unknown'),'body':m['body'],'certainty':m['certainty']})
        chars+=len(m['body'])
    return {'coverage':cov,'revision':s['revision'],'validity_signature':signature,
        'handoff':h if cov['personalization_verified'] and s.get('memory_signature')==memory_signature(app,family) else None,
        'portrait':portrait if cov['personalization_verified'] else [],
        'active_count':len(active),'detail_tool':'memory.read',
        'deletion_receipts':deletion_receipts(app,family,today=True),
        'rule':'未整理历史存在时，旧画像只代表历史认知，使用偏好前先通过resource.read核对未覆盖聊天。记忆不能解除控制、结清任务或证明学习掌握。删除回执证明对应来源的删除已完成；当前没有有效记忆不等于从未保存，不得说此前未保存或重建已删内容。'}


def schedule(app):
    if getattr(app,'context_mode',None)!='daily_native':return 0
    from .jobs import JobQueue
    count=0
    with app.store.transaction() as db:
        for row in db.execute('SELECT id FROM families').fetchall():
            family=row['id'];s=state(app,family)
            rows=[r for r in source_rows(app,family) if s.get('covered',{}).get(str(r['seq']))!=r['digest'] and
                (r['day']<business_day(app.clock.now()) or app.clock.now().hour>=app.service_end)]
            if not rows:continue
            day=min(r['day'] for r in rows);b=freeze(app,family,day)
            if b and b['status'] not in {'published','superseded'}:
                count+=JobQueue(app).enqueue(family,'memory:'+b['id'],{'kind':'memory','memory_batch_id':b['id'],'memory_day':day})
    return count


def run_job(host,job,business):
    app=business.app
    with app.store.transaction():
        family,binding=app.auth(business.token,{ROLE})
        b=app.store.get('memory_batch',job['memory_batch_id'],family)
        if b['status']=='published':return {'run_id':binding['run_id'],'already_published':True}
        snapshot_current(app,family,b)
        b.update(attempts=b['attempts']+1)
        app.store.put('memory_batch',b['id'],family,b,b['version'])
    prompt={'recipe':ROLE,'run_id':binding['run_id'],'batch_id':b['id'],
            'business_day':b['business_day'],'source_count':len(b['source_refs']),
            'chunk_count':len(b['chunks']),'cutoff_seq':b['cutoff_seq']}
    from .workflow import enabled
    result=host.run(business,json.dumps(prompt,ensure_ascii=False),system=SYSTEM+(WORKFLOW_SYSTEM if enabled(app) else ''),options={'exclude_materials':True})
    host.last_result=result
    if not result.get('completed'):
        if result.get('error') in STALE_CODES:
            supersede(app,family,b['id'],result['error'])
            return {'superseded':True,'reason':result['error'],'run_id':result['run_id']}
        return {'retryable_error':result.get('error') or 'MEMORY_MODEL_INCOMPLETE','run_id':result['run_id']}
    try:
        publication=publish(app,family,b['id'],token=business.token)
    except BusinessError as exc:
        if exc.code not in STALE_CODES:raise
        supersede(app,family,b['id'],exc.code)
        return {'superseded':True,'reason':exc.code,'run_id':result['run_id']}
    return {'run_id':result['run_id'],'publication':publication}


SYSTEM='''你是家庭教辅日终记忆整理员。仅整理本批来源，不发送、不修改控制、不结清任务。
先 memory.read 核对已有记忆，has_more时携带next_cursor继续。memory.read_tasks核对任务与控制账本，再 memory.read_batch 从offset=0读取全部页，has_more时继续。不要把老师的建议当家长或孩子事实。
最后调用 memory.propose_daily 提交候选。保存明确偏好、学习习惯与阶段变化，区分parent/child/family/unknown；一次表现不固化成永久习惯。
同主题更正请使用已有memory_id，沿用subject和topic。topic用不含具体值的稳定属性名（如阅读时间、阅读载体），每项只记一个属性；时间变化不能丢掉未变化的阅读载体。body只写当前结论，不夹带此前旧值。旧发生时间的迟到内容不能覆盖较新事实。语义重复不要新增多份。
每项sources摘录必须来自本批入站原文。一次抱怨（例如这次太复杂了）只描述本次体验，用source_day；不得推广为长期沟通偏好。孩子转述的故事情节不是家庭事实。含糊错字不能推断孩子能力。明确长期偏好可persistent并填persistence_basis=explicit_general，必须原话已表达一般偏好或固定习惯；repeated_pattern须至少两个不同日期的本批证据；临时情况source_day或until；推测service_interpretation必须until且最多30天。
保留原话的否定和不确定程度：尚未决定、尚未同意、没有同意不等于拒绝或长期不接受；只记尚未确认的事实或交接，不造永久偏好。没有证明某能力不等于能力差。
相对日期以每条来源的source_calendar为准。本周是该来源日所在的周一至周日，不把已经过去的星期擅自滚到下周；有歧义保留待确认，不编造新的具体日期、永久安排或控制。已经过去的临时情况用source_day或不写有效记忆。
handoff简短说明服务连续性，附本批来源ID，任务状态由实时账本决定。可以提交空items，不要求每句话生成记忆。
suppressed_from_memory为删除抑制，不可重建。全部工具成功才结束；失败保留原记忆，不能声称已发布。'''

WORKFLOW_SYSTEM='''
本轮启用增量记忆更新协议。必须分页memory.read读取所有有效旧记忆。已有记忆是已校验的历史依据，允许与本批新来源结合，不要求本批重复陈述所有旧值。
新事实写items；旧属性确实改变时写memory_id、expected_version、change=replace，只替换改变的那个属性。家长说载体/时长不变时用change=retain（body可照抄旧值，宿主保留旧正文及证据链）；不能把已知具体值改写成未说明/待确认。未被提及的旧属性省略，宿主自动保留。
读取当天全部来源后逐项核对旧的遗留问题。已补题、明确取消或已结束的记忆放入resolved_memories，带memory_id、expected_version和本批新入站原文依据；保留原记录以供审计，但不再作为当前遗留问题。此操作不改变任务或控制状态，不能用记忆结清替代实际业务回执。仅时间/时长等个人属性不要当作任务结清。
items和resolved_memories不能引用同一记忆两次。原始来源、旧记忆版本、删除抑制任一变化，整批作废重新读取，不能偷用上一轮读取凭据。
'''

SPECS={'memory.read_batch':({ROLE},BatchRead,'按offset分页读取宿主冻结的当日来源；每页最多32块且正文不超过12000字符，has_more时继续。'),
       'memory.read_tasks':({ROLE},TaskRead,'分页读取本批任务和控制账本，只可引用真实任务ID，不改变任务状态。'),
       'memory.propose_daily':({ROLE},Proposal,'暂存完整日终候选。先读完整批次，不直接生效；宿主校验并原子发布。')}
HANDLERS={'memory.read_batch':read_batch,'memory.read_tasks':read_tasks,'memory.propose_daily':propose}
