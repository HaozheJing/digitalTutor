#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/../.."
export PYTHONPATH="$PWD/backend/src"
PYTHON="$PWD/runtime/backend/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo 'Run bash backend/scripts/prepare.sh first.' >&2
  exit 1
fi
mkdir -p evaluations/backend-stage1
args=(--root "$PWD")
if [ -f data/表格数据.xlsx ]; then
  export DIGITALTUTOR_TEST_WORKBOOK="$PWD/data/表格数据.xlsx"
  args+=(--workbook "$DIGITALTUTOR_TEST_WORKBOOK")
fi
"$PYTHON" backend/scripts/test_stage1.py --output evaluations/backend-stage1/tests.json
"$PYTHON" backend/scripts/verify_stage1.py "${args[@]}"
