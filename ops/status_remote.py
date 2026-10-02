import json,pathlib,urllib.request
base=pathlib.Path('/root/autodl-tmp/digitalTutor')
for name,url in [('openviking-live','http://127.0.0.1:1933/health'),('openviking-ready','http://127.0.0.1:1933/ready'),('hermes','http://127.0.0.1:9119/api/status')]:
 try:
  with urllib.request.urlopen(url,timeout=10) as r: data=json.load(r)
  print(name,json.dumps(data,ensure_ascii=False)[:1600])
 except Exception as e: print(name,type(e).__name__,getattr(e,'code',None))
