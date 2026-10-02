set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
"$BASE/scripts/status.sh"
"$BASE/runtime/openviking/bin/python" - <<'PY'
import json,pathlib,platform,shutil
b=pathlib.Path('/root/autodl-tmp/digitalTutor')
p=json.loads((b/'config/provider.json').read_text())
ov=json.loads((b/'config/ov.conf').read_text())
print('Handover snapshot:',json.dumps({'platform':platform.platform(),'project_git_initialized':(b/'.git').exists(),'hermes_vendor_git':(b/'vendor/hermes/.git').exists(),'docker':shutil.which('docker'),'base_url':p['base_url'],'active_understanding_model':ov['vlm']['model'],'embedding_model':ov['embedding']['dense']['model'],'embedding_dimension':ov['embedding']['dense']['dimension']},ensure_ascii=False))
PY
