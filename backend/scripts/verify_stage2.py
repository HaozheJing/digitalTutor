import json
from pathlib import Path
from datetime import datetime, timezone
import hashlib
from digital_tutor.simulation import Scenario

root=Path.cwd(); run=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
out=root/'evaluations/backend-stage2'/run; out.mkdir(parents=True)
state=root/'state/backend-stage2'/run; state.mkdir(parents=True)
result=Scenario(state/'business.sqlite').run()
tests=json.loads((root/'evaluations/backend-stage2/tests-development.json').read_text())
assert tests['successful'] and not tests['skipped']
for name,data in [('scenario.json',result),('tests.json',tests)]:
    (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
manifest={'run_id':run,'project_root':str(root),'state_root':str(state),'real_wechat_used':False,
          'source_sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (root/'backend').rglob('*') if p.is_file() and '__pycache__' not in p.parts}}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(out.parent/'latest.json').write_text(json.dumps({'run_id':run,'evidence_root':str(out),'state_root':str(state)},indent=2)+'\n')
print(json.dumps({'run_id':run,'verified':True,'tests':tests['tests_run'],'transport_operations':result['transport_operations']}))
