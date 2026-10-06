"""Collect frozen-version evidence without sending real learner data to a model."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from digital_tutor.application import Application
from digital_tutor.learning import import_workbook, PUBLIC_SCHEMA
from digital_tutor.tools import SPECS

parser=argparse.ArgumentParser();parser.add_argument('--run',required=True)
args=parser.parse_args();assert args.run.replace('-','').replace('_','').isalnum()
root=Path.cwd(); out=root/'evaluations/backend-stage3'/args.run
source_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (root/'backend').rglob('*.py') if '__pycache__' not in p.parts}
tests=json.loads((out/'tests.json').read_text());assert tests['successful'] and not tests['skipped']
models=[json.loads((out/(case+'.json')).read_text()) for case in ('a1','b','a2','daily','progress','cancel')]
assert all(r['passed'] and r['source_sha256']==source_hashes for r in models)
assert len({r['native']['profile'] for r in models})==len(models)
assert len({r['native']['cwd'] for r in models})==len(models)
materials=json.loads((out/'native-materials.json').read_text());assert materials['passed']
cron=json.loads((root/'evaluations/backend-stage3/native-cron-20261003T143111Z-f1e33703.json').read_text())
assert cron['successful'] and cron['business_operations']==0
assert all(hashlib.sha256((root/path).read_bytes()).hexdigest()==hashed for path,hashed in cron['source_sha256'].items())
workbook=root/'data/表格数据.xlsx'; original=hashlib.sha256(workbook.read_bytes()).hexdigest()
app=Application(root/'state/backend-stage3'/args.run/'real-learning-import.sqlite')
try:
    first=import_workbook(app,workbook); second=import_workbook(app,workbook)
    assert second['outcome']=='duplicate' and hashlib.sha256(workbook.read_bytes()).hexdigest()==original
    assert app.store.connection.execute('SELECT count(*) FROM families').fetchone()[0]==0
    real={'source_sha256':original,'source_unchanged':True,'second_import':'duplicate',
          'automatic_family_binding':False,'sent_to_model':False,
          **{k:first[k] for k in ('user_count','learning_day_count','assignment_count','source_sheet_count','source_row_count')},
          'quality_flags':dict(Counter(f['code'] for f in first['quality_flags'])),
          'unknown_fields':['course_binding','source_timezone','coverage_through'],
          'sql_tables':list(PUBLIC_SCHEMA)}
finally:
    app.close()
revision=subprocess.check_output(['git','-C',str(root/'vendor/hermes'),'rev-parse','HEAD'],text=True).strip()
vendor_status=subprocess.check_output(['git','-C',str(root/'vendor/hermes'),'status','--porcelain'],text=True)
assert not vendor_status.strip(), 'Vendor working tree changed'
tool_schema=[{'name':name,'roles':sorted(roles),'description':description,
              'input_schema':model.model_json_schema() if model else {'type':'object','properties':{},'additionalProperties':False}}
             for name,(roles,model,description) in sorted(SPECS.items())]
acceptance={'run_id':args.run,'verified_at':datetime.now(timezone.utc).isoformat(),
    'stage3_foundation_passed':True,'tests_run':tests['tests_run'],
    'real_model_cases':{r['case']:r['checks'] for r in models},'native_model_runs':len(models),
    'tool_count':len(SPECS),'hermes_revision':revision,'vendor_unmodified':True,
    'native_material_read':materials['passed'],'native_cron':cron['run_id'],
    'internal_materials_model_evaluation': 'not_run_pending_authorization',
    'real_wechat_used':False,'real_learner_data_sent_to_model':False,
    'stage4_full_two_day_model_service':'not_started',
    'remaining_business_inputs':['real_course_binding','source_timezone','coverage_through','production_contact_limits'],
    'state_root':str(root/'state/backend-stage3'/args.run)}
manifest={'source_sha256':source_hashes,'hermes_revision':revision,
          'artifact_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.json')}}
for name,value in [('learning-import.json',real),('tools.json',tool_schema),('acceptance.json',acceptance),('manifest.json',manifest)]:
    (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'passed':True,'tests':tests['tests_run'],'model_cases':len(models),'tools':len(SPECS),'run_id':args.run}))
