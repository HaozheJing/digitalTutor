"""Additive, conservative model-facing evidence labels; no business-state writes."""
import copy

GUIDANCE = '''工具结果中的 result_evidence 分别说明资料获取、业务审核、操作回执和事项完成范围。
found/read 只表示找到了资料或读到了正文；verified 仅覆盖明确标明已审核的资料及适用范围，不代表答案一定适用于当前家庭。
accepted/queued 只表示操作受理或排队，sent 表示通道报告发送成功，不证明对方已读，也不等于退款、问题或人工处理已解决。
completed 只覆盖 completion.scope 指定的事件或任务；no_contact、blocked、unknown、partial 不可说成服务已办妥。
human_confirmation_required / pending_confirmation 只表示需要或等待人工，不是人工已接单或退款已办理。原始字段和回执仍是依据，缺少状态不能猜。
'''

KNOWLEDGE = {'knowledge_browse', 'knowledge_search', 'knowledge_read', 'course_search', 'course_browse', 'course_read', 'course_knowledge'}
DELIVERY = {'send_message', 'reply_to_message', 'reply_with_highlights', 'deliver_message', 'deliver_proactive', 'deliver_festival', 'send_image', 'workflow_highlight', 'workflow_fulfill'}
FINISH = {'finish_event', 'service_task_finish'}


def review(item):
    # Hash equality and successful reads prove integrity, NOT business review.
    if item.get('status') in {'unavailable', 'changed', 'unknown'}:
        return 'unknown'
    if item.get('business_review_status') == 'unverified' or item.get('can_answer_directly') is False:
        return 'unverified'
    if item.get('business_review_status') == 'verified':
        return 'verified'
    return 'unknown'


def annotate(name, args, value):
    if not isinstance(value, dict) or name not in KNOWLEDGE | DELIVERY | FINISH | {'request_human_confirmation','workflow_commit','completion_plan_commit'}:
        return value
    result = copy.deepcopy(value)
    evidence = {'version': 1}
    status = value.get('status')
    if name in KNOWLEDGE and isinstance(value.get('parts'),dict):
        parts=value['parts']
        states={k:('read' if item.get('status')=='ok' and isinstance(item.get('body'),str) and item['body'] else item.get('status','unknown')) for k,item in parts.items()}
        read_count=sum(x=='read' for x in states.values())
        state='read' if states and read_count==len(states) else 'partial' if read_count else status or 'unknown'
        evidence['retrieval']={'state':state,'parts':states}
        evidence['verification']={'state':'unknown','scope':'retrieved_material_only','parts':{k:review(v) for k,v in parts.items()},'note':'正文读取和版本一致不等于业务审核；审核状态逐项沿用来源。'}
        result['result_evidence']=evidence
        return result
    if name=='knowledge_browse':
        result['result_evidence']={'version':1,'retrieval':{'state':'catalog' if status=='catalog' else status or 'unknown'},'verification':{'state':'not_applicable','scope':'catalog_metadata_only'}}
        return result
    if name in KNOWLEDGE:
        matches = value.get('matches', value.get('children'))
        if status in {'unavailable','changed','unknown','no_match','suppressed'}:
            retrieval = status
        elif isinstance(matches, list):
            retrieval = 'found' if matches else 'no_match'
        elif isinstance(value.get('body'), str) and value['body']:
            retrieval = 'read'
        else:
            retrieval = 'unknown'
        emoji = name == 'knowledge_search' and args.get('scope') == 'emoji'
        evidence['retrieval'] = {'state': retrieval}
        if isinstance(matches, list): evidence['retrieval']['count'] = len(matches)
        if emoji:
            evidence['verification'] = {'state':'not_applicable','scope':'emoji_usage_only',
                'note':'素材候选可用性以原 selection_authorized_for_current_turn 和 policy 为准，不证明业务答案。'}
        else:
            items = matches if isinstance(matches, list) else [value]
            states = [review(item) for item in items if isinstance(item, dict)]
            state = states[0] if states and len(set(states)) == 1 else 'mixed' if states else 'unknown'
            if retrieval in {'unavailable','changed','unknown','no_match','suppressed'}:state='unknown'
            evidence['verification'] = {'state':state,'scope':'retrieved_material_only',
                'note':'审核状态沿用源记录；检索相似度、正文可读和哈希一致均不代表业务核实。'}
            if isinstance(matches,list):
                field = 'matches' if 'matches' in value else 'children'
                for item in result[field]:
                    if isinstance(item,dict):item['material_verification']={'state':review(item),'scope':'this_material_only'}
    elif name in DELIVERY:
        evidence['operation'] = {'state':status or 'unknown','scope':'message_delivery'}
        evidence['completion'] = {'state':'not_established','scope':'business_issue',
            'note':'本工具只报告消息交付；accepted 尚未确认发送成功，sent 也不证明对方已读或业务问题解决。'}
    elif name in FINISH:
        outcome = value.get('outcome')
        state = ('completed' if outcome == 'completed' else outcome or 'unknown') if name == 'service_task_finish' else ('completed' if status == 'done' else status or 'unknown')
        # Never promote an incomplete transport/task status because outcome says completed.
        if status != 'done':state=status or 'unknown'
        evidence['operation'] = {'state':status or 'unknown','scope':'finish_record'}
        evidence['completion'] = {'state':state,'scope':'current_message_event' if name=='finish_event' else 'current_service_task',
            'note':'仅表示这个事件或任务的收尾结果，不证明退款完成、人工受理或家长疑虑解决。'}
    elif name == 'request_human_confirmation':
        evidence['operation'] = {'state':status or 'unknown','scope':'operator_alert'}
        evidence['completion'] = {'state':'not_established','scope':'human_resolution',
            'note':'提醒已建立/待确认不等于真人已接单；以人工后续记录为准。'}
    else:
        evidence['operation'] = {'state':status or 'unknown','scope':'plan_update'}
        evidence['completion'] = {'state':'not_established','scope':'service_execution',
            'note':'计划提交回执不代表计划中的服务已经执行。'}
    result['result_evidence'] = evidence
    return result


if __name__ == '__main__':
    import json
    print(json.dumps({'guidance': GUIDANCE}, ensure_ascii=False))
