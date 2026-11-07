"""Append-only repair before a source has any durable business effects."""
from .domain import require

def source_locked(app,family,message):
    mid=message['id']
    if any(i.get('reply_operation_refs') or i.get('partial_reply_operation_refs') for i in message['intents']):return True
    for kind in ('pending','control','candidate_source'):
        if any(r.get('source_message_id')==mid for r in app.store.list(kind,family)):return True
    if any(mid in o.get('context_refs',[]) or any(r.get('message_id')==mid for r in o.get('reply_to',[])) for o in app.store.list('operation',family)):return True
    if any(any(r.get('kind')=='message' and r.get('id')==mid for r in report.get('payload',{}).get('evidence_refs',[])) for report in app.store.list('report',family)):return True
    return False

def validate_extension(app,family,message,intents):
    require(not source_locked(app,family,message),'INTERPRETATION_SOURCE_LOCKED','此来源已有业务效果，不能补解释而改变既有来源合同')
    existing={i['intent_id'] for i in message['intents']}
    texts={i['text'] for i in message['intents']}
    require(all(i.intent_id not in existing and i.text not in texts for i in intents),
            'ALREADY_INTERPRETED','只追加新的独立意图，不能重传或改写已有意图')
    require(all(i.source_excerpt and i.source_excerpt.strip() and i.source_excerpt in message['body'] for i in intents),
            'INTENT_SOURCE_REQUIRED','补意图必须引用同一原消息的明确原文')
