#!/usr/bin/env bash
set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
"$BASE/runtime/supervisor/bin/supervisorctl" -c "$BASE/config/supervisord.conf" status
"$BASE/runtime/openviking/bin/python" "$BASE/scripts/status.py"
