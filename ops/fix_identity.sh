set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
"$BASE/runtime/openviking/bin/python" "$BASE/scripts/provision.py"
"$BASE/runtime/openviking/bin/python" "$BASE/scripts/configure.py"
"$BASE/runtime/supervisor/bin/supervisorctl" -c "$BASE/config/supervisord.conf" restart hermes
source "$BASE/config/runtime.env"
"$BASE/runtime/openviking/bin/python" "$BASE/scripts/smoke_openviking.py"
grep -n -E 'viking_search|openviking|"memory"' "$BASE/vendor/hermes/toolsets.py" | head -n 35 || true
