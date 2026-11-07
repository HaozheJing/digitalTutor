"""Publish only registered immutable acceptance assets on the owned static origin."""
from pathlib import Path
import hashlib,json,re
from urllib.request import urlopen
import sql_tools as s
BASE='https://82.156.162.52/festival-media/'
ROOT=Path('/var/www/digitaltutor-festival')
_lab=__import__('family_lab_bridge').configuration()
if _lab:
    BASE=_lab['public_media_base'];ROOT=Path(_lab['public_media_directory'])

def url(asset):
    suffix=Path(asset['path']).suffix.lower()
    if suffix=='.jpeg':suffix='.jpg'
    if suffix not in ('.png','.jpg','.pdf') or not re.fullmatch('[a-f0-9]{64}',asset['sha256']):raise ValueError('unsupported_registered_asset')
    return BASE+asset['sha256']+suffix

def publish(asset,raw):
    if not (_lab and __import__('family_lab_bridge').verify()) and not __import__('manual_workbench').verify():raise ValueError('manual_media_requires_bound_test_route')
    cfg={'public_base_url':BASE,'public_directory':str(ROOT)} if _lab else json.loads((s.R/'config/festival-media.json').read_text())
    if cfg!={'public_base_url':BASE,'public_directory':str(ROOT)}:raise ValueError('manual_media_origin_changed')
    if len(raw)!=asset['size'] or hashlib.sha256(raw).hexdigest()!=asset['sha256']:raise ValueError('manual_media_bytes_changed')
    target=ROOT/url(asset).split('/')[-1]
    if target.is_symlink() or target.parent.resolve()!=ROOT.resolve():raise ValueError('manual_media_path_invalid')
    ROOT.mkdir(exist_ok=True)
    if target.exists():
        if target.read_bytes()!=raw:raise ValueError('manual_media_immutable_conflict')
    else:
        with target.open('xb') as f:f.write(raw)
        target.chmod(0o644)
    if _lab:return url(asset)  # Central HTTP handler serves these exact immutable bytes.
    with urlopen(url(asset),timeout=30) as response:
        if response.geturl()!=url(asset):raise ValueError('manual_media_redirect_forbidden')
        observed=response.read(asset['size']+1)
    if observed!=raw:raise ValueError('manual_media_public_bytes_changed')
    return url(asset)
