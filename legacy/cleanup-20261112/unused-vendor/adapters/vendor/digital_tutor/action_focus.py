"""Explicit task semantics for inherited service plans; never grants send authority."""
def missing_invitation_step(app, family, event):
    if event.get('service_action_id') not in {'I01','I02','I03','I04'} or event['status'] in {'done','cancelled'}:
        return False
    from .action_completion import _contract
    c=_contract(app,family,event)
    if c.get('invitation_closed'):return False
    slot={'I01':'invitation','I02':'questions','I03':'demonstration','I04':'life_question'}[event['service_action_id']]
    part=c['slot_parts'].get(slot)
    sent=any(o.get('event_id')==event['id'] and o.get('requirement_revision')==event['requirement_revision']
             and o.get('part_id')==part and o.get('stage') in {'sent','delivered','read'}
             for o in app.store.list('operation',family))
    return not sent and part not in (event.get('next_action') or {}).get('target_refs',[])

def project(app, family, events, binding):
    from .action_completion import evaluate,_contract
    result=[]
    for e in events:
        if not e.get('service_action_id'):continue
        c=_contract(app,family,e);fact=evaluate(app,family,e,c)
        item={'event_id':e['id'],'action':e['service_action_id'],'fact':fact['fact'],
              'needs_judgment':fact['needs_judgment'],'remaining':fact['remaining'],
              'bound_task':binding.get('event_id')==e['id'],
              'rule':'只处理本任务绑定事项；其他阅读答疑未结清不阻塞本事项内部核验。当前实际操作优先于旧提示中的已发送描述。'}
        if e['service_action_id'] in {'S02','S04'}:
            item['course_date_context']={'original_period':(e.get('source_identity') or {}).get('period_start'),
                'now':app.clock.iso(),'rule':'按北京时间判断原课日；跨日补发用原日期或那一课，不把昨天的课程说成今天完成。'}
            item.update(child_message_meaning='老师直接对孩子说的鼓励，不是孩子来信；不等待孩子回答任何问题。',
                        execution='先逐项判断 needs_judgment 中已发操作的真实正文，用 service.record_assessment 的 delivery_content。已有内容覆盖则核验收尾；缺失才在获授权的步骤中直接对孩子补齐，同一条新鼓励可分别判断覆盖 praise/encouragement 与 child_message。历史已发内容按原事项的 completion_condition 判断，不因新话术偏好否定旧事实；从现在开始的鼓励直接对孩子说。旧描述要求等待孩子表达的句子不适用这两个交付型动作。')
        if missing_invitation_step(app,family,e):
            item['plan_repair_required']='没有本事项的开场交付回执，旧计划却已跳到回应阶段。用 progress.submit 引用当前事项提交事实，建议原事项恢复开场步骤（I01 invitation、I02 questions、I03 demonstration、I04 life_question）并保留原约定时刻；不得把另一条普通答疑当成本事项邀请，不新增重复事项。'
        if binding.get('job_kind')=='review':
            item['review_only']='本轮禁止外发，但应完成已列出的内部内容判断或计划纠错报告。禁止外发不代表不能判断已有正文。'
        result.append(item)
    return result
