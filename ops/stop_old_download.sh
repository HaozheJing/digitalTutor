set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
if [ -r /proc/63952/cmdline ] && tr '\0' ' ' < /proc/63952/cmdline | grep -Fq "$BASE/tmp/uv-install.sh"; then
  kill 63917 63952 63994 2>/dev/null || true
fi
