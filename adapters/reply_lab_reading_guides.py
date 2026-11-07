"""Preview only an authenticated run's pinned S01 original image."""
import base64,hashlib,json,re,sqlite3
from pathlib import Path
from contextlib import closing
from reply_lab import require

def preview(lab,actor,data,run,part):
    match=re.fullmatch(r'https://experiment\.invalid/reading-guide/(exp-[a-f0-9]{32})/([a-f0-9]{64})\.(png|jpg|jpeg)',part['body'])
    require(match is not None,'导读单图片引用无效',409);experiment,sha,ext=match.groups()
    workspace=lab.home/'runs'/run['id']/'source/runtime/experiments'/experiment/'workspace'
    require(workspace.resolve()==workspace,'图片工作区不能重定向',409)
    db=workspace/'runtime/live.sqlite';require(db.resolve()==db and str(db)==run['result']['assembly']['writable_paths']['business_db'],'图片业务库不符',409)
    manifest=json.loads((workspace/'config/experiment.json').read_text());require(manifest['experiment_run_id']==experiment,'图片运行不符',409)
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as c:
        rows=c.execute('SELECT card FROM s01_guide_deliveries WHERE run_id=? AND public_url=? AND sha256=?',(manifest['business_run_id'],part['body'],sha)).fetchall()
    require(len(rows)==1,'缺少唯一导读单交付记录',409);asset=json.loads(rows[0][0]);path=workspace/asset['path']
    require(path.resolve()==path and path.is_relative_to(workspace/'assets/reading-guides') and path.is_file() and path.stat().st_size==asset['size'] and asset['size']<=24*1024*1024,'原图路径或大小不符',409)
    raw=path.read_bytes();require(hashlib.sha256(raw).hexdigest()==sha,'原图哈希不符',409)
    return {'mime':asset['mime'],'file_base64':base64.b64encode(raw).decode(),'sha256':sha,'asset_id':asset['id'],'part_id':part['part_id'],'status':part['status'],'simulation':True}
