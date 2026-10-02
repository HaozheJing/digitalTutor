set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
"$BASE/scripts/stop.sh"
for i in $(seq 1 30); do
  if [ ! -S "$BASE/state/supervisor.sock" ]; then break; fi
  sleep 1
done
"$BASE/scripts/start.sh"
sleep 3
"$BASE/scripts/status.sh"
