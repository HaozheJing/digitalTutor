"""Preview only the exact pinned PDF recorded in an owned simulated run."""
import base64,hashlib,json,re,sqlite3
from contextlib import closing
from pathlib import Path
from reply_lab import require

def preview(lab,actor,data):
    run=lab.run(actor,data['id']);result=run.get('result') or {}
    require(result.get('native_verified') is True and result.get('simulation') is True,'没有隔离运行记录',409)
    parts=[x for x in result.get('parts',[]) if x.get('part_id')==data.get('part_id') and x.get('message_type')==1 and x.get('simulation') is True]
    require(len(parts)==1,'文件分段不存在',404);part=parts[0]
    payload=json.loads(part['body'])
    match=re.fullmatch(r'https://experiment\.invalid/weekly-material/(exp-[a-f0-9]{32})/([a-f0-9]{64})\.pdf',payload.get('url',''))
    require(match is not None,'文件来源不符',409);experiment,sha=match.groups()
    folder=lab.home/'runs'/run['id'];workspace=folder/'source/runtime/experiments'/experiment/'workspace'
    require(folder.resolve()==folder and folder.parent==lab.home/'runs' and workspace.resolve()==workspace,'运行目录越界',409)
    db=workspace/'runtime/live.sqlite'
    require(db.is_file() and db.resolve()==db and result.get('assembly',{}).get('writable_paths',{}).get('business_db')==str(db),'业务库绑定不符',409)
    manifest=json.loads((workspace/'config/experiment.json').read_text())
    require(manifest.get('experiment_run_id')==experiment,'运行标识不符',409)
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as c:
        rows=c.execute('SELECT w.asset_id FROM weekly_material_deliveries w JOIN test_outbox_parts p ON w.action_key=p.action_key JOIN test_outbox o ON p.operation_id=o.id WHERE w.run_id=? AND o.id=? AND o.body=? AND o.message_type=1',(manifest['business_run_id'],part['part_id'],part['body'])).fetchall()
    require(len(rows)==1,'无匹配文件交付登记',409)
    assets=[x for x in json.loads((workspace/'adapters/weekly_materials.json').read_text())['assets'] if x['id']==rows[0][0] and x['sha256']==sha]
    require(len(assets)==1,'文件版本不符',409);asset=assets[0];path=workspace/asset['path']
    require(path.resolve()==path and path.is_relative_to(workspace/'assets/weekly-review') and path.is_file() and path.stat().st_size==asset['size'] and asset['size']<=24*1024*1024,'文件路径或大小不符',409)
    raw=path.read_bytes();require(raw.startswith(b'%PDF-') and hashlib.sha256(raw).hexdigest()==sha and payload['name']==asset['name'] and payload['size']==asset['size'],'文件哈希或元数据不符',409)
    return {'mime':'application/pdf','name':asset['name'],'file_base64':base64.b64encode(raw).decode(),'sha256':sha,'part_id':part['part_id'],'status':part['status'],'simulation':True}
