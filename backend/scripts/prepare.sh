#!/usr/bin/env bash
set -euo pipefail
cd /root/autodl-tmp/digitalTutor
umask 077
if [ ! -x runtime/backend/bin/python ]; then
  bin/uv venv --python runtime/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3 runtime/backend
fi
requirements=backend/requirements.txt
if [ -f backend/requirements.lock ]; then requirements=backend/requirements.lock; fi
UV_CACHE_DIR="$PWD/cache/uv" bin/uv pip install --python runtime/backend/bin/python -r "$requirements"
runtime/backend/bin/python -c 'import sys,sqlite3,pydantic,openpyxl; print({"python":sys.version.split()[0],"sqlite":sqlite3.sqlite_version,"pydantic":pydantic.__version__,"openpyxl":openpyxl.__version__})'
PYTHONPATH=backend/src runtime/backend/bin/python -c 'from digital_tutor.application import Application; a=Application(":memory:"); a.close(); print("backend imports and migration OK")'
