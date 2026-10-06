from pathlib import Path
import hashlib,json,sys,zipfile
root=Path.cwd(); source=root/'work/stage4/4c-development-l'
round_name=sys.argv[1]
out=root/'work/stage4/intraday-implementation-20261005'/round_name
out.mkdir(parents=True,exist_ok=False)
files=[p for folder in ('backend','agent') for p in (source/folder).rglob('*')
       if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']
manifest={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(out/'source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
with zipfile.ZipFile(out/'source.zip','x',zipfile.ZIP_DEFLATED) as z:
    for p in files:z.write(p,p.relative_to(source).as_posix())
relative=out.relative_to(root).as_posix()
script='''set -eu
cd /root/autodl-tmp/digitalTutor
runtime/backend/bin/python - <<'PY'
from pathlib import Path
import hashlib,json,zipfile
root=Path.cwd(); target=root/'work/stage4/4c-development-l'
bundle=root/ROUND
expected=json.loads((bundle/'source-manifest.json').read_text())
with zipfile.ZipFile(bundle/'source.zip') as z:
 for name in z.namelist():
  path=(target/name).resolve()
  assert path.is_relative_to(target.resolve()) and name in expected
  raw=z.read(name); assert hashlib.sha256(raw).hexdigest()==expected[name]
  path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
assert all(hashlib.sha256((target/name).read_bytes()).hexdigest()==sha for name,sha in expected.items())
out=target/'evaluations/backend-stage4'/ROUND_NAME;out.mkdir(parents=True,exist_ok=False)
(out/'source-manifest.json').write_text(json.dumps(expected,indent=2))
print(json.dumps({'synced_source_files':len(expected),'output':str(out)}))
PY
cd work/stage4/4c-development-l
export PYTHONPATH="$PWD/backend/src"
export PYTHONIOENCODING=utf-8
export DIGITALTUTOR_TEST_WORKBOOK="$PWD/data/表格数据.xlsx"
runtime/backend/bin/python -m unittest discover -s backend/tests -v > evaluations/backend-stage4/ROUND_NAME_TXT/tests.log 2>&1
tail -n 6 evaluations/backend-stage4/ROUND_NAME_TXT/tests.log
'''.replace('ROUND_NAME_TXT',round_name).replace('ROUND_NAME',repr(round_name)).replace('ROUND',repr(relative))
(out/'run.sh').write_text(script,encoding='utf-8')
print(json.dumps({'round':round_name,'source_files':len(files)}))
