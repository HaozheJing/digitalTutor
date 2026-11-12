"""Register capability-bound business tools in Hermes' native registry."""
import json
from .efficient_context import enabled, project_result, strip_schema_titles
from .storage import digest

TOOLSET = 'digital_tutor'

def redacted(value):
    if isinstance(value, dict):
        return {k:redacted(v) for k,v in value.items() if k not in
                {'lease','lease_token','token','token_hash','api_key','capability'}}
    if isinstance(value, list):
        return [redacted(v) for v in value]
    return value



def register_tools(business, trace, on_tool=None, exclude_materials=False):
    from tools.registry import registry
    from .domain import require
    surface = getattr(business.app, 'prompt_tool_surface', 'direct')
    require(surface in {'direct','discovery'}, 'TOOL_SURFACE_INVALID', '工具表面配置无效')
    facade = None
    if surface == 'discovery':
        from .tool_discovery import BusinessToolFacade
        facade = BusinessToolFacade(business, trace, on_tool, registry)
    names = []
    native_schemas = []
    for spec in business.describe():
        original = spec['name']
        if exclude_materials and original.startswith(('skills.','knowledge.')):
            continue
        name = 'dt_' + original.replace('.', '_')
        def bind(tool_name):
            def invoke(args, **kwargs):
                args=dict(args);full=False
                model_args=dict(args)
                if (getattr(business.app,'context_mode',None)=='rebuild_focused'
                        and tool_name in {'wechat.read_all_messages','wechat.read_message_stream','context.read'}
                        and args.get('context_view') in {None,'focused','full'}):
                    full=args.pop('context_view',None)=='full'
                from .runtime_hooks import gate
                from .domain import BusinessError
                try:
                    gate(business.app,business.token,'before:'+tool_name)
                    if tool_name=='wechat.send_message' and enabled(business.app,'bindings'):
                        from .reply_segments import compile_send
                        args=compile_send(args)
                    result = redacted(business.call(tool_name, args))
                except BusinessError as exc:
                    result={'ok':False,'error':{'code':exc.code,'message':exc.message}}
                if getattr(business.app,'strict_runtime',False) and tool_name in {'knowledge.read','skills.read'} and not result['ok']:
                    from .materials import MATERIAL_FAILURE_BOUNDARY
                    result={**result,'source_boundary':MATERIAL_FAILURE_BOUNDARY}
                if tool_name in {'knowledge.read','skills.read'}:
                    from .material_recovery import record_read
                    record_read(business.app,business.token,tool_name,args,result)
                trace.append({'tool': tool_name, 'arguments_digest': digest(args),
                              'requested_context_view':'full' if full else 'default',
                              'ok': result['ok'], 'result_digest': digest(result),
                              'error': result.get('error', {}).get('code'), 'at':business.app.clock.iso()})
                if surface == 'discovery':
                    trace[-1]['model_tool'] = 'dt_tools_call'
                if getattr(business.app,'strict_runtime',False):
                    trace[-1].update(arguments=redacted(args),result=result)
                    if args!=model_args:trace[-1]['model_arguments']=redacted(model_args)
                from .context_transport import render, fingerprint
                model_value=result
                model_value=project_result(tool_name,model_value,getattr(business.app,'context_features',()))
                if getattr(business.app,'context_mode',None)=='daily_native' and result.get('ok'):
                    from .daily_context import project,thin_tool_result
                    if tool_name=='context.read':
                        family,binding=business.app.auth(business.token,{'service','planner_daily','planner_progress'})
                        model_value={**model_value,'result':project(business.app,family,binding,result['result'])}
                    model_value=thin_tool_result(model_value,getattr(business.app,'daily_sources',{}))
                if getattr(business.app,'context_mode',None)=='rebuild_focused':
                    from .focused_context import tool_view
                    model_value=tool_view(tool_name,result,full=full)
                model_result = render(model_value, getattr(business.app, 'context_mode', 'rebuild_legacy'))
                if getattr(business.app,'strict_runtime',False):
                    trace[-1]['model_result']=model_result
                trace[-1].update(model_result_chars=len(model_result),
                                raw_result_chars=len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))),
                                model_result_sha256=fingerprint(model_result))
                if tool_name=='learning.query_sql' and result['ok']:
                    table=result['result']
                    trace[-1]['returned_rows']=len(table['rows'])
                    if 'uid' in table['columns']:
                        family,_=business.app.auth(business.token,{'service','planner_daily','planner_progress','admin'})
                        uid=business.app.store.family(family)['uid']
                        trace[-1]['authorized_uid_only']=all(row['uid']==uid for row in table['rows'])
                if on_tool:
                    on_tool(trace[-1])
                finished=getattr(business.app,'on_native_tool_finished',None)
                if finished:finished(trace[-1])
                if result['ok'] and tool_name in {'memory.save','memory.delete'}:
                    changed=getattr(business.app,'on_memory_context_changed',None)
                    if changed:changed()
                if result['ok'] and tool_name in {'progress.submit','planner.commit_progress'}:
                    from .runtime_hooks import crash_boundary
                    crash_boundary(business.app,'after:'+tool_name,
                                   request_id=args.get('request_id'),report_id=args.get('report_id'),
                                   revision=args.get('revision'))
                return model_result
            return invoke
        if getattr(business.app,'context_mode',None)=='rebuild_focused' and original in {'wechat.read_all_messages','wechat.read_message_stream','context.read'}:
            from copy import deepcopy
            spec=deepcopy(spec)
            spec['input_schema'].setdefault('properties',{})['context_view']={'type':'string','enum':['focused','full'],
                'description':'默认展示必要来源及近期对话。需要较早原文、完整历史或引用不清时填full，取得完整实际读取结果。'}
        if original=='wechat.send_message' and enabled(business.app,'bindings'):
            from .reply_segments import send_schema
            spec={**spec,'input_schema':send_schema(spec['input_schema'])}
        if enabled(business.app,'schema'):
            spec={**spec,'input_schema':strip_schema_titles(spec['input_schema'])}
        schema={'name': name, 'description': spec['description'], 'parameters': spec['input_schema']}
        native_schemas.append({'type':'function','function':schema})
        if facade:
            facade.add(original, schema, bind(original))
        else:
            registry.register(name=name, toolset=TOOLSET, schema=schema,
                              handler=bind(original), check_fn=lambda: True)
        names.append(name)
    business.app.tool_surface_metrics = {
        'surface':surface, 'authorized_business_tool_count':len(names),
        'direct_business_schema_chars':len(json.dumps(native_schemas,ensure_ascii=False,separators=(',',':'))),
        'unit':'characters_not_provider_tokens',
        'discovery_has_additional_requests_and_schema_result_tokens':surface == 'discovery',
    }
    if facade:
        return facade.expose()
    return sorted(names)
