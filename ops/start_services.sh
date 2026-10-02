#!/usr/bin/env bash
set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
source "$BASE/config/runtime.env"
CTL="$BASE/runtime/supervisor/bin/supervisorctl"
CONF="$BASE/config/supervisord.conf"
if ! "$CTL" -c "$CONF" pid >/dev/null 2>&1; then
  "$BASE/runtime/supervisor/bin/supervisord" -c "$CONF"
fi
for i in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:1933/ready >/dev/null 2>&1; then
    "$BASE/runtime/openviking/bin/python" "$BASE/scripts/provision.py"
    "$BASE/runtime/openviking/bin/python" "$BASE/scripts/configure.py"
    "$CTL" -c "$CONF" start hermes || { "$CTL" -c "$CONF" status hermes | grep -q RUNNING; }
    "$CTL" -c "$CONF" status
    exit 0
  fi
  sleep 2
done
printf 'OpenViking readiness timeout; inspect logs/openviking.log\n' >&2
exit 1
