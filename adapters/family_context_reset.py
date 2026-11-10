"""Map the teacher's reset to the selected family's authenticated native actor."""
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from family_lab_server import NoRedirect


def reset(owner, family, request):
    workspace = Path(family['workspace']).resolve()
    binding = json.loads((workspace / 'config/family-lab.json').read_text())
    family_id = request.get('family_id')
    if (family_id != family['family_id'] or binding['family_id'] != family_id
            or binding['run_id'] != request['run_id'] or binding['workspace'] != str(workspace)
            or Path(binding['control_db']).resolve() != (owner.home / 'control.sqlite').resolve()):
        raise ValueError('会话归属已变化，请刷新')
    if not re.fullmatch(r'[a-f0-9-]{32,36}', request.get('request_id') or ''):
        raise ValueError('清理请求编号无效')
    assembly = json.loads((owner.home / 'assembly.json').read_text())
    port = int(assembly['family_ports'][family_id][1])
    key = json.loads((workspace / 'config/workbench.json').read_text())['token']
    payload = {k: request[k] for k in ('request_id', 'expected_context', 'expected_seq')}
    payload.update(run_id=binding['business_run_id'], family_id=family_id, lab_run_id=binding['run_id'])
    req = Request(f'http://127.0.0.1:{port}/workbench/api/new-conversation',
                  data=json.dumps(payload).encode(), headers={'Authorization':'Bearer '+key,
                  'Content-Type':'application/json','X-Workbench':'1'})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        if exc.code in (400, 409):
            raise ValueError(json.load(exc).get('error', '请刷新后再试')) from None
        if exc.code==404:
            raise ValueError('当前家庭运行端尚未启用清理功能') from None
        raise ValueError('家庭运行端未确认会话切换，请刷新核对') from None
    except (TimeoutError, URLError):
        raise ValueError('家庭运行端未确认会话切换，请刷新核对') from None
    if result.get('status') != 'context_cleared' or result.get('context_id') != request['request_id']:
        raise ValueError('家庭运行端尚未确认新会话')
    return {'status':'context_cleared','context_id':result['context_id']}
