set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
"$BASE/scripts/status.sh"
"$BASE/runtime/openviking/bin/python" - <<'PY'
import inspect, openviking
print('Client classes:',[name for name in dir(openviking) if 'Client' in name])
from openviking import SyncHTTPClient
print('Client init',inspect.signature(SyncHTTPClient))
for name in ['initialize','add_resource','find','search','rm','session','close']:
 if hasattr(SyncHTTPClient,name):print(name,inspect.signature(getattr(SyncHTTPClient,name)))
PY
rg -n 'viking_search|openviking|"memory"' "$BASE/vendor/hermes/toolsets.py" | head -n 30
