"""Compile model-selected reply spans; core permissions and evidence stay unchanged."""
from copy import deepcopy
from .domain import require


def send_schema(schema):
    out = deepcopy(schema)
    props = out['properties']
    original_body=props.pop('body')
    props['body_segments'] = {'type':'array','minItems':1,'maxItems':100,
        'items':{'type':'string','minLength':1},
        'description':original_body.get('description','')+' 按发送顺序分段，直接无分隔拼接成完整正文，总计最多10000字符；所需换行须写在片段内。每个reply_to用response_segment_index引用实际回应它的片段。不要在同一片段混合无关意图。'}
    out['required'] = ['body_segments' if k == 'body' else k for k in out['required']]
    reply = out['$defs']['ReplyRef']
    original_excerpt=reply['properties'].pop('response_excerpt')
    reply['properties']['response_segment_index'] = {'type':'integer','minimum':0,
        'description':original_excerpt.get('description','')+' 改用从0开始的body_segments索引；该片段须实际回应本条message_id/intent_id。未解决的仍填resolves_intent=false；仅背景参考放context_refs。'}
    reply['required'] = [*reply.get('required',[]),'response_segment_index']
    return out


def compile_send(args):
    args = deepcopy(args)
    require('body' not in args, 'REPLY_SEGMENTS_INVALID', '只提供body_segments，禁止同时提供body')
    segments = args.pop('body_segments',None)
    require(isinstance(segments,list) and 1 <= len(segments) <= 100
            and all(isinstance(s,str) and len(s)>0 for s in segments),
            'REPLY_SEGMENTS_INVALID','正文片段必须是1到100个非空字符串')
    body = ''.join(segments)
    require(len(body)<=10000,'REPLY_SEGMENTS_INVALID','拼接正文不可超过10000字符')
    refs = args.get('reply_to',[])
    require(isinstance(refs,list),'REPLY_SEGMENTS_INVALID','reply_to必须是列表')
    for ref in refs:
        require(isinstance(ref,dict) and 'response_excerpt' not in ref,
                'REPLY_SEGMENTS_INVALID','使用片段编号，不接受另写response_excerpt')
        index=ref.pop('response_segment_index',None)
        require(type(index) is int and 0 <= index < len(segments),
                'REPLY_SEGMENTS_INVALID','回应片段编号不存在')
        ref['response_excerpt']=segments[index]
    args['body']=body
    return args
