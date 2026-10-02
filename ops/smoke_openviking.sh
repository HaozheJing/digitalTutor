set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
source "$BASE/config/runtime.env"
"$BASE/runtime/openviking/bin/python" "$BASE/scripts/smoke_openviking.py"
grep -n -E 'viking_search|openviking|"memory"' "$BASE/vendor/hermes/toolsets.py" | head -n 35 || true
