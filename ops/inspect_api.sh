set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
grep -n -E '^def _ws_|token|Authorization|SESSION_HEADER|sec-websocket' "$BASE/vendor/hermes/hermes_cli/web_server_chat.py" | head -n 65
