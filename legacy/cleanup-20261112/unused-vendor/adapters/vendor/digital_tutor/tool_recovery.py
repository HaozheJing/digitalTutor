"""Bounded version diagnostics; never rebase a write or grant a capability."""
from .domain import BusinessError, timestamp

def source_hint(app,token,args):
    try:
        family,binding=app.auth(token,{'service'})
        mid=args.get('source_message_id') or args.get('message_id')
        if mid not in binding.get('reply_scope',{}):return None
        row=app.store.get('message',mid,family)
        if row['direction']!='inbound' or row.get('historical_reference') or timestamp(row['occurred_at'])>app.clock.now():return None
        from .interpretation_extension import source_locked
        return {'message_id':mid,'source_version':row['version'],'source_excerpt':row['body'],
            'interpreted':row['interpreted'],'existing_intent_ids':[i['intent_id'] for i in row['intents']],
            'can_append_new_intents':row['interpreted'] and not source_locked(app,family,row),
            'contract':'这里只返回本轮已授权原消息数据，不替代微信读取、不解释或授权发送。未来需求工具source_excerpt复制整条原文；同条遗漏新意图须先append_new_intents=true、只传新ID，保留已有意图；已有业务效果不能改解释。不能因参数拒绝把明确未来交付退成无规划来源的普通pending。'}
    except BusinessError:return None


def version_hint(app, token, args):
    try:
        family,binding=app.auth(token,{'service','planner_daily','planner_progress','admin'})
    except BusinessError:
        return None
    keys=set()
    for field,kind in (('message_id','message'),('source_message_id','message'),
                       ('memory_id','memory'),('operation_id','operation'),('event_id','event')):
        if isinstance(args.get(field),str):keys.add((kind,args[field]))
    for field in ('refs','reply_to','evidence_refs'):
        for ref in args.get(field,[]) if isinstance(args.get(field),list) else []:
            if not isinstance(ref,dict):continue
            if isinstance(ref.get('message_id'),str):keys.add(('message',ref['message_id']))
            if isinstance(ref.get('id'),str) and ref.get('kind') in {'message','event','report','operation','control','pending','memory','evidence'}:
                keys.add((ref['kind'],ref['id']))
    refs=[]
    for kind,rid in sorted(keys)[:20]:
        try:
            row=app.store.get(kind,rid,family)
            refs.append({'kind':kind,'id':rid,'version':row['version']})
        except BusinessError:
            continue  # Never disclose whether an ID exists in another family.
    hint={'family_input_version':app.store.family(family)['version'], 'current_records':refs,
            'contract':'家庭版本用于expected_family_version；message等记录版本用于对应expected_version/source_version/refs.version。版本提示不替代重新核对变化，不自动重试发送。'}
    requested={rid for kind,rid in keys if kind=='message'}
    resolved={r['id'] for r in refs if r['kind']=='message'}
    if requested-resolved and binding.get('role')=='service':
        candidates=[]
        for rid in binding.get('reply_scope',{}):
            try:row=app.store.get('message',rid,family)
            except BusinessError:continue
            if row['direction']!='inbound' or row.get('historical_reference'):continue
            if timestamp(row['occurred_at'])>app.clock.now() or timestamp(row.get('received_at',row['occurred_at']))>app.clock.now():continue
            candidates.append({'message_id':row['id'],'version':row['version'],
                'source_id_for_audit_only':(row.get('source_metadata') or {}).get('source_id',row.get('external_id'))})
        hint['reply_message_refs']=candidates[:20]
        hint['reply_message_refs_coverage']={'available':len(candidates),'included':min(len(candidates),20),
            'complete':len(candidates)<=20}
        hint['identity_contract']='message_id复制本轮微信读取回执的id；request_id、run_id、external_id和source_id不是消息记录id。此处仅列本轮授权来源，不表示已读取、已解释或可重发。必要时重新全量读取核对。'
    return hint
