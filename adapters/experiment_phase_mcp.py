"""Experiment-only MCP entry point; delegates every business operation unchanged."""
import json
import os
from pathlib import Path
import mcp_server as original
from reply_phases import DELIVERY, FENCED, validate_handoff

R = Path(__file__).resolve().parents[1]
STATE = R/'runtime/reply-phase.json'
SCHEMAS = {n: sc for n, _, sc in original.base_mcp.TOOLS}
CALL = original.base_mcp.call


def read_state():
    return json.loads(STATE.read_text(encoding='utf8')) if STATE.exists() else {'phase': 'prepare'}


def save_state(value):
    temp = STATE.with_suffix('.new')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8');temp.chmod(0o600)
    os.replace(temp, STATE)


def call(name, args):
    state = read_state()
    if name == 'reply_prepare_context':
        if state['phase'] != 'prepare': raise ValueError('preparation_already_completed')
        if state.get('context_loaded'): return {'status':'already_loaded', 'note':'沿用上一份上下文，仅缺失时补查'}
        frozen = json.loads((R/'config/frozen-experiment-input.json').read_text())
        import agent_plan, test_mode
        task = agent_plan.active()
        shared = [] if agent_plan.independent_task() else ['service-policy','tutor-main'] + (['proactive-service'] if task else [])
        skills = list(dict.fromkeys(shared + ['reply-style'] + ([task['skill']] if task and task.get('skill') else [])))
        result = {'skills': {s: CALL('service_skill', {'name': s}) for s in skills}}
        result['delivery_contracts'] = {n:{'required':sorted((set(SCHEMAS[n].get('required',[]))|({'generation_token','execution_token'} & set(SCHEMAS[n]['properties'])))-{'body'}),
                                          'properties':{k:v for k,v in SCHEMAS[n]['properties'].items() if k!='body'}}
                                       for n in sorted(DELIVERY) if n in SCHEMAS}
        result['daily_context'] = (__import__('observability').invoke('daily_context',{'day':test_mode.clock().date().isoformat()},lambda:__import__('passive_context').entry(test_mode.clock().date().isoformat()),origin='host') if __import__('tool_surface').enabled('context') else CALL('daily_context', {'day': test_mode.clock().date().isoformat()}))
        if frozen['scenario']['mode'] == 'passive': result['workflow_context'] = CALL('workflow_context', {})
        state['context_loaded'] = True;save_state(state)
        return result
    if name == 'reply_material_ready':
        if state['phase'] != 'prepare' or not state.get('context_loaded'): raise ValueError('prepare_context_before_handoff')
        try: ready = validate_handoff(args, SCHEMAS)
        except ValueError as exc:
            failures=state.get('handoff_errors',0)+1
            save_state({**state,'handoff_errors':failures,**({'phase':'terminal','receipt_status':'failed'} if failures>=3 else {})})
            return {'status':'rejected','reason':str(exc),'allowed_fields':list(SCHEMAS.get(args.get('delivery_tool'),{}).get('properties',{})),
                    'note':'delivery_args 不含 body，且只能使用指定工具的字段；reply_to_message 不接受 day。',
                    'reply_phase_terminal':failures>=3}
        # Native execution will still validate generation/execution tokens,
        # task revisions, reference provenance, and emoji quotas at delivery.
        save_state({**state, **ready})
        return {'status': 'material_ready', 'next_phase': 'compose', 'delivery_tool': ready['delivery_tool']}
    if name in FENCED:
        if state['phase'] != 'compose' or name != state['delivery_tool']: raise ValueError('delivery_requires_material_ready')
        if {k:v for k,v in args.items() if k!='body'} != state['delivery_args'] or not isinstance(args.get('body'),str) or not args['body'].strip():
            raise ValueError('expression_may_only_change_body')
        # Claim before invoking: an exception or ambiguous transport never
        # permits an automatic second send.
        save_state({**state, 'phase':'terminal', 'delivery_attempted':True})
        try: result = CALL(name, args)
        except Exception as exc:
            save_state({**state, 'phase':'terminal', 'delivery_attempted':True, 'receipt_status':'failed', 'error_type':type(exc).__name__})
            return {'status':'failed','error_type':type(exc).__name__,'reason':str(exc),'reply_phase_terminal':True}
        if isinstance(result, dict):
            save_state({**state, 'phase':'terminal', 'delivery_attempted':True, 'receipt_status':result.get('status')})
            # Pass the original receipt through. The native host already
            # registers active service deliveries; passive replies use the
            # existing finish_event validation, never direct SQL state edits.
            if name in {'reply_to_message','reply_with_highlights'} and result.get('status') in {'sent','accepted'}:
                try:
                    import test_mode
                    result['phase_finish_receipt'] = CALL('finish_event', {'event_id':args['event_id'],'day':test_mode.clock().date().isoformat()})
                except Exception as exc: result['phase_finish_error'] = type(exc).__name__
            return {**result, 'reply_phase_terminal':True}
        return result
    if state['phase'] != 'prepare': raise ValueError('tools_closed_after_preparation')
    return CALL(name, args)


if __name__ == '__main__':
    from experiment_runtime import verify
    if not verify(): raise ValueError('two_stage_is_experiment_only')
    original.base_mcp.TOOLS += [
        ('reply_prepare_context','一次装载当前 Skill、家庭/课程/对话背景及操作上下文；缺失资料再补查',original.schema()),
        ('reply_material_ready','结束准备阶段：交接核实要点和除 body 外的交付参数；下一次模型只组织正文',original.schema({
            'delivery_tool':{'type':'string','enum':sorted(DELIVERY)},
            'delivery_args':{'type':'object'},
            'points':{'type':'array','minItems':1,'maxItems':8,'items':{'type':'string'}}},['delivery_tool','delivery_args','points']))]
    original.base_mcp.call = call
    original.base_mcp.main()
