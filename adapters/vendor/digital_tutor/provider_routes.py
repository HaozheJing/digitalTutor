"""Explicit model routes; the embedding provider and frozen runs stay independent."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class ModelRouteError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(value, code, message):
    if not value:
        raise ModelRouteError(code, message)


def read_json(path):
    try:
        value = path.read_text(encoding='utf-8')
        return json.loads(value)
    except (OSError, ValueError):
        raise ModelRouteError('MODEL_ROUTE_CONFIG_MISSING', '模型路由配置缺失或无效') from None


def selected_route(project):
    path = Path(project)/'config/model-route.json'
    config = read_json(path) if path.exists() else {'provider':'direct'}
    name = config.get('provider')
    require(name in {'direct', 'igetcoolapi', 'arkplan'} and name not in config.get('retired_providers', []),
            'MODEL_ROUTE_NOT_ALLOWED', '模型路由未获准或已经停用')
    return name


def authenticated_role(app, token):
    # Older project baselines do not include role in the returned binding.
    # Ask the authoritative session API; never accept a role from model input.
    for role in ['service', 'planner_daily', 'planner_progress', 'memory_daily']:
        try:
            app.auth(token, {role})
            return role
        except Exception as exc:
            if getattr(exc, 'code', None) != 'ROLE_FORBIDDEN':
                raise
    raise ModelRouteError('ROLE_FORBIDDEN', '会话角色不能使用模型路由')


def resolve_connection(project, role='service', *, strict=True, check_health=True):
    project = Path(project)
    name = selected_route(project)
    if name == 'arkplan':
        p = read_json(project/'config/provider.json')
        require(p.get('base_url') == 'https://ark.cn-beijing.volces.com/api/plan/v3',
                'MODEL_DESTINATION_FORBIDDEN', '必须使用 Agent Plan 专属接口')
        require(p.get('small_llm_name') == p.get('large_llm_name') == 'doubao-seed-2.1-pro',
                'MODEL_NOT_ALLOWED', 'Agent Plan 默认聊天模型未获准')
        require(isinstance(p.get('api_key'), str) and p['api_key'].startswith('ark-'),
                'MODEL_ROUTE_CONFIG_MISSING', 'Agent Plan 凭据缺失')
        return {'name': name, 'base_url': p['base_url'], 'api_key': p['api_key'],
                'model': p['small_llm_name'], 'generation_options': {'temperature': 0.5 if role == 'service' else 0.0}}
    if name == 'direct':
        p = read_json(project/'config/provider.json')
        require(p.get('small_llm_name') == 'qwen3.8-flash', 'MODEL_NOT_ALLOWED', '直连保留原 Flash 模型限制')
        if strict:
            require(urlsplit(p.get('base_url', '')).hostname == 'maas.qianwenaiapi.com',
                    'MODEL_DESTINATION_FORBIDDEN', '直连模型目的地未批准')
        return {'name': name, 'base_url': p['base_url'], 'api_key': p['api_key'],
                'model': p['small_llm_name'], 'generation_options': {'temperature': 0.0} if strict else {}}
    p = read_json(project/'config/igetcoolapi-runtime.json')
    u = urlsplit(p.get('base_url', ''))
    require(u.scheme == 'http' and u.hostname == '127.0.0.1' and u.port is not None
            and 1024 <= u.port <= 65535 and u.path == '/v1' and not (u.username or u.password or u.query or u.fragment),
            'MODEL_DESTINATION_FORBIDDEN', 'igetcoolapi 必须使用获准的本机回环中转地址')
    require(p.get('provider') == name and p.get('upstream_base_url') == 'https://ai-gateway.igetcool.com/openaibailian',
            'MODEL_DESTINATION_FORBIDDEN', 'igetcoolapi 上游未获准')
    key = 'service_llm_name' if role == 'service' else 'planner_llm_name'
    require(p.get(key) == 'qwen3.7-max', 'MODEL_NOT_ALLOWED', 'igetcoolapi 模型未获准')
    temperature = p.get('service_temperature', 0.5) if role == 'service' else p.get('planner_temperature', 0.0)
    require(isinstance(temperature, (int, float)) and not isinstance(temperature, bool) and 0 <= temperature <= 2,
            'TEMPERATURE_NOT_ALLOWED', '温度必须为 0 至 2 之间的数值')
    token = p.get('api_key')
    require(isinstance(token, str) and len(token) >= 32, 'MODEL_ROUTE_CONFIG_MISSING', '中转认证配置无效')
    if check_health:
        try:
            req = Request(p['base_url'].removesuffix('/v1')+'/health', headers={'Authorization': 'Bearer '+token})
            # Loopback traffic must not inherit the cloud host's HTTP proxy.
            with build_opener(ProxyHandler({})).open(req, timeout=3) as res:
                status = json.loads(res.read(4096))
            require(status.get('provider') == name and status.get('ready') is True and status.get('model') == p[key],
                    'MODEL_ROUTE_UNAVAILABLE', 'igetcoolapi 中转尚未就绪')
        except ModelRouteError:
            raise
        except Exception:
            raise ModelRouteError('MODEL_ROUTE_UNAVAILABLE', 'igetcoolapi 未连接；请启动本地隧道并确认局域网/VPN') from None
    return {'name': name, 'base_url': p['base_url'], 'api_key': token, 'model': p[key],
            'generation_options': {'temperature': float(temperature)}}
