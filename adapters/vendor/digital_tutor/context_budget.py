"""Per-request source protection and conservative, explicit capacity accounting."""
from copy import deepcopy
from math import ceil
import json
from .context_transport import dumps, expand, fingerprint, render
from .daily_context import FORMAT
from .domain import require

POLICY={'version':1,'request_tokens':128000,'output_reserve':12000,'estimate_margin':1.2,
        'automatic_source_repairs':1,'index_limit':20,
        'normal_capacity_contract':'estimation_with_margin; provider_usage_measured_separately'}


def estimated_tokens(value):
    s=dumps(value)
    return ceil((sum(ord(c)<128 for c in s)/4 + sum(ord(c)>=128 for c in s))*POLICY['estimate_margin'])


def decoded(message):
    try:
        value=expand(json.loads(message.get('content','')))
        return value if isinstance(value,dict) and value.get('format')==FORMAT else None
    except (ValueError,TypeError,KeyError):return None


class RequestGuard:
    def __init__(self,context,check_current=None):
        self.check_current=check_current
        self.context=deepcopy(context)
        self.source_hash=fingerprint(context['sources'])
        self.protected={k:deepcopy(context[k]) for k in ('format','business_day','sources','source_contract',
            'run_contract','family_version','controls','operations','events','evidence',
            'task_continuation','response_responsibilities','daily_memory','action_execution_focus','historical_reference_context','conversation_identity','human_help','first_lesson_check_policy') if k in context}

    def valid(self,value):
        return isinstance(value,dict) and all(value.get(k)==v for k,v in self.protected.items())

    def protect(self,messages):
        """Executed after native compression and again on actual HTTP bytes."""
        values=[(i,decoded(m)) for i,m in enumerate(messages) if m.get('role')=='user']
        matched=[i for i,v in values if self.valid(v)]
        if len(matched)==1 and sum(v is not None for _,v in values)==1:
            return messages,False
        out=deepcopy(messages)
        candidates=[i for i,v in values if v is not None]
        if candidates:
            current=decoded(out[candidates[-1]])
            out[candidates[-1]]['content']=render({**current,**self.protected},'daily_native')
            for i in candidates[:-1]:out[i]['content']='Previous host snapshot superseded by the current daily envelope.'
        else:
            # Before the execution transcript keeps tool-call/result adjacency intact.
            index=next((i for i,m in enumerate(out) if m.get('role')!='system'),len(out))
            out.insert(index,{'role':'user','content':render({**self.protected,'rule':'body_ref引用sources中的同ID原文；本轮状态覆盖旧状态。'},'daily_native')})
        require(sum(self.valid(decoded(m)) for m in out)==1,'CONTEXT_WIRE_MISSING','必要日聊天无法恢复')
        return out,True

    def wire(self,payload):
        if self.check_current:self.check_current()
        messages,repaired=self.protect(payload.get('messages',[]))
        value={**payload,'messages':messages}
        estimate=estimated_tokens({'messages':messages,'tools':value.get('tools',[])})
        require(estimate+POLICY['output_reserve']<=POLICY['request_tokens'],
                'CONTEXT_REQUIRED_OVER_BUDGET','必要日聊天超预算；保存责任并使用显式分段，不静默裁剪')
        return value,{'sources':len(self.context['sources']),'source_sha256':self.source_hash,
            'source_coverage':1.0,'repaired':repaired,'estimated_input_tokens':estimate,
            'output_reserve':POLICY['output_reserve'],'request_budget':POLICY['request_tokens']}

    def install_compressor(self,agent):
        """Narrow, instance-local adapter around the pinned native compressor."""
        compressor=agent.context_compressor
        original=compressor.compress
        def compress(messages,*args,**kwargs):
            result=original(messages,*args,**kwargs)
            protected,_=self.protect(result)
            return protected
        compressor.compress=compress


def segment(context, fixed_input_tokens=0):
    """Explicit exception for an oversized day; references remain recoverable.

    Current reply sources must fit intact. Other daily chat is indexed and can be
    read through resource.read. This does not mark any responsibility completed.
    """
    budget=POLICY['request_tokens']-POLICY['output_reserve']-fixed_input_tokens
    if estimated_tokens(context)<=budget:return context
    # Leave space for the explicit segment marker and archive references.
    budget-=1000
    out=deepcopy(context);all_sources=out['sources']
    required=set(out.get('run_contract',{}).get('reply_scope') or {})
    current={k:v for k,v in all_sources.items() if k in required}
    out['sources']=current
    # Do not silently split a question or omit part of a compound current input.
    require(estimated_tokens(out)<=budget,'CONTEXT_CURRENT_TASK_OVER_BUDGET',
            '当前授权问题本身超预算，保留任务并等待显式拆分任务范围')
    for key,value in reversed(list(all_sources.items())):
        if key in current:continue
        current[key]=value
        if estimated_tokens(out)>budget:
            current.pop(key)
            break
    omitted=set(all_sources)-set(current)
    def references(v):
        if isinstance(v,dict):
            if set(v)=={'body_ref'} and v['body_ref'] in omitted:return {'archive_message_id':v['body_ref'],'read_tool':'resource.read'}
            return {k:references(x) for k,x in v.items()}
        if isinstance(v,list):return [references(x) for x in v]
        return v
    out=references(out)
    out['source_contract'].update(source_ids=list(current),sha256=fingerprint(current))
    out['dialogue_context']['transcript_coverage']['daily_complete']=False
    out['coverage']['conversation_transcript']['daily_complete']=False
    out['segmentation']={'explicit':True,'day_source_count':len(all_sources),'included':len(current),
        'archived_count':len(omitted),'all_sources_sha256':fingerprint(all_sources),
        'read_tool':'resource.read','business_day':context['business_day'],
        'rule':'本次超长分段不是全天完整上下文；按日期分页读回，未处理来源仍保留责任。'}
    require(estimated_tokens(out)<=budget+1000,'CONTEXT_REQUIRED_OVER_BUDGET','分段元数据仍超预算，保留任务')
    return out
