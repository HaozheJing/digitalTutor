#!/usr/bin/env bash
set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
"$BASE/runtime/supervisor/bin/supervisorctl" -c "$BASE/config/supervisord.conf" shutdown
