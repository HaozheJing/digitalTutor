set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
export UV_CACHE_DIR="$BASE/cache/uv" UV_PYTHON_INSTALL_DIR="$BASE/runtime/python"
cd "$BASE/vendor/hermes"
"$BASE/bin/uv" export --frozen --no-dev --extra web --extra mcp --format requirements-txt --output-file "$BASE/docs/hermes-locked-requirements.txt" >/dev/null
head -n 8 "$BASE/docs/hermes-locked-requirements.txt"
