set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
source "$BASE/config/runtime.env"
"$BASE/runtime/openviking/bin/python" - <<'PY'
import json,pathlib
from openviking import SyncHTTPClient
base=pathlib.Path('/root/autodl-tmp/digitalTutor')
key=(base/'config/openviking-user-key').read_text().strip()
client=SyncHTTPClient(url='http://127.0.0.1:1933',api_key=key)
try:
 client.initialize()
 result=client.find(query='部署验收代号',target_uri='viking://resources/deployment-smoke',limit=1)
 assert result.get('resources') and '海豚星' in result['resources'][0]['abstract']
finally:client.close()
print('Persisted knowledge retrieval after service restart: PASS')
text=(base/'docs/hermes-smoke.jsonl').read_text()
events=[]
for line in text.splitlines():
 try:events.append(json.loads(line))
 except json.JSONDecodeError:pass
assert any(x.get('type')=='tool_result' and x.get('name')=='viking_search' and not x.get('is_error') for x in events)
assert any(x.get('type')=='result' and x.get('exit_code')==0 and x.get('text')=='海豚星' for x in events)
print('Recorded Hermes actual tool call and answer: PASS')
print('Credential permissions:', {str(p.relative_to(base)):oct(p.stat().st_mode & 0o777) for p in [base/'config/provider.json',base/'config/openviking-user-key',base/'config/openviking-api-key',base/'state/hermes/.env']})
PY
git -C "$BASE/vendor/hermes" rev-parse HEAD
du -sh "$BASE"
