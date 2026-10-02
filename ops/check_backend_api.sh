set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
source "$BASE/config/runtime.env"
cd "$BASE/vendor/hermes"
"$BASE/vendor/hermes/venv/bin/python" - <<'PY'
import asyncio,json,pathlib
from urllib.parse import quote
from gateway import host_rendezvous as hr
import websockets
record=hr.read_record(hr.ROLE_SERVE)
token=hr.read_token(hr.ROLE_SERVE)
assert record and token,'Missing backend auth record'
async def check():
 async with websockets.connect('ws://127.0.0.1:9119/api/ws?token='+quote(token),origin='http://127.0.0.1:9119') as ws:
  payload=json.loads(await asyncio.wait_for(ws.recv(),15))
  assert payload.get('method')=='event' and payload.get('params',{}).get('type')=='gateway.ready',str(payload)[:200]
  print('Hermes authenticated WebSocket backend: gateway.ready PASS')
  pathlib.Path('/root/autodl-tmp/digitalTutor/docs/hermes-api-smoke.json').write_text(json.dumps({'websocket_ready':True}))
asyncio.run(check())
PY
