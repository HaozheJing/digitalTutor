"""Explicit host-owned manual acceptance scope; never a simulated transport."""
import json,hashlib,sqlite3,datetime as dt
from pathlib import Path
from contextlib import closing
import sql_tools as s

def configured():return (s.R/'config/manual-workbench.json').is_file()

def verify():
    if not configured():return None
    value=json.loads((s.R/'config/manual-workbench.json').read_text())
    automatic=value.get('version')==2 and value.get('mode')=='allowlisted_automatic_test'
    if (not automatic and (value.get('version')!=1 or value.get('mode')!='allowlisted_manual_test')) or Path(value.get('workspace','')).resolve()!=s.R.resolve() or Path(s.DB).resolve()!=s.R.resolve()/'runtime/live.sqlite':
        raise ValueError('manual_workbench_scope_mismatch')
    if (s.R/'config/experiment.json').exists():raise ValueError('manual_workbench_cannot_be_simulation')
    settings=json.loads((s.R/'config/test-wechat.json').read_text())
    cfg=json.loads(Path(settings['provider_config']).read_text());route=settings['route']
    digest=hashlib.sha256(json.dumps(route,sort_keys=True).encode()).hexdigest()
    matches=[x for x in cfg['allowed_routes'] if all(x.get(k)==route.get(k) for k in ('orgId','imBotId','chatId','imContactId'))]
    if settings.get('simulation') or len(matches)!=1 or matches[0]['orgId']!=cfg['expected_org_id'] or digest!=value['route_sha256'] or cfg['api_base_url']!=value['api_base_url']:
        raise ValueError('manual_workbench_route_changed')
    with closing(sqlite3.connect('file:'+str(s.DB)+'?mode=ro',uri=True)) as c:
        row=c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()
    state=json.loads(row[0]) if row else {}
    if automatic:validate_window(value,state)
    if state.get('uid')!=value['family_uid'] or state.get('frozen') is not True or state.get('auto_highlights') is not automatic:
        raise ValueError('manual_workbench_requires_bound_frozen_manual_run')
    return {'enabled':True,'manual_only':not automatic,'family_uid':value['family_uid'],'delivery_mode':'allowlisted_test_wechat'}

def validate_window(value,state,as_of=None):
    try:
        start=dt.datetime.fromisoformat(value['starts_at']);end=dt.datetime.fromisoformat(value['ends_at'])
        authorized=dt.datetime.fromisoformat(value['authorized_at']);expires=dt.datetime.fromisoformat(value['authorization_expires_at'])
        current=dt.datetime.fromisoformat(as_of or state['as_of'])
        assert all(x.tzinfo is not None for x in [start,end,authorized,expires,current])
        assert dt.timedelta(0)<end-start<=dt.timedelta(days=7)
        assert dt.timedelta(0)<expires-authorized<=dt.timedelta(hours=24)
        assert authorized<=dt.datetime.now(dt.timezone.utc)<expires and start<=current<=end
        assert isinstance(value['run_id'],str) and value['run_id'] and state.get('run_id')==value['run_id']
        assert value.get('authorization')=='user_requested_real_wechat_week_test'
    except (KeyError,TypeError,ValueError,AssertionError):raise ValueError('automatic_test_outside_authorized_run_or_window') from None


def clock_authority(action,as_of):
    scope=verify()
    if not scope:return True
    if scope['manual_only']:return False
    if action!='advance':raise ValueError('authorized_week_test_cannot_reset_or_create_run')
    value=json.loads((s.R/'config/manual-workbench.json').read_text())
    with closing(sqlite3.connect('file:'+str(s.DB)+'?mode=ro',uri=True)) as c:
        state=json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
    validate_window(value,state,as_of)
    return True


if __name__=='__main__':print(json.dumps(verify()))
