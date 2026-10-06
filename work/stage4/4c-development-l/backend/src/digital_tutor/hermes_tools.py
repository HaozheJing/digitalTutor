"""Register capability-bound business tools in Hermes' native registry."""
import json
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
    names = []
    for spec in business.describe():
        original = spec['name']
        if exclude_materials and original.startswith(('skills.','knowledge.')):
            continue
        name = 'dt_' + original.replace('.', '_')
        def bind(tool_name):
            def invoke(args, **kwargs):
                from .runtime_hooks import gate
                from .domain import BusinessError
                try:
                    gate(business.app,business.token,'before:'+tool_name)
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
                              'ok': result['ok'], 'result_digest': digest(result),
                              'error': result.get('error', {}).get('code'), 'at':business.app.clock.iso()})
                if getattr(business.app,'strict_runtime',False):
                    trace[-1].update(arguments=redacted(args),result=result)
                if tool_name=='learning.query_sql' and result['ok']:
                    table=result['result']
                    trace[-1]['returned_rows']=len(table['rows'])
                    if 'uid' in table['columns']:
                        family,_=business.app.auth(business.token,{'service','planner_daily','planner_progress','admin'})
                        uid=business.app.store.family(family)['uid']
                        trace[-1]['authorized_uid_only']=all(row['uid']==uid for row in table['rows'])
                if on_tool:
                    on_tool(trace[-1])
                return json.dumps(result, ensure_ascii=False)
            return invoke
        registry.register(name=name, toolset=TOOLSET,
                          schema={'name': name, 'description': spec['description'],
                                  'parameters': spec['input_schema']},
                          handler=bind(original), check_fn=lambda: True)
        names.append(name)
    return sorted(names)
