set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
# Stop only our previously identified, stalled installer download.
if [ -r /proc/63919/cmdline ] && tr '\0' ' ' < /proc/63919/cmdline | grep -Fq "$BASE/tmp/uv-install.sh"; then kill 63919; fi
export TMPDIR="$BASE/tmp"
export PIP_CACHE_DIR="$BASE/cache/pip"
/root/miniconda3/bin/python -m pip install --target "$BASE/runtime/uv-package" uv --timeout 30 --retries 2
ln -sf "$BASE/runtime/uv-package/bin/uv" "$BASE/bin/uv"
"$BASE/bin/uv" --version
