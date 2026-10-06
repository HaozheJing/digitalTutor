"""Persist a remote demo and validate the administrator CLI/JSON tool transport."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

from digital_tutor.application import Application
from digital_tutor.demo import run_demo
from digital_tutor.learning import import_workbook

parser=argparse.ArgumentParser()
parser.add_argument('--root',default='.')
parser.add_argument('--workbook')
args=parser.parse_args()
root=Path(args.root).resolve()
run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
out=root/'evaluations'/'backend-stage1'/run_id
state=root/'state'/'backend-stage1'/run_id
out.mkdir(parents=True,exist_ok=False); state.mkdir(parents=True,exist_ok=False)
def save(name,data):
    (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

tests=json.loads((root/'evaluations'/'backend-stage1'/'tests.json').read_text(encoding='utf-8'))
assert tests['successful'] and not tests['failures'] and not tests['errors']
save('tests.json',tests)

db=state/'demo.sqlite'
app=Application(db)
try:
    demo=run_demo(app); save('demo.json',demo)
finally:
    app.close()

base=[sys.executable,'-m','digital_tutor','--db',str(db)]
def cli(*parts,input_=None):
    done=subprocess.run(base+list(parts),input=input_,capture_output=True,text=True,encoding='utf-8',check=True)
    return json.loads(done.stdout)

all_=cli('call','--family','family-demo','wechat.read_messages','--arguments','{"mode":"all"}')
latest=cli('call','--family','family-demo','wechat.read_messages','--arguments','{"mode":"latest"}')
assert all_['result']['returned_count']==2 and latest['result']['returned_count']==1
state_after=cli('call','--family','family-demo','state.read')['result']
assert state_after['events'][0]['status']=='waiting' and len(state_after['controls'])==1
assert state_after['events'][0]['remaining_work']==['lesson-demo']
sql=cli('call','--family','family-demo','learning.query_sql','--arguments',
        '{"sql":"SELECT count(*) AS rows FROM learning_days"}')
assert sql['result']['rows']==[{'rows':3}]
schemas=cli('tools','--family','family-demo'); save('service-tools.json',schemas)
lines='\n'.join(json.dumps(r) for r in [
    {'method':'tools/list'},
    {'method':'tools/call','name':'wechat.read_messages','arguments':{'mode':'latest'}},
    {'method':'tools/call','name':'state.read','arguments':{},'family':'another-family'},
])+'\n'
served=subprocess.run(base+['serve','--family','family-demo'],input=lines,capture_output=True,text=True,encoding='utf-8',check=True)
responses=[json.loads(line) for line in served.stdout.splitlines()]
assert responses[0]['ok'] and responses[1]['result']['returned_count']==1
assert responses[2]['error']['code']=='INVALID_JSON_REQUEST'
save('cli-verification.json',{'all_count':2,'latest_count':1,'sql_count':3,'persistent_restart_verified':True,
                            'stdio_tool_list_and_call_verified':True,'per_request_identity_override_rejected':True})

learning_summary=None
if args.workbook:
    source=Path(args.workbook).resolve()
    learning=Application(state/'learning.sqlite')
    try:
        meta=import_workbook(learning,source)
        duplicate=import_workbook(learning,source)
        assert duplicate['outcome']=='duplicate'
        learning_summary={k:v for k,v in meta.items() if k!='quality_flags'}
        learning_summary['quality_flag_counts']={code:sum(f['code']==code for f in meta['quality_flags'])
                                                for code in sorted({f['code'] for f in meta['quality_flags']})}
        learning_summary['duplicate_import']=duplicate['outcome']
        learning_summary['families_auto_created']=learning.store.connection.execute('SELECT count(*) FROM families').fetchone()[0]
        save('learning-import.json',learning_summary)
    finally:
        learning.close()

files={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
       for p in sorted((root/'backend').rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
manifest={'run_id':run_id,'host':platform.node(),'platform':platform.platform(),'python':platform.python_version(),
          'project_root':str(root),'state_root':str(state),'evidence_root':str(out),
          'source_sha256':files,'real_model_used':False,'real_wechat_used':False,
          'learning_imported':learning_summary is not None}
save('manifest.json',manifest)
(root/'evaluations'/'backend-stage1'/'latest.json').write_text(json.dumps({'run_id':run_id,'evidence_root':str(out),'state_root':str(state)},indent=2)+'\n')
print(json.dumps({'run_id':run_id,'evidence_root':str(out),'state_root':str(state),'verified':True},ensure_ascii=False))
