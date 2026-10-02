import json,pathlib,time
from openviking import SyncHTTPClient
base=pathlib.Path('/root/autodl-tmp/digitalTutor')
source=base/'tmp/deployment-smoke.md'
source.write_text('# 部署验收测试资料\n\n此文档仅用于系统部署验收，不是真实学生资料。\n部署验收代号是海豚星。\n测试记录日期为2026年10月2日。\n',encoding='utf-8')
key=(base/'config/openviking-user-key').read_text().strip()
client=SyncHTTPClient(url='http://127.0.0.1:1933',api_key=key)
try:
 client.initialize()
 result=client.add_resource(str(source),to='viking://resources/deployment-smoke',wait=True,timeout=120)
 print('ADD',json.dumps(result,ensure_ascii=False)[:1800],flush=True)
 found=client.find(query='部署验收代号是什么',target_uri='viking://resources/deployment-smoke',limit=3)
 print('FIND',json.dumps(found,ensure_ascii=False)[:2200],flush=True)
 (base/'docs/openviking-smoke.json').write_text(json.dumps({'add':result,'find':found},ensure_ascii=False,indent=2))
 assert found.get('resources'), 'Resource retrieval returned no matches'
 print('OpenViking ingestion and semantic retrieval PASS',flush=True)
finally: client.close()
