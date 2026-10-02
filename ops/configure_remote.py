"""Generate remote runtime configuration from a protected provider file."""
import json
import os
import pathlib
import secrets
import shlex

def merge(existing, updates):
    result = dict(existing)
    for key, value in updates.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result

base = pathlib.Path('/root/autodl-tmp/digitalTutor')
os.umask(0o077)
p = json.loads((base/'config/provider.json').read_text())
probe = json.loads((base/'docs/provider-probe.json').read_text())
assert probe.get('chat_ok') and probe.get('embedding_dimension'), 'Provider probe must pass first'
keypath=base/'config/openviking-api-key'
if not keypath.exists(): keypath.write_text(secrets.token_urlsafe(40))
ovkey=keypath.read_text().strip()
userkeypath=base/'config/openviking-user-key'
userkey=userkeypath.read_text().strip() if userkeypath.exists() else ''
ov={
 'server': {'host':'127.0.0.1','port':1933,'root_api_key':ovkey},
 'storage': {'workspace':str(base/'state/openviking')},
 'embedding': {'dense':{'provider':'openai','api_base':p['base_url'],'api_key':p['api_key'],'model':p['embedding_name'],'dimension':probe['embedding_dimension'],'input':'text'}},
 'vlm': {'provider':'openai','api_base':p['base_url'],'api_key':p['api_key'],'model':p['small_llm_name']}
}
ovpath=base/'config/ov.conf'
ov=merge(json.loads(ovpath.read_text()) if ovpath.exists() else {},ov)
ovpath.write_text(json.dumps(ov,ensure_ascii=False,indent=2))
(base/'config/ovcli.conf').write_text(json.dumps({'url':'http://127.0.0.1:1933','api_key':userkey,'root_api_key':ovkey},indent=2))
hermes_home=base/'state/hermes'
hermes_home.mkdir(exist_ok=True)
# JSON is valid YAML and avoids a separate generator dependency.
hermes_config={'_config_version':46,'model':{'provider':'custom','default':p['small_llm_name'],'base_url':p['base_url'],'api_mode':'chat_completions'},'memory':{'provider':'openviking'},'auxiliary':{'compression':{'provider':'custom','model':p['small_llm_name'],'base_url':p['base_url']}}}
cfgpath=hermes_home/'config.yaml'
if cfgpath.exists():
    raw=cfgpath.read_text()
    try: previous=json.loads(raw)
    except json.JSONDecodeError:
        import yaml
        previous=yaml.safe_load(raw) or {}
    hermes_config=merge(previous,hermes_config)
cfgpath.write_text(json.dumps(hermes_config,ensure_ascii=False,indent=2))
env={'OPENAI_API_KEY':p['api_key'],'OPENAI_BASE_URL':p['base_url'],'OPENVIKING_ENDPOINT':'http://127.0.0.1:1933','OPENVIKING_API_KEY':userkey,'OPENVIKING_ACCOUNT':'digitaltutor','OPENVIKING_USER':'teacher'}
envpath=hermes_home/'.env'
preserved=[]
if envpath.exists():
    preserved=[line for line in envpath.read_text().splitlines() if line.split('=',1)[0].strip() not in env]
envpath.write_text('\n'.join(preserved)+('\n' if preserved else '')+''.join(k+'='+shlex.quote(v)+'\n' for k,v in env.items()))
paths={'DIGITALTUTOR_ROOT':str(base),'HERMES_HOME':str(hermes_home),'HERMES_INSTALL_DIR':str(base/'vendor/hermes'),'OPENVIKING_CONFIG_FILE':str(base/'config/ov.conf'),'OPENVIKING_CLI_CONFIG_FILE':str(base/'config/ovcli.conf'),'UV_CACHE_DIR':str(base/'cache/uv'),'UV_PYTHON_INSTALL_DIR':str(base/'runtime/python'),'XDG_CACHE_HOME':str(base/'cache'),'XDG_DATA_HOME':str(base/'state/xdg'),'XDG_CONFIG_HOME':str(base/'config/xdg'),'TMPDIR':str(base/'tmp'),'PYTHONUNBUFFERED':'1'}
(base/'config/runtime.env').write_text(''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in paths.items())+'export PATH="$DIGITALTUTOR_ROOT/bin:$PATH"\n')
for name,exe,args in [('hermes','vendor/hermes/venv/bin/python','-m hermes_cli.main "$@"'),('ov','runtime/openviking/bin/ov','"$@"'),('openviking-server','runtime/openviking/bin/openviking-server','"$@"')]:
 wrapper=base/'bin'/name
 wrapper.write_text('#!/usr/bin/env bash\nset -euo pipefail\nsource '+str(base/'config/runtime.env')+'\ncd "$DIGITALTUTOR_ROOT/vendor/hermes"\nexec "$DIGITALTUTOR_ROOT/'+exe+'" '+args+'\n')
 wrapper.chmod(0o700)
supervisor=f'''[unix_http_server]
file={base}/state/supervisor.sock
chmod=0700
[supervisord]
logfile={base}/logs/supervisord.log
pidfile={base}/state/supervisord.pid
childlogdir={base}/logs
directory={base}
logfile_maxbytes=10MB
logfile_backups=3
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{base}/state/supervisor.sock
[program:openviking]
command={base}/bin/openviking-server --config {base}/config/ov.conf
autostart=true
autorestart=true
startsecs=10
stopasgroup=true
killasgroup=true
stopwaitsecs=60
stdout_logfile={base}/logs/openviking.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=3
redirect_stderr=true
[program:hermes]
command={base}/bin/hermes serve --host 127.0.0.1 --port 9119
autostart=false
autorestart=true
startsecs=10
stopasgroup=true
killasgroup=true
stopwaitsecs=60
stdout_logfile={base}/logs/hermes.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=3
redirect_stderr=true
'''
(base/'config/supervisord.conf').write_text(supervisor)
for f in [base/'config/provider.json',keypath,base/'config/ov.conf',base/'config/ovcli.conf',hermes_home/'.env',hermes_home/'config.yaml']:f.chmod(0o600)
print('Configuration generated. All understanding models:',p['small_llm_name'])
