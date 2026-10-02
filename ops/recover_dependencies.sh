set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
if [ -r /proc/64186/cmdline ] && tr '\0' ' ' < /proc/64186/cmdline | grep -Fq 'uv sync --frozen'; then kill 64177 64186 2>/dev/null || true; fi
export UV_CACHE_DIR="$BASE/cache/uv" UV_PYTHON_INSTALL_DIR="$BASE/runtime/python" TMPDIR="$BASE/tmp"
export XDG_CACHE_HOME="$BASE/cache" XDG_DATA_HOME="$BASE/state/xdg" XDG_CONFIG_HOME="$BASE/config/xdg"
export PATH="$BASE/bin:$PATH"
cd "$BASE/vendor/hermes"
sed '/^-e \.$/d' "$BASE/docs/hermes-locked-requirements.txt" > "$BASE/docs/hermes-locked-dependencies.txt"
uv pip sync --python "$BASE/vendor/hermes/venv/bin/python" --require-hashes --index-url http://mirrors.aliyun.com/pypi/simple "$BASE/docs/hermes-locked-dependencies.txt"
uv pip install --python "$BASE/vendor/hermes/venv/bin/python" --no-deps --index-url http://mirrors.aliyun.com/pypi/simple -e .
cd "$BASE"
if [ ! -x "$BASE/runtime/openviking/bin/python" ]; then uv venv --python 3.11 "$BASE/runtime/openviking"; fi
uv pip install --python "$BASE/runtime/openviking/bin/python" --index-url http://mirrors.aliyun.com/pypi/simple 'openviking==0.4.22'
if [ ! -x "$BASE/runtime/supervisor/bin/python" ]; then uv venv --python 3.11 "$BASE/runtime/supervisor"; fi
uv pip install --python "$BASE/runtime/supervisor/bin/python" --index-url http://mirrors.aliyun.com/pypi/simple 'supervisor==4.3.0'
uv pip freeze --python "$BASE/runtime/openviking/bin/python" > "$BASE/docs/openviking-requirements.txt"
uv pip freeze --python "$BASE/vendor/hermes/venv/bin/python" > "$BASE/docs/hermes-requirements.txt"
printf 'Installation complete\n'
