
import os,sys,json
from pathlib import Path
from datetime import datetime,timezone
root=Path('/home/ubuntu/digitalTutor');candidate=Path('/home/ubuntu/digitalTutor/var/wecom-autoreply/workbench/versions/20261019T061705927375-da41fbf5')
sys.path.insert(0,str(candidate/'backend/src'))
os.environ.pop('DIGITAL_TUTOR_WECOM_CONFIG',None)
os.environ.update(DIGITAL_TUTOR_CONTEXT_MODE='daily_native',DIGITAL_TUTOR_STEERING='1',DIGITAL_TUTOR_WORKFLOW='1')
from digital_tutor.application import Application
from digital_tutor.hermes_host import HermesHost
from digital_tutor.scheduler import Worker
state=root/'var/wecom-autoreply/smoke-workbench-20261019';state.mkdir(exist_ok=True)
app=Application(state/'business.sqlite',mode='passive',strict_runtime=True,service_start=0,service_end=24)
app.create_family('synthetic-smoke','synthetic-smoke','mock:synthetic-smoke')
token=app.open_session('synthetic-smoke','channel')
app.ingest_message(token,{'external_id':'synthetic-only-001','body':'Hello, can we chat?','occurred_at':datetime.now(timezone.utc).isoformat()})
app.revoke_session(token)
host=HermesHost(candidate,state/'runs',budget_seconds=180,context_mode='daily_native')
result=Worker(app,host,name='synthetic-smoke',lease_seconds=30).run_once()
summary={'result':result,'last_result':host.last_result,'operations':[{'stage':o['stage'],'body':o['body']} for o in app.store.list('operation','synthetic-smoke')]}
(state/'result.json').write_text(json.dumps(summary,ensure_ascii=False,default=str));app.close()
