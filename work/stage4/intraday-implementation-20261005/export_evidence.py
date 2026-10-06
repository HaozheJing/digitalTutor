"""Server-side immutable review export, keeping approved source bodies private."""
from pathlib import Path
import argparse,hashlib,json,sqlite3,sys,zipfile
parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);parser.add_argument('--label',required=True)
parser.add_argument('--case',action='append',help='Export only named closed cases while other isolated cases run.')
args=parser.parse_args();project=Path('/root/autodl-tmp/digitalTutor/work/stage4/4c-development-l')
sys.path.insert(0,str(project/'backend/scripts'));sys.path.insert(0,str(project/'backend/src'))
from accept_stage4_passive import safe
from digital_tutor.storage import encode
root=project/'evaluations/backend-stage4'/args.run
target=Path('/root/autodl-tmp/digitalTutor/work/stage4/intraday-implementation-20261005')/(args.label+'.zip')
assert root.is_dir() and not target.exists()
if args.case:
    assert all(any((root/case/name).exists() for name in ('summary.json','result.json')) for case in args.case)
def material_metadata(value):
    if isinstance(value,list):return [material_metadata(v) for v in value]
    if not isinstance(value,dict):return value
    return {k:({'server_retained':True,'sha256':hashlib.sha256(v.encode()).hexdigest(),'characters':len(v)}
               if k in {'content','body','text','full_text'} and isinstance(v,str) and len(v)>100 else material_metadata(v)) for k,v in value.items()}
def review(value):
    if isinstance(value,list):return [review(v) for v in value]
    if not isinstance(value,dict):return value
    if value.get('tool') in {'knowledge.read','skills.read'}:
        return {**value,'result':material_metadata(value.get('result')),
                'server_result_sha256':hashlib.sha256(json.dumps(value.get('result'),ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
    return {k:review(v) for k,v in value.items()}
manifest={}
with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED) as archive:
    for path in root.rglob('*.json'):
        if args.case and path.relative_to(root).parts[0] not in args.case:continue
        if path.name.startswith('assembly-'):continue
        raw=json.dumps(review(safe(json.loads(path.read_text()))),ensure_ascii=False,indent=2).encode()
        relative=path.relative_to(root).as_posix();archive.writestr(relative,raw)
        manifest[relative]=hashlib.sha256(raw).hexdigest()
    for case in sorted(p for p in root.iterdir() if p.is_dir()):
        if args.case and case.name not in args.case:continue
        if not any((case/name).exists() for name in ('summary.json','result.json')):continue
        for db in case.glob('*.sqlite'):
            if db.name=='channel.redacted.sqlite':continue
            archive.write(db,db.relative_to(root).as_posix())
        source=project/'state/backend-stage4'/args.run/case.name/'business.channel.sqlite'
        if source.exists():
            # Backup the independent ledger itself, never synthesize it from business operations.
            readonly=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
            export=case/'channel.redacted.sqlite'
            if not export.exists():
                connection=sqlite3.connect(export);readonly.backup(connection)
                connection.execute('PRAGMA journal_mode=DELETE');connection.execute('PRAGMA secure_delete=ON')
                for operation_id,payload in connection.execute('SELECT operation_id,payload FROM delivery').fetchall():
                    connection.execute('UPDATE delivery SET payload=? WHERE operation_id=?',(encode(safe(json.loads(payload))),operation_id))
                connection.commit();connection.execute('VACUUM')
                assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok';connection.close()
            readonly.close();archive.write(export,export.relative_to(root).as_posix())
    archive.writestr('export-manifest.json',json.dumps(manifest,indent=2))
print(json.dumps({'review_archive':str(target),'json_files':len(manifest)}))
