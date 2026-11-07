"""One bounded message capability; original transports and guards own effects."""
import re
import agent_plan as ap,business,sql_tools as s

REPLACED={'deliver_message','deliver_proactive','reply_to_message','send_image'}

def enabled():
    return __import__('capability_surface').enabled() and __import__('tool_surface').enabled('capabilities_delivery')

def route(args):
    if not isinstance(args,dict) or set(args)-{'body','candidate_ids','asset_id','highlight_ids','execution_token','generation_token','emoji_refs'}:
        raise ap.ServiceValidationError('invalid_message_arguments')
    if not isinstance(args.get('body'),str) or not args['body'].strip():raise ap.ServiceValidationError('message_body_required')
    if re.search(r'(?i)(?:generation_token|execution_token)\s*[:=]',args['body']) or any(isinstance(args.get(k),str) and len(args[k])>=8 and args[k] in args['body'] for k in ('generation_token','execution_token')):
        raise ap.ServiceValidationError('internal_authority_in_body_remove_it_and_retry')
    common={k:args[k] for k in ('body','execution_token','generation_token','emoji_refs') if k in args}
    task=ap.active()
    if task:
        if task.get('kind') in __import__('service_workflow').INTERNAL:raise ap.ServiceValidationError('internal_task_cannot_send')
        if task.get('kind')=='highlight':raise ap.ServiceValidationError('highlight_original_delivery_is_host_owned')
        if args.get('highlight_ids'):raise ap.ServiceValidationError('highlight_attachment_requires_passive_reply')
        if task.get('kind')=='s01':
            if args.get('asset_id') or args.get('candidate_ids'):raise ap.ServiceValidationError('s01_media_and_lesson_are_host_bound')
            return 'deliver_message',{**common,'day':task['day']}
        ids=args.get('candidate_ids') or ([business.token(task['candidate_id'])] if task.get('candidate_id') else [])
        if not isinstance(ids,list) or not 1<=len(ids)<=3 or any(not isinstance(i,str) or not i for i in ids):raise ap.ServiceValidationError('use_candidate_ids_from_current_task_material')
        if ap.festival_task(task):
            if len(ids)!=1 or not args.get('asset_id') or args.get('emoji_refs'):raise ap.ServiceValidationError('festival_requires_one_bound_candidate_and_inspected_asset')
            return 'send_image',{**common,'candidate_id':ids[0],'asset_id':args['asset_id']}
        if args.get('asset_id'):raise ap.ServiceValidationError('generated_asset_not_allowed_for_this_service')
        return 'deliver_proactive',{**common,'candidate_ids':ids}
    events=s.read("SELECT id FROM events WHERE kind='passive' AND status='dispatched'")
    if len(events)!=1:raise ap.ServiceValidationError('one_current_passive_event_required')
    if args.get('asset_id') or args.get('candidate_ids') or args.get('execution_token'):raise ap.ServiceValidationError('passive_reply_cannot_select_proactive_delivery')
    if args.get('highlight_ids'):common['highlight_ids']=args['highlight_ids']
    return 'reply_to_message',{**common,'event_id':business.token(events[0]['id'])}

def definition(schema,definitions):
    props={k:v for k,v in definitions['reply_to_message'][1]['properties'].items() if k in ('body','generation_token','emoji_refs','highlight_ids')}
    props.update(candidate_ids=definitions['deliver_proactive'][1]['properties']['candidate_ids'],asset_id={'type':'string'},execution_token={'type':'string'})
    return ('send_message','向当前绑定的家长消息或服务任务交付文字/附件，不接受接收人或任意URL。普通回复仅body；主动沿用本任务candidate_ids与execution_token；节日另带已检查asset_id；被动高光可带highlight_ids。generation_token与execution_token仅放独立参数，禁止放进body。沿用generation_token，不凭accepted或unknown重发。家长取消/暂停/修改安排时，先skill_read(name=manage-service-arrangements)完成事务；未取得committed回执不得确认已改动。',schema(props,['body']))
