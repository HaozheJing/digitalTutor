set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
source "$BASE/config/runtime.env"
"$BASE/runtime/openviking/bin/python" - <<'PY'
import pathlib
p=pathlib.Path('/root/autodl-tmp/digitalTutor/tmp/hermes-smoke-query.txt')
p.write_text('这是系统部署验收，不是真实家庭咨询。请调用 viking_search 检索 viking://resources/deployment-smoke 中的部署验收代号，必要时调用 viking_read，最后只回复检索出的代号。不要凭空猜测，不要使用其他工具。',encoding='utf-8')
PY
"$BASE/bin/hermes" chat --query-file "$BASE/tmp/hermes-smoke-query.txt" --oneshot --format stream-json --max-turns 5 --run-budget 120 > "$BASE/docs/hermes-smoke.jsonl" 2> "$BASE/logs/hermes-smoke.log"
"$BASE/runtime/openviking/bin/python" - <<'PY'
import pathlib,json
p=pathlib.Path('/root/autodl-tmp/digitalTutor/docs/hermes-smoke.jsonl')
for line in p.read_text(errors='replace').splitlines():
 try:
  event=json.loads(line)
  print(json.dumps(event,ensure_ascii=False)[:1500])
 except json.JSONDecodeError:print(line[:400])
PY
