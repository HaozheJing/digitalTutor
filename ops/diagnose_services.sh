BASE=/root/autodl-tmp/digitalTutor
"$BASE/runtime/openviking/bin/python" - <<'PY'
import json,pathlib
base=pathlib.Path('/root/autodl-tmp/digitalTutor')
keys=[json.loads((base/'config/provider.json').read_text())['api_key'],(base/'config/openviking-api-key').read_text().strip()]
for file in [base/'logs/openviking.log',base/'logs/hermes.log']:
 if file.exists():
  text='\n'.join(file.read_text(errors='replace').splitlines()[-55:])
  for key in keys:text=text.replace(key,'[REDACTED]')
  print('Log:',file,'\n',text)
PY
ss -lntp | grep -E ':(1933|9119)\b' || true
