"""Approved, version-pinned emoji selections join the existing delivery manifest."""
import contextlib
import contextvars
import json
import re
from urllib.parse import urlsplit
import sql_tools as s

SELECTED = contextvars.ContextVar('emoji_selection', default=())


def catalog():
    file = s.R / 'config/emoji-assets.json'
    if not file.is_file(): return {'version': None, 'assets': []}
    registry = json.loads(file.read_text(encoding='utf8'))
    if registry.get('retrieval'):
        return {'version':registry['version'],'assets':[],'retrieval_required':True,
                'available_count':sum(a.get('status')=='approved' for a in registry.get('assets',[])),
                'policy':__import__('emoji_policy').availability(),
                'instruction':'适合使用表情时，调用knowledge_search(scope="emoji",query="本轮沟通场景")，阅读返回说明后自主选0或1张。'}
    cards = []
    for asset in registry.get('assets', []):
        if asset.get('status') != 'approved': continue
        ref = {'asset_id': asset['asset_id'], 'version': registry['version']}
        resolve([ref])
        cards.append({**ref, **{k: asset.get(k) for k in ['caption','visible_text','intents','suitable','avoid']}})
    return {'version': registry['version'], 'assets': cards, 'instruction': '可不选表情；选择时以emoji_refs附加在同一交付工具，不单独创建任务。'}


def resolve(refs):
    if not isinstance(refs, list) or len(refs) > 1:
        raise __import__('emoji_policy').PolicyViolation('emoji_quantity_limit: 一次交付最多一张表情')
    if not refs: return []
    file = s.R / 'config/emoji-assets.json'
    if not file.is_file(): raise ValueError('emoji_registry_unavailable')
    registry = json.loads(file.read_text(encoding='utf8'))
    assets = registry.get('assets', [])
    result = []
    for ref in refs:
        if set(ref) != {'asset_id', 'version'} or ref['version'] != registry.get('version'):
            raise ValueError('emoji_version_mismatch')
        matches = [a for a in assets if a.get('asset_id') == ref['asset_id']]
        if len(matches) != 1: raise ValueError('emoji_asset_unavailable')
        asset = matches[0];url = urlsplit(asset.get('url', ''))
        if (asset.get('status') != 'approved' or not asset.get('reviewed_by')
                or not re.fullmatch(r'[a-f0-9]{64}', asset.get('sha256', ''))
                or url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment or url.query
                or url.hostname not in registry.get('allowed_hosts', [])):
            raise ValueError('emoji_asset_not_approved')
        result.append({**ref, 'url': asset['url'], 'sha256': asset['sha256']})
    if len({x['asset_id'] for x in result}) != len(result): raise ValueError('duplicate_emoji_asset')
    return result


@contextlib.contextmanager
def selection(refs):
    token = SELECTED.set(resolve(refs))
    try: yield
    finally: SELECTED.reset(token)


def queue(c, action_key):
    refs = SELECTED.get()
    file=s.R/'config/emoji-assets.json'
    registry=json.loads(file.read_text()) if file.exists() else {}
    __import__('emoji_policy').register(c,action_key,refs,registry)
    if not refs: return
    c.execute('INSERT INTO test_delivery_emojis VALUES(?,?)', (action_key, s.encoded(refs)))


def check_existing(c, action_key):
    if not c.execute('SELECT 1 FROM deliveries WHERE action_key=?', (action_key,)).fetchone(): return
    old = c.execute('SELECT assets FROM test_delivery_emojis WHERE action_key=?', (action_key,)).fetchone()
    if (json.loads(old[0]) if old else []) != list(SELECTED.get()):
        raise ValueError('immutable_delivery_emoji_selection_changed')


def validate_part(c, key, body):
    # A type-5 caller cannot bypass asset_id resolution by supplying a URL.
    run = __import__('test_mode').state()['run_id']
    for action, raw in c.execute('SELECT action_key,assets FROM test_delivery_emojis'):
        for i, frozen in enumerate(json.loads(raw)):
            if key == run + ':' + action + ':emoji:' + str(i):
                __import__('emoji_policy').check(c,action,__import__('emoji_policy').owner(c,action)[1])
                current = resolve([{k: frozen[k] for k in ('asset_id', 'version')}])[0]
                if current != frozen or body != frozen['url']: raise ValueError('emoji_asset_changed')
                return
    raise ValueError('emoji_part_not_registered')
