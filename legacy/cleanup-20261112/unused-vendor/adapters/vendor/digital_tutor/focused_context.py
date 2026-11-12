"""Bound optional history, pin required sources, expose an explicit full-read escape."""
from copy import deepcopy
from .context_transport import fingerprint

def select_ids(rows,required,tail=40):
    keep={r.get('message_id',r.get('id')) for r in rows[-tail:]}|set(required)
    # Resolve a short current answer against its immediate preceding question.
    for i,r in enumerate(rows):
        if r.get('message_id',r.get('id')) in required:
            keep.update(x.get('message_id',x.get('id')) for x in rows[max(0,i-2):i])
    return keep

def context_view(original):
    result=deepcopy(original);d=result.get('dialogue_context')
    if not isinstance(d,dict):return result
    rows=d.get('transcript',[])
    required={r['message_id'] for r in d.get('current_inputs',[])}
    required.update(r['id'] for r in result.get('coverage',{}).get('unanswered_message_refs',[]))
    required.update(r['message_id'] for r in d.get('awaiting_clarifications',[]))
    required.update(r['message_id'] for r in d.get('preceding_outbound',[])[-4:])
    keep=select_ids(rows,required)
    selected=[r for r in rows if r['message_id'] in keep]
    for key in ('current_inputs','awaiting_clarifications'):assert d.get(key)==original['dialogue_context'].get(key)
    d['transcript']=selected
    d['preceding_outbound']=[r for r in d.get('preceding_outbound',[]) if r['message_id'] in keep]
    groups=[]
    for g in d.get('utterance_groups',[]):
        pairs=[(mid,body) for mid,body in zip(g['message_ids'],g['parts']) if mid in keep]
        if not pairs:continue
        by_id={r['message_id']:r for r in selected}
        # Preserve actual retained boundaries; do not imply omitted adjacency.
        groups.append({**g,'message_ids':[p[0] for p in pairs],'parts':[p[1] for p in pairs],
                       'first_at':by_id[pairs[0][0]]['occurred_at'],'last_at':by_id[pairs[-1][0]]['occurred_at'],
                       'has_omitted_parts':len(pairs)!=len(g['parts'])})
    d['utterance_groups']=groups
    coverage={**d.get('transcript_coverage',{}),'included_messages':len(selected),'complete':False,
        'model_projection':'required_sources_and_recent_history','omitted_from_original_view':len(rows)-len(selected),
        'source_view_sha256':fingerprint(rows),'body_characters':sum(len(r['body']) for r in selected),
        'fetch_older':'wechat.read_all_messages(context_view="full") returns all visible history; default returns focused history.',
        'omission_is_not_absence':True}
    coverage['complete']=coverage.get('visible_messages',len(rows))==len(selected)
    coverage['older_omitted']=coverage.get('visible_messages',len(rows))-len(selected)
    d['transcript_coverage']=coverage
    if 'coverage' in result:result['coverage']['conversation_transcript']=deepcopy(coverage)
    d['contract']+=' 当前transcript是明确覆盖范围的工作视图。若问题指向更早信息、原文不足或引用不清，调用wechat.read_all_messages并设置context_view为full补读。未显示不等于没有。'
    return result

def tool_view(name,result,full=False):
    if full or not result.get('ok'):return result
    if name=='context.read':
        return {**result,'result':context_view(result['result'])}
    if name not in {'wechat.read_all_messages','wechat.read_message_stream'}:return result
    value=result.get('result',{});rows=value.get('messages')
    if not isinstance(rows,list) or len(rows)<=40:return result
    # Pin every actual unresolved inbound, even if it precedes the cursor window.
    required={m['id'] for m in rows if m.get('direction')=='inbound' and not m.get('historical_reference') and
        (not m.get('interpreted') or any(i.get('requires_reply',True) and not i.get('reply_operation_refs') for i in m.get('intents',[])))}
    keep=select_ids(rows,required);selected=[m for m in rows if m['id'] in keep]
    if len(selected)==len(rows):return result
    from .conversation_context import utterance_groups
    out=deepcopy(result);v=out['result'];v['messages']=selected
    if 'utterance_groups' in v:v['utterance_groups']=utterance_groups(selected)
    v['returned_count']=len(selected);v['has_older']=True;v['complete_snapshot']=False
    v['model_history_coverage']={'original_read_count':len(rows),'shown_count':len(selected),
        'omitted_count':len(rows)-len(selected),'verified_source_sha256':fingerprint(rows),
        'business_read_executed':True,'read_is_not_answered':True,'full_read_available':True,
        'instruction':'为减少重复历史只展示必要来源与近期原文。需要更早原文请以context_view="full"再次调用同一读取工具。原游标属于真实读取进度，不代表模型已理解全部历史。'}
    return out
