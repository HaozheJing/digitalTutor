"""As-of-time transcript and utterance views; no inferred speaker permissions."""
from .domain import timestamp


def anchor(m):
    metadata=m.get('source_metadata') or {}
    return {'message_id':m['id'],'version':m['version'],'occurred_at':m['occurred_at'],
        'direction':m['direction'],'body':m['body'],
        'speaker_role':metadata.get('speaker_role','unknown'),
        'historical_reference':m.get('historical_reference',False),
        'actual_agent_delivery':m.get('actual_agent_delivery',not m.get('historical_reference',False)
            and m['direction']=='outbound'),
        'source_id':metadata.get('source_id',m.get('external_id')),
        'transcript':metadata.get('transcript')}


def utterance_groups(messages):
    groups=[]
    for m in messages:
        a=anchor(m)
        if groups:
            old=groups[-1]
            delta=(timestamp(a['occurred_at'])-timestamp(old['last_at'])).total_seconds()
            same=(old['direction']==a['direction'] and old['speaker_role']==a['speaker_role']
                and old['historical_reference']==a['historical_reference'] and 0<=delta<=120)
        else:same=False
        if not same:
            groups.append({'direction':a['direction'],'speaker_role':a['speaker_role'],
                'historical_reference':a['historical_reference'],'first_at':a['occurred_at'],
                'last_at':a['occurred_at'],'message_ids':[],'parts':[]})
        groups[-1]['message_ids'].append(a['message_id'])
        groups[-1]['parts'].append(a['body']);groups[-1]['last_at']=a['occurred_at']
    return groups


def dialogue_context(app, family, messages, binding):
    now=app.clock.now()
    visible=[m for m in messages if timestamp(m['occurred_at'])<=now
        and timestamp(m.get('received_at',m['occurred_at']))<=now]
    inbound=[m for m in visible if m['direction']=='inbound' and not m.get('historical_reference')]
    scope=set(binding.get('reply_scope',{}))
    current=[m for m in inbound if m['id'] in scope]
    selected=[];chars=0
    for m in reversed(visible):
        if len(selected)>=300 or chars+len(m['body'])>48000:break
        selected.append(m);chars+=len(m['body'])
    selected.reverse()
    required={m['id'] for m in inbound if m['id'] in scope or not m.get('interpreted') or
        any(i.get('requires_reply',True) and not i.get('reply_operation_refs') for i in m.get('intents',[]))}
    keep={m['id'] for m in selected}|required
    selected=[m for m in visible if m['id'] in keep]
    chars=sum(len(m['body']) for m in selected)

    operations={o['id']:o for o in app.store.list('operation',family)}
    waiting=[]
    for m in inbound:
        for intent in m.get('intents',[]):
            if not intent.get('requires_reply',True) or intent.get('reply_operation_refs'):continue
            asked=[operations[r] for r in intent.get('partial_reply_operation_refs',[])
                if r in operations and operations[r]['stage'] in {'sent','delivered'}]
            if asked:
                waiting.append({'message_id':m['id'],'intent_id':intent['intent_id'],'source_body':m['body'],
                    'already_asked':True,'last_question_body':asked[-1]['body'],
                    'last_question_at':asked[-1]['occurred_at'],
                    'contract':'保留原欠项；新的纯确认/表情不等于补充答案，不借其重复旧澄清。'})
    return {'current_inputs':[anchor(m) for m in current],
        'transcript':[anchor(m) for m in selected],
        'transcript_coverage':{'visible_messages':len(visible),'included_messages':len(selected),
            'complete':len(selected)==len(visible),'older_omitted':len(visible)-len(selected),
            'future_excluded':len(messages)-len(visible),'body_characters':chars,
            'required_messages':len(required),'required_pinned_over_budget':len(selected)>300 or chars>48000,
            'max_messages':300,'max_body_characters':48000,'as_of':app.clock.iso()},
        'utterance_groups':utterance_groups(selected),'awaiting_clarifications':waiting,
        'preceding_outbound':[anchor(m) for m in visible if m['direction']=='outbound'][-20:],
        'latest_inbound_at':max((m['occurred_at'] for m in inbound),key=timestamp,default=None),
        'contract':'先读完整transcript和连续发言，再把当前短句接到此前问题上。可结合前文推测口语/转写的本意，不把重复字、同音字、标点当孩子书写或语法错误；确实影响含义才澄清。unknown保持unknown，不固定当家长。教师历史是当时话语，不是智能体发送或可信学情。上下文扩展不扩大reply_scope；分组不合并责任身份。旧请假不证明现在未听课。',
        'task_context_source':'visible_messages_only_no_hidden_teacher_prompt'}
