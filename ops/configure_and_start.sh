set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
"$BASE/runtime/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11" "$BASE/scripts/configure.py"
source "$BASE/config/runtime.env"
"$BASE/bin/hermes" --version
"$BASE/bin/hermes" memory status
"$BASE/runtime/openviking/bin/python" -c 'import importlib.metadata; print("OpenViking",importlib.metadata.version("openviking"))'
"$BASE/bin/uv" pip check --python "$BASE/vendor/hermes/venv/bin/python"
"$BASE/bin/uv" pip check --python "$BASE/runtime/openviking/bin/python"
"$BASE/scripts/start.sh"
