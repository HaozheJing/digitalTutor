"""Experimental lossless sharing across JSON messages in ONE actual API request.

Roles, tool call IDs and message ordering never change. Definitions are carried
by a data-role message, never system. No references survive across requests.
"""
import json
from copy import deepcopy
from .context_transport import compact,expand,dumps

FORMAT='digital-tutor-api-shared-v1'

def share(payload):
    messages=payload.get('messages',[]);indices=[];values=[]
    for i,m in enumerate(messages):
        if m.get('role') not in {'user','tool'} or not isinstance(m.get('content'),str):continue
        try:v=expand(json.loads(m['content']))
        except (ValueError,KeyError,TypeError):continue
        if not isinstance(v,dict):continue
        if not (v.get('family_id') or 'ok' in v or 'dialogue_context' in v):continue
        indices.append(i);values.append(v)
    if len(values)<2:return payload,{'shared':False}
    combined=compact({'entries':values})
    if combined.get('format')!='digital-tutor-context-v1':return payload,{'shared':False}
    encoded=combined['payload'].get('entries')
    if not isinstance(encoded,list):return payload,{'shared':False}
    out=deepcopy(payload)
    for ordinal,index in enumerate(indices):
        item={'format':FORMAT,'entry':encoded[ordinal]}
        if ordinal==0:
            item['reading_rule']=combined['reading_rule']+' definitions 仅供这一次API请求共用。后续 user/tool 消息的 entry 均引用本表；每条entry的角色、工具身份和时间顺序仍由原消息位置确定。'
            item['definitions']=combined['definitions']
        out['messages'][index]['content']=dumps(item)
    # Verify every data message against its original source, not the encoder.
    for ordinal,index in enumerate(indices):
        item=json.loads(out['messages'][index]['content'])
        restored=expand({'format':'digital-tutor-context-v1','definitions':combined['definitions'],'payload':item['entry']})
        if restored!=values[ordinal]:raise ValueError('WIRE_CONTEXT_ROUNDTRIP_FAILED')
    before=len(dumps(payload));after=len(dumps(out))
    if after>=before:return payload,{'shared':False,'candidate_chars':after,'original_chars':before}
    return out,{'shared':True,'entries':len(indices),'definitions':len(combined['definitions']),
                'original_chars':before,'model_chars':after,'roundtrip_equal':True}
