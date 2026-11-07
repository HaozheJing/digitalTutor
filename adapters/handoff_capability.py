"""Bind human handoff to the current owner; the model cannot construct IDs."""
import agent_plan as ap
import sql_tools as s


def route(args):
    if not isinstance(args,dict) or set(args)-{'reason','proposed_reply','category'}:
        raise ap.ServiceValidationError('human_handoff_is_host_bound_do_not_supply_target_ids')
    task=ap.active()
    if task:
        return {**args,'task_id':task['id']}
    events=s.read("SELECT id FROM events WHERE kind='passive' AND status='dispatched'")
    if len(events)!=1:
        raise ap.ServiceValidationError('one_current_passive_event_required_for_handoff')
    return {**args,'event_id':events[0]['id']}


def definition(schema):
    return ('request_human_confirmation',
            '将当前绑定的消息或任务转人工核对；宿主确定目标，不传、不猜、不拼接event_id或task_id。reason说明缺口，proposed_reply仅供人工审核，不发送。提交成功后本轮停止发送；人工处理不等于业务问题解决。',
            schema({'reason':{'type':'string'},'proposed_reply':{'type':'string'},
                    'category':{'type':'string','enum':['needs_confirmation','missing_information','unsupported_request','delivery_problem','other']}},['reason']))
