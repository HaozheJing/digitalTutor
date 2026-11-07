import os,json
from pathlib import Path
r=Path(__file__).resolve().parents[1];p=json.loads((r/'config/provider.json').read_text())
env=dict(os.environ,PATH=str(r/'runtime/node-v22.21.1-linux-x64/bin')+':'+os.environ['PATH'],DSH_HOME=str(r/'runtime/dsh-home'),DSH_AGENTS_HOME=str(r/'runtime/agents-home'),TUTOR_API_KEY=p['api_key'],DSH_PERMISSION_MODE='workspace-write')
os.chdir(r);os.execve(str(r/'runtime/node-v22.21.1-linux-x64/bin/node'),['node',str(r/'app/node_modules/@deepseek-ai/dsh/lib/bin.js'),'web','--port',os.environ.get('TUTOR_NATIVE_PORT','19031'),'--no-open'],env)
