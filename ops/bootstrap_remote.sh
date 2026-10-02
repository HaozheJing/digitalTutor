set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
umask 077
mkdir -p "$BASE"/{bin,vendor,runtime,cache,tmp,config,state,logs,scripts,docs}
export UV_CACHE_DIR="$BASE/cache/uv"
export UV_PYTHON_INSTALL_DIR="$BASE/runtime/python"
export XDG_CACHE_HOME="$BASE/cache"
export XDG_DATA_HOME="$BASE/state/xdg"
export XDG_CONFIG_HOME="$BASE/config/xdg"
export TMPDIR="$BASE/tmp"
export HERMES_HOME="$BASE/state/hermes"
export PATH="$BASE/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
if [ ! -x "$BASE/bin/uv" ]; then
  curl -fsSL --connect-timeout 10 --max-time 60 --retry 2 https://astral.sh/uv/install.sh -o "$BASE/tmp/uv-install.sh"
  UV_UNMANAGED_INSTALL="$BASE/bin" sh "$BASE/tmp/uv-install.sh"
fi
uv --version
uv python install 3.11
if [ ! -d "$BASE/vendor/hermes/.git" ]; then
  git clone --depth 1 --branch v2026.9.24 https://github.com/NousResearch/hermes-agent.git "$BASE/vendor/hermes"
fi
git -C "$BASE/vendor/hermes" describe --tags --always
cd "$BASE/vendor/hermes"
export UV_PROJECT_ENVIRONMENT="$BASE/vendor/hermes/venv"
uv sync --frozen --python 3.11 --extra web --extra mcp
cd "$BASE"
if [ ! -x "$BASE/runtime/openviking/bin/python" ]; then
  uv venv --python 3.11 "$BASE/runtime/openviking"
fi
uv pip install --python "$BASE/runtime/openviking/bin/python" 'openviking==0.4.22'
if [ ! -x "$BASE/runtime/supervisor/bin/python" ]; then
  uv venv --python 3.11 "$BASE/runtime/supervisor"
fi
uv pip install --python "$BASE/runtime/supervisor/bin/python" 'supervisor==4.3.0'
uv pip freeze --python "$BASE/runtime/openviking/bin/python" > "$BASE/docs/openviking-requirements.txt"
uv pip freeze --python "$BASE/vendor/hermes/venv/bin/python" > "$BASE/docs/hermes-requirements.txt"
printf 'Installation complete\n'
