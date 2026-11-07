"""Bind a pinned, reviewed emoji registry and credential before native startup."""
from pathlib import Path
import json,hashlib,os,shutil

def bind(root,operator,config):
 root=Path(root)
 if config.get('emoji_enabled') is False:return {'status':'disabled_explicitly'}
 file=root/'config/emoji-assets.json'
 if not file.is_file():
  if operator.get('emoji_ovcli_config') or config.get('emoji_enabled'):raise ValueError('emoji_assets_missing_before_startup')
  return {'status':'not_configured'}
 raw=file.read_bytes();registry=json.loads(raw)
 expected=config.get('emoji_registry_sha256')
 if expected and hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('emoji_registry_digest_mismatch')
 if not registry.get('assets') or not registry.get('retrieval'):raise ValueError('reviewed_emoji_retrieval_package_required')
 for asset in registry['assets']:
  if asset.get('status')!='approved' or not asset.get('knowledge') or not asset.get('reviewed_by'):raise ValueError('unreviewed_emoji_asset')
 source=Path(operator.get('emoji_ovcli_config',''))
 if not source.is_file():raise ValueError('emoji_openviking_config_missing')
 target=root/'runtime/emoji-ovcli.conf';target.parent.mkdir(parents=True,exist_ok=True)
 temporary=target.with_suffix('.tmp');shutil.copyfile(source,temporary);temporary.chmod(0o600);os.replace(temporary,target)
 return {'status':'ready','registry_version':registry['version'],'count':len(registry['assets']),'registry_sha256':hashlib.sha256(raw).hexdigest()}
