"""Complete offline regression entry point; no silently missing private fixture."""
from pathlib import Path
import os,sys,subprocess,json,hashlib
root=Path(__file__).resolve().parents[1]
fixture=root/'runtime/fixtures/mock_uid_00001_first_month.json'
if not fixture.is_file():raise SystemExit('Required versioned test fixture missing; provision the approved synthetic fixture before running.')
expected=json.loads((root/'app/test-fixture-sha256.json').read_text())[fixture.name]
if hashlib.sha256(fixture.read_bytes()).hexdigest()!=expected:raise SystemExit('Fixture differs from reviewed regression baseline; review and version its expectations first.')
out=root/'runtime/test-results';out.mkdir(parents=True,exist_ok=True)
commands={'python':[sys.executable,'-B','-m','unittest','discover','-s','adapters','-p','test_*.py'],
          'node':[os.environ.get('NODE_BINARY','node'),'--test',*[str(p) for folder in ('app','adapters') for p in sorted((root/folder).glob('test_*.mjs'))]]}
results={}
for name,command in commands.items():
    with (out/(name+'.log')).open('w',encoding='utf8') as log:
        result=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    results[name]={'exit_code':result.returncode,'log':str(out/(name+'.log'))}
print(json.dumps(results))
raise SystemExit(int(any(x['exit_code'] for x in results.values())))
