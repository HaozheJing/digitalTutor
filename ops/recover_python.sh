set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
if [ -r /proc/64074/cmdline ] && tr '\0' ' ' < /proc/64074/cmdline | grep -Fq 'uv python install 3.11'; then kill 64071 64074 2>/dev/null || true; fi
echo 'f05084f3fbd7a0c7ed762ea6fd2455976a463952bb5c3253fc6cdc777f84728d  /root/autodl-tmp/digitalTutor/tmp/python311.tar.gz' | sha256sum -c -
mkdir -p "$BASE/runtime/python/cpython-3.11.16-linux-x86_64-gnu"
tar xzf "$BASE/tmp/python311.tar.gz" --strip-components=1 -C "$BASE/runtime/python/cpython-3.11.16-linux-x86_64-gnu"
"$BASE/runtime/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11" --version
