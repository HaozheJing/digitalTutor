"""Authenticated previews of a run's already delivered local generated media."""
import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from reply_lab import require


def preview(lab, actor, data):
    run = lab.run(actor, data['id'])  # Includes owner and immutable-result checks.
    result = run.get('result') or {}
    require(result.get('native_verified') is True and result.get('simulation') is True,
            '此运行没有可核对的隔离图片记录。', 409)
    parts = [p for p in result.get('parts', []) if p.get('part_id') == data.get('part_id')]
    if len(parts)==1 and parts[0].get('message_type')==1:return __import__('reply_lab_files').preview(lab,actor,data)
    require(len(parts) == 1 and parts[0].get('message_type') == 6 and parts[0].get('simulation') is True,
            '图片分段不存在于此运行。', 404)
    part = parts[0]
    if str(part.get('body','')).startswith('https://experiment.invalid/reading-guide/'):
        return __import__('reply_lab_reading_guides').preview(lab,actor,data,run,part)
    match = re.fullmatch(r'https://experiment\.invalid/festival-media/(exp-[a-f0-9]{32})/([a-f0-9]{64})\.png', str(part.get('body', '')))
    require(match is not None, '此图片来源尚不支持本地预览；请查看运行记录。', 409)
    experiment, expected_sha = match.groups()
    folder = lab.home / 'runs' / run['id']
    require(folder.resolve() == folder and folder.parent == lab.home / 'runs', '运行目录越界。', 409)
    workspace = folder / 'source/runtime/experiments' / experiment / 'workspace'
    require(workspace.resolve() == workspace, '图片工作区不能重定向。', 409)
    db = workspace / 'runtime/live.sqlite'
    require(db.is_file() and db.resolve() == db and
            result.get('assembly', {}).get('writable_paths', {}).get('business_db') == str(db),
            '图片业务库与运行绑定不符。', 409)
    manifest_path = workspace / 'config/experiment.json'
    require(manifest_path.resolve() == manifest_path and manifest_path.is_file(), '图片运行清单缺失。', 409)
    manifest = json.loads(manifest_path.read_text())
    require(manifest.get('experiment_run_id') == experiment, '图片运行标识不符。', 409)
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        require(c.execute("SELECT 1 FROM sqlite_master WHERE name='festival_images'").fetchone(), '没有生成图片登记。', 404)
        rows = c.execute('SELECT * FROM festival_images WHERE public_url=? AND sha256=? AND run_id=?',
                         (part['body'], expected_sha, manifest.get('business_run_id'))).fetchall()
    require(len(rows) == 1 and rows[0]['inspection'], '图片缺少匹配的生成及验图记录。', 409)
    asset = rows[0]
    path = Path(asset['path'])
    media = workspace / 'artifacts/media'
    require(path.is_absolute() and path.resolve() == path and path.is_relative_to(media) and
            path.suffix == '.png' and path.is_file(), '图片不属于当前运行的素材目录。', 409)
    require(path.stat().st_size <= 24 * 1024 * 1024, '图片过大，无法预览。', 409)
    raw = path.read_bytes()
    require(raw.startswith(b'\x89PNG\r\n\x1a\n') and hashlib.sha256(raw).hexdigest() == expected_sha,
            '图片内容哈希校验失败。', 409)
    return {'mime': 'image/png', 'file_base64': base64.b64encode(raw).decode(),
            'sha256': expected_sha, 'part_id': part['part_id'], 'status': part['status'],
            'asset_id': asset['asset_id'], 'inspection': asset['inspection'], 'simulation': True}
