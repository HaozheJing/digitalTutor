"""Summarize immutable exported results; never rewrite their pass flags."""
from pathlib import Path
import json,argparse
base=Path(__file__).parent
parser=argparse.ArgumentParser();parser.add_argument('--group',action='append');parser.add_argument('--output')
args=parser.parse_args()
groups=args.group or ('native03-complete','compat01-passive-complete','compat01-behavior-complete','compat01-daily-complete')
cases=[]
for group in groups:
 for case in sorted((base/group).iterdir()):
  if not case.is_dir():continue
  p=next((case/n for n in ('summary.json','result.json') if (case/n).exists()),None)
  if not p:continue
  result=json.loads(p.read_text(encoding='utf8'))
  calls=[json.loads(p.read_text(encoding='utf8')) for p in case.glob('model-*.json')]
  checks=result.get('checks',result.get('program_checks',{}))
  cases.append({'group':group,'case':case.name,'native_runs':len(calls),
    'api_calls':sum(c['native'].get('api_calls') or 0 for c in calls),
    'all_flash':all(c['native'].get('model')=='qwen3.8-flash' for c in calls),
    'program_passed':result['program_passed'],'failed_checks':[k for k,v in checks.items() if v is False]})
out={'cases':cases,'case_count':len(cases),'native_runs':sum(c['native_runs'] for c in cases),
 'api_calls':sum(c['api_calls'] for c in cases),'raw_program_passed':sum(c['program_passed'] for c in cases),
 'all_flash':all(c['all_flash'] for c in cases),'gate_passed':False,
 'note':'Raw program results only; test-contract adjudications and independent semantic decisions are separate.'}
path=base/(args.output or 'test08-model-runs.json');assert not path.exists();path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in out.items() if k!='cases'},ensure_ascii=False))
