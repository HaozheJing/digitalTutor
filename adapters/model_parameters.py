"""Validated A-experiment controls; omitted values retain the previous route."""
import math

MODEL = 'deepseek-v4.1-flash-economy'

def validate(model, value):
    if not isinstance(value, dict): raise ValueError('模型参数必须是对象')
    if not value: return {}
    if model != MODEL: raise ValueError('目前参数控制仅支持 '+MODEL)
    if set(value) - {'thinking','reasoning_effort','temperature','top_p','max_tokens'}:
        raise ValueError('存在尚未接通的模型参数（不支持 seed）')
    p = dict(value)
    if 'thinking' in p and p['thinking'] not in ('enabled','disabled'):
        raise ValueError('thinking 只能是 enabled 或 disabled')
    if 'reasoning_effort' in p and p['reasoning_effort'] not in ('low','high','max'):
        raise ValueError('思考强度只能是 low、high 或 max；关闭思考请使用 thinking')
    if p.get('thinking') == 'disabled' and 'reasoning_effort' in p:
        raise ValueError('关闭思考时不能设置思考强度')
    for key, low, high in [('temperature',0,2),('top_p',0.95,1)]:
        if key in p and (type(p[key]) not in (int,float) or not math.isfinite(p[key]) or not low <= p[key] <= high):
            raise ValueError(f'{key} 必须介于 {low} 和 {high} 之间')
    if p.get('thinking') == 'disabled' and p.get('top_p',1) != 1:
        raise ValueError('非思考模式的 top_p 固定为 1，不能使用其他值')
    if 'max_tokens' in p and (type(p['max_tokens']) is not int or not 256 <= p['max_tokens'] <= 32768):
        raise ValueError('A 工作台输出上限必须为 256～32768 的整数')
    return p

def apply(body, model, parameters):
    p = validate(model, parameters)
    if body.get('model') != model: raise ValueError('模型与冻结配置不一致')
    result = dict(body)
    if 'thinking' in p:
        result.pop('reasoning_effort',None)
        result['thinking'] = {'type':p['thinking']}
        if p['thinking']=='disabled': result['reasoning_effort']='none'
    if 'reasoning_effort' in p:
        result['thinking'] = {'type':'enabled'}
        result['reasoning_effort'] = p['reasoning_effort']
    for key in ('temperature','top_p'):
        if key in p: result[key]=p[key]
    if 'max_tokens' in p:
        result.pop('max_completion_tokens',None)
        result['max_tokens']=p['max_tokens']
    return result

def snapshot(body):
    return {k:body[k] for k in ('model','thinking','reasoning_effort','temperature','top_p','max_tokens','max_completion_tokens') if k in body}
