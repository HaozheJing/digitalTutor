"""Host-only, per-run media capabilities; never inherit a production channel."""
import hashlib
import json
import os
from pathlib import Path

ORIGIN = 'https://ark.cn-beijing.volces.com/api/plan/v3'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bind(workspace, operator):
    selected = operator.get('media')
    if selected is None:
        return None
    if set(selected) != {'credential_file', 'image_model', 'inspection_model'}:
        raise ValueError('invalid_media_capability')
    if any(not isinstance(selected[k], str) or not selected[k].strip() for k in selected):
        raise ValueError('invalid_media_capability')
    private = json.loads(Path(selected['credential_file']).read_text())
    if private.get('base_url') != ORIGIN or not private.get('api_key'):
        raise ValueError('media_plan_route_required')
    root = Path(workspace).resolve()
    home = root / 'runtime/media-capability'
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    if home.is_symlink() or not home.resolve().is_relative_to(root):
        raise ValueError('media_capability_path_invalid')
    credential = {'api_key':private['api_key'], 'base_url':ORIGIN,
                  'vlm_model':selected['inspection_model'],
                  'inspection_parameters':{'max_tokens':8192}}
    service = {'credential_file':str(home/'provider.json'),
               'asset_directory':str(root/'artifacts/media'), 'image_model':selected['image_model']}
    files = {'provider.json':credential, 'service.json':service}
    hashes = {}
    for name, value in files.items():
        data = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        target = home/name
        if target.is_symlink() or (target.exists() and target.read_bytes() != data):
            raise ValueError('media_capability_changed_on_restart')
        if not target.exists():
            with target.open('xb') as handle:handle.write(data)
        target.chmod(0o600)
        hashes[name] = sha(data)
    binding = json.dumps(hashes, sort_keys=True).encode()
    target = home/'binding.json'
    if target.is_symlink() or (target.exists() and target.read_bytes() != binding):
        raise ValueError('media_capability_changed_on_restart')
    target.write_bytes(binding);target.chmod(0o600)
    return {'sha256':sha(binding), 'image_model':selected['image_model'],
            'inspection_model':selected['inspection_model'], 'base_url':ORIGIN,
            'publication':'simulated_local_asset_only'}


def configuration(root, kind):
    root = Path(root).resolve()
    isolated = (root/'config/experiment.json').exists() or os.environ.get('TUTOR_EXPERIMENT_ID')
    if not isolated:
        path = root/'config'/('service-lab-media.json' if kind == 'service' else 'provider.json')
        return path if path.is_file() else None
    expected = os.environ.get('TUTOR_MEDIA_BINDING_SHA256')
    if not expected:
        return None
    home = root/'runtime/media-capability'
    manifest = home/'binding.json'
    if manifest.is_symlink() or not manifest.resolve().is_relative_to(root) or sha(manifest.read_bytes()) != expected:
        raise ValueError('media_capability_binding_changed')
    hashes = json.loads(manifest.read_text())
    if set(hashes) != {'service.json','provider.json'}:
        raise ValueError('media_capability_file_set_changed')
    for name, digest in hashes.items():
        path = home/name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or sha(path.read_bytes()) != digest:
            raise ValueError('media_capability_file_changed')
    service = json.loads((home/'service.json').read_text())
    if (Path(service['asset_directory']).resolve() != root/'artifacts/media'
            or Path(service['credential_file']).resolve() != home/'provider.json'):
        raise ValueError('media_capability_scope_changed')
    return home/('service.json' if kind == 'service' else 'provider.json')
