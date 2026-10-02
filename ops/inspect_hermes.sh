set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
"$BASE/runtime/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11" "$BASE/scripts/configure.py"
"$BASE/bin/hermes" --version
"$BASE/bin/hermes" chat --help | head -n 65
"$BASE/bin/hermes" memory --help
