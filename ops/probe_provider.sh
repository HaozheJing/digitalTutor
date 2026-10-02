set -euo pipefail
BASE=/root/autodl-tmp/digitalTutor
/root/miniconda3/bin/python - <<'PY'
import json, urllib.request, urllib.error, pathlib
p=pathlib.Path('/root/autodl-tmp/digitalTutor')
c=json.loads((p/'config/provider.json').read_text())
results={}
for kind, body in [('chat/completions', {'model':c['small_llm_name'],'messages':[{'role':'user','content':'Reply with OK only.'}],'max_tokens':16}),('embeddings',{'model':c['embedding_name'],'input':['阅读兴趣和小动物']})]:
 try:
  req=urllib.request.Request(c['base_url'].rstrip('/')+'/'+kind,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+c['api_key'],'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=45) as r: data=json.load(r)
  if kind=='embeddings':
   dim=len(data['data'][0]['embedding']); results['embedding_dimension']=dim; print('Embedding OK, dimension:',dim)
  else: results['chat_ok']=True; print('Chat OK, model:', data.get('model'), 'reply:',data['choices'][0]['message'].get('content'))
 except Exception as e:
  # Avoid printing request headers or credentials.
  print(kind, 'FAILED',type(e).__name__, getattr(e,'code',None))
  if isinstance(e,urllib.error.HTTPError):
   text=e.read().decode(errors='replace').replace(c['api_key'],'[REDACTED]'); print(text[:800])
(p/'docs/provider-probe.json').write_text(json.dumps(results,indent=2))
PY
