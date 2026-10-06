"""Server-only bounded acceptance batches against one pinned L checkout."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import argparse,hashlib,json,os,subprocess,sys
parser=argparse.ArgumentParser();parser.add_argument('--suite',choices=['new','compat','ordinary-full','regression-final','all'],required=True)
parser.add_argument('--manifest',required=True)
parser.add_argument('--workers',type=int,choices=range(1,7),default=3)
parser.add_argument('--extra-compat',action='store_true',help='Also rerun family admission and legacy deadline behavior.')
parser.add_argument('--run',required=True);args=parser.parse_args()
project=Path('/root/autodl-tmp/digitalTutor/work/stage4/4c-development-l')
manifest=json.loads((project/'evaluations/backend-stage4'/args.manifest/'source-manifest.json').read_text())
assert all(hashlib.sha256((project/p).read_bytes()).hexdigest()==sha for p,sha in manifest.items())
out=project/'evaluations/backend-stage4'/args.run;out.mkdir(parents=True,exist_ok=False)
sys.path.insert(0,str(project/'backend/scripts'))
tasks=[]
ordinary_cases=('missing_guide','missing_policy','work_sample','memory_correction')
if args.suite in {'new','all','regression-final'}:
    tasks += [('accept_intraday.py',c) for c in ('daily-chain','intraday-empty','intraday-explicit','intraday-continuous')]
if args.extra_compat or args.suite=='regression-final':
    tasks += [('accept_stage4_daily.py','family_requests'),('accept_stage4_behavior.py','independent_need')]
if args.suite in {'ordinary-full','regression-final'}:
    tasks += [('accept_stage4_behavior.py',c) for c in ordinary_cases]
if args.suite in {'compat','all'}:
    from stage4_passive_fixtures import CASES as passive
    from stage4_behavior_fixtures import CASES as behavior
    from accept_stage4_daily import CASES as daily
    tasks += [('accept_stage4_passive.py',c) for c in passive]+[('accept_stage4_behavior.py',c) for c in behavior]+[('accept_stage4_daily.py',c) for c in daily]
env={**os.environ,'PYTHONPATH':str(project/'backend/src'),'PYTHONIOENCODING':'utf-8'}
def run(task):
    script,case=task;prefix=script.removeprefix('accept_').removesuffix('.py')
    name=args.run+'-'+prefix
    extra=['--runtime-mode','full'] if (args.suite=='ordinary-full' or
        (args.suite=='regression-final' and script=='accept_stage4_behavior.py' and case in ordinary_cases)) else []
    with (out/(prefix+'-'+case+'.log')).open('x',encoding='utf8') as log:
        child=subprocess.run([str(project/'runtime/backend/bin/python'),str(project/'backend/scripts'/script),
            '--run',name,'--case',case,'--budget-seconds','360',*extra],env=env,cwd=project,stdout=log,stderr=log,check=False)
    result={'script':script,'case':case,'run':name,'exit_code':child.returncode}
    print(json.dumps(result),flush=True);return result
results=[]
with ThreadPoolExecutor(max_workers=args.workers) as pool:
    for future in as_completed([pool.submit(run,t) for t in tasks]):
        results.append(future.result())
        (out/'processes.json').write_text(json.dumps(results,indent=2))
assert all(hashlib.sha256((project/p).read_bytes()).hexdigest()==sha for p,sha in manifest.items())
(out/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps({'suite':args.suite,'processes':len(results),'all_exit_zero':all(r['exit_code']==0 for r in results)}),flush=True)
