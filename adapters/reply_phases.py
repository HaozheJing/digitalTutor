"""A-only two-stage contract. Existing delivery guards remain authoritative."""
import copy
import json
from model_parameters import validate

DELIVERY = {'reply_to_message', 'reply_with_highlights', 'deliver_message',
            'deliver_proactive', 'deliver_festival', 'send_image', 'workflow_fulfill'}
FENCED = DELIVERY | {'workflow_highlight', 'mock_reply', 'send_mock'}


def pipeline(model, value):
    if value is None or value == {}: return {}
    if not isinstance(value, dict) or set(value) != {'mode', 'prepare', 'compose'} or value['mode'] != 'two_stage':
        raise ValueError('两阶段配置须包含 mode=two_stage、prepare、compose')
    result = {'mode': 'two_stage', 'prepare': validate(model, value['prepare']),
              'compose': validate(model, value['compose'])}
    if result['compose'].get('thinking') != 'disabled':
        raise ValueError('本轮两阶段实验的组织回复阶段固定关闭思考')
    return result


def short_name(name):
    return name.removeprefix('mcp__tutor__')


def request_for_phase(body, config, state):
    """Restrict tools and switch parameters on a persisted ready receipt only."""
    result = copy.deepcopy(body)
    phase = state.get('phase', 'prepare')
    if phase not in ('prepare', 'compose'): raise ValueError('reply_phase_already_terminal')
    tools = result.get('tools', [])
    if phase == 'prepare':
        result['tools'] = [t for t in tools if short_name(t['function']['name']) not in FENCED | {'skill'}]
        if not state.get('context_loaded'):
            result['tools']=[t for t in result['tools'] if short_name(t['function']['name'])=='reply_prepare_context']
            if len(result['tools'])!=1:raise ValueError('preparation_context_tool_unavailable')
            result['tool_choice']={'type':'function','function':{'name':result['tools'][0]['function']['name']}}
        instruction = ('本次实验只有准备物料和组织回复两个阶段。现在是准备物料阶段。'
                       '先调用 reply_prepare_context 一次取得常用 Skill 和真实上下文；工具已用 service_skill 登记读取，已有信息不要重复读取。'
                       '仅缺少资料时补查，互不依赖的查询可放在同一轮。先判断是否需要表情，再按 reply-style 检索。'
                       '需要业务动作时先通过原工具完成，不能把计划当成成功。材料齐全后调用 reply_material_ready，'
                       '提交已核实的简短回复要点、要使用的交付工具和除 body 外的完整参数（含所需授权令牌及表情引用）。'
                       '此阶段不写对客正文、不直接发送。无需联系时按原 Skill 结束任务，不强行进入表达阶段。')
    else:
        name = state['delivery_tool']
        result['tools'] = [t for t in tools if short_name(t['function']['name']) == name]
        if len(result['tools']) != 1: raise ValueError('prepared_delivery_tool_unavailable')
        # All authority/target/media fields were decided in preparation. Only
        # body remains model-authored; MCP checks equality again before sending.
        fn = result['tools'][0]['function']
        schema = fn['parameters']
        for key, val in state['delivery_args'].items():
            if key in schema.get('properties', {}): schema['properties'][key]['enum'] = [val]
        result['tool_choice'] = {'type': 'function', 'function': {'name': fn['name']}}
        instruction = ('物料准备完成。现在只组织一次对客回复，使用已加载的 Persona、reply-style、用户原话和近期对话。'
                       '依据以下已核实要点表达；不增加事实、承诺、建议、业务动作或表情。'
                       '直接调用指定交付工具一次，除 body 外原样使用以下参数；不要查询或再次规划。\n'
                       + json.dumps({'points': state['points'], 'delivery_tool': name,
                                     'delivery_args': state['delivery_args']}, ensure_ascii=False))
    result['parallel_tool_calls'] = phase == 'prepare' and bool(state.get('context_loaded'))
    # Keep the original conversation and native tool results; never relay a
    # second model's hidden reasoning or manufacture a user message.
    result['messages'].insert(0, {'role': 'system', 'content': instruction})
    return result, config[phase], phase


def validate_handoff(args, schemas):
    if set(args) != {'delivery_tool', 'delivery_args', 'points'}: raise ValueError('invalid_handoff_fields')
    name, fixed, points = args['delivery_tool'], args['delivery_args'], args['points']
    if name not in DELIVERY or name not in schemas: raise ValueError('unsupported_delivery_tool')
    if not isinstance(points, list) or not 1 <= len(points) <= 8 or any(not isinstance(x, str) or not x.strip() or len(x)>500 for x in points):
        raise ValueError('one_to_eight_short_verified_points_required')
    schema = schemas[name]
    if not isinstance(fixed, dict) or 'body' in fixed or set(fixed)-set(schema['properties']): raise ValueError('invalid_fixed_delivery_args')
    required=set(schema.get('required', []))|({'generation_token','execution_token'} & set(schema['properties']))
    if required-{'body'}-set(fixed): raise ValueError('missing_delivery_authority_or_target')
    if len(json.dumps(fixed, ensure_ascii=False)) > 12000: raise ValueError('delivery_args_too_large')
    return {'phase': 'compose', **copy.deepcopy(args)}
