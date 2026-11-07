"""Private reply experiments. No production model client, task store or sender.

The native runner is a trusted, server-side dependency owned by track B.
Without it only an explicitly labelled UI contract fixture can run.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import base64
import difflib
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import uuid

from skill_studio import Fault, now

MODES = {'passive', 'proactive_expression', 'planning', 'mixed'}
KINDS = {'config', 'scenario', 'asset'}
RUBRIC = ['理解', '判断', '连贯', '具体', '语感', '可信', '表情适配']


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def require(condition, message, status=400):
    if not condition:
        raise Fault(message, status)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        require(parsed.tzinfo is not None, '时间必须包含时区。')
        return parsed
    except (ValueError, TypeError):
        raise Fault('时间格式无效。') from None


class ReplyLab:
    def __init__(self, studio, runner=None):
        self.studio = studio
        self.home = studio.home / 'reply-lab'
        require(not self.home.is_symlink(), '实验目录不能是符号链接。', 409)
        self.home.mkdir(parents=True, exist_ok=True)
        require(self.home.resolve().is_relative_to(studio.home.resolve()), '实验目录越界。', 409)
        self.db = self.home / 'experiments.sqlite'
        self.runner = runner
        baseline = studio.root / 'docs/reply-quality-baseline.json'
        self.baseline = json.loads(baseline.read_text(encoding='utf8')) if baseline.exists() else {}
        # One process owns recovery and the serial worker. A second server must
        # not mark the first server's running experiments as interrupted.
        self.process_lock = (self.home / 'worker.lock').open('a+b')
        try:
            import fcntl
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            import msvcrt
            self.process_lock.write(b'0'); self.process_lock.flush(); self.process_lock.seek(0)
            try:
                msvcrt.locking(self.process_lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.process_lock.close()
                raise Fault('此运行根已有实验 worker。', 409) from None
        except OSError:
            self.process_lock.close()
            raise Fault('此运行根已有实验 worker。', 409) from None
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.RLock()
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS lab_versions(
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
              name TEXT NOT NULL, parent TEXT, payload TEXT NOT NULL,
              digest TEXT NOT NULL, note TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS lab_heads(
              owner TEXT, kind TEXT, name TEXT, version TEXT,
              PRIMARY KEY(owner,kind,name));
            CREATE TABLE IF NOT EXISTS lab_runs(
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
              envelope TEXT NOT NULL, digest TEXT NOT NULL, result TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS lab_reviews(
              id TEXT PRIMARY KEY, owner TEXT, mapping TEXT, payload TEXT, vote TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS lab_splits(
              owner TEXT, family TEXT, split TEXT, PRIMARY KEY(owner,family));
            CREATE TABLE IF NOT EXISTS lab_source_splits(
              owner TEXT, source TEXT, family TEXT, split TEXT, PRIMARY KEY(owner,source));
            CREATE TABLE IF NOT EXISTS lab_asset_disabled(
              owner TEXT, asset TEXT, created TEXT, PRIMARY KEY(owner,asset));
            ''')
            c.execute("UPDATE lab_runs SET status='interrupted', result=? WHERE status IN ('queued','running')",
                      (encoded({'error': '实验服务重启中断；保留记录，需新建运行，不重放外发。'}),))
            for row in c.execute("SELECT owner,payload FROM lab_versions WHERE kind='scenario'").fetchall():
                p = json.loads(row['payload'])
                for source in p.get('source_refs', []):
                    key = source.get('file_sha256')
                    if not key: continue
                    old = c.execute('SELECT family,split FROM lab_source_splits WHERE owner=? AND source=?', (row['owner'], key)).fetchone()
                    require(not old or (old['family'] == p['family_group'] and old['split'] == p['split']), '历史案例存在同源跨集合冲突。', 409)
                    c.execute('INSERT OR IGNORE INTO lab_source_splits VALUES(?,?,?,?)', (row['owner'], key, p['family_group'], p['split']))

    def default_config(self, actor):
        path = self.studio.root / 'config/reply-lab-default.json'
        if not path.exists():
            return None
        ident = json.loads(path.read_text(encoding='utf8'))['config_version']
        try:return self.version(actor, ident, 'config')['id']
        except Fault as error:
            if error.status==404:return None
            raise

    def close(self):
        self.pool.shutdown(wait=True)
        self.process_lock.close()

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def version(self, actor, ident, kind=None):
        with self.connect() as c:
            row = c.execute('SELECT * FROM lab_versions WHERE id=? AND owner=?', (ident, actor['id'])).fetchone()
        require(row is not None, '版本不存在或无权读取。', 404)
        row = dict(row)
        row['payload'] = json.loads(row['payload'])
        require(digest(row['payload']) == row['digest'], '版本校验失败。', 409)
        require(kind is None or row['kind'] == kind, '版本类型不匹配。')
        return row

    def versions(self, actor, kind):
        require(kind in KINDS, '版本类型无效。')
        with self.connect() as c:
            rows = [dict(r) for r in c.execute('SELECT id,name,parent,digest,note,created,payload FROM lab_versions WHERE owner=? AND kind=? ORDER BY rowid DESC', (actor['id'], kind))]
        for row in rows:
            p = json.loads(row.pop('payload'))
            if kind == 'scenario':
                row.update({key: p.get(key) for key in ['mode', 'split', 'origin', 'review', 'category', 'family_group']})
        return rows

    def save(self, actor, kind, name, payload, parent=None, note='', expected=None):
        require(kind in KINDS and isinstance(name, str) and 0 < len(name) <= 100, '名称或类型无效。')
        require(isinstance(payload, dict) and len(encoded(payload)) <= 700000, '版本内容无效或过大。')
        require(isinstance(note, str) and 0 < len(note.strip()) <= 500, '请填写版本说明。')
        if parent:
            prior = self.version(actor, parent, kind)
            require(prior['name'] == name, '父版本不属于当前版本分支。')
        ident = uuid.uuid4().hex
        with self.lock, self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            head = c.execute('SELECT version FROM lab_heads WHERE owner=? AND kind=? AND name=?', (actor['id'], kind, name)).fetchone()
            require((head['version'] if head else None) == expected, '版本已更新，请重新加载后保存。', 409)
            if kind == 'scenario':
                family, split = payload['family_group'], payload['split']
                old = c.execute('SELECT split FROM lab_splits WHERE owner=? AND family=?', (actor['id'], family)).fetchone()
                require(not old or old['split'] == split, '同一家庭及其变体不能跨数据集。', 409)
                c.execute('INSERT OR IGNORE INTO lab_splits VALUES(?,?,?)', (actor['id'], family, split))
                for source in payload.get('source_refs', []):
                    source_hash = source.get('file_sha256')
                    if not source_hash: continue
                    previous = c.execute('SELECT family,split FROM lab_source_splits WHERE owner=? AND source=?', (actor['id'], source_hash)).fetchone()
                    require(not previous or (previous['family'] == family and previous['split'] == split), '同源文件不能通过重命名家庭绕过划分。', 409)
                    c.execute('INSERT OR IGNORE INTO lab_source_splits VALUES(?,?,?,?)', (actor['id'], source_hash, family, split))
            c.execute('INSERT INTO lab_versions VALUES(?,?,?,?,?,?,?,?,?)', (ident, actor['id'], kind, name, parent, encoded(payload), digest(payload), note, now()))
            c.execute('INSERT OR REPLACE INTO lab_heads VALUES(?,?,?,?)', (actor['id'], kind, name, ident))
        return self.version(actor, ident)

    def scenario_save(self, actor, data):
        p = json.loads(encoded(data['payload']))
        require(p.get('mode') in MODES, '场景模式无效。')
        require(p.get('split') in {'examples', 'development', 'blind'}, '数据集划分无效。')
        require(isinstance(p.get('family_group'), str) and p['family_group'], '缺少家庭分组。')
        require(p.get('origin') in {'synthetic', 'real_redacted', 'real_transformed', 'historical_model'}, '必须标明来源类别。')
        require(p.get('review') in {'candidate', 'approved'}, '必须标明审核状态。')
        require(p.get('review') != 'approved' or bool(p.get('reviewer')), '审核通过需要具名审核人。')
        if p['review'] == 'approved':
            p['reviewer'] = {'id': actor['id'], 'name': actor['name']}
        require(p['origin'] == 'synthetic' or (p.get('source_refs') and p.get('transformations') is not None), '真实来源必须保留定位和加工记录。')
        if p['origin'] != 'synthetic':
            require(all(isinstance(s, dict) and re.fullmatch(r'[a-f0-9]{64}', s.get('file_sha256', '')) and s.get('sheet') and s.get('visible_rows') for s in p['source_refs']), '来源必须包含文件SHA256、工作表和可见行。')
        cutoff = timestamp(p['as_of'])
        for key in ['history', 'messages', 'facts', 'events']:
            require(isinstance(p.get(key, []), list), '场景记录必须为数组。')
        require(p.get('messages') or p.get('events'), '场景缺少消息或事件。')
        for m in p.get('history', []) + p.get('messages', []):
            require(m.get('role') in {'user', 'assistant'}, '发言角色未确认。')
            require(isinstance(m.get('content'), str) and len(m['content']) <= 10000, '消息格式错误。')
            require(timestamp(m['at']) <= cutoff, '可见消息晚于截断时点。')
        for fact in p.get('facts', []):
            require(fact.get('source') and timestamp(fact['known_at']) <= cutoff, '事实缺来源或泄漏未来。')
        p['schema_version'] = 1
        return self.save(actor, 'scenario', data['name'], p, data.get('parent'), data.get('note'), data.get('expected'))

    def freeze_skills(self, primary, overrides):
        require(isinstance(overrides, dict) and len(overrides) <= 64, 'Skill 候选格式无效。')
        bundle, queue = {}, [primary, 'reply-style']
        installed = set(self.studio.installed())
        require(set(overrides) <= installed, '不能加载未安装的 Skill。')
        queue.extend(overrides)
        while queue:
            name = queue.pop(0)
            if name in bundle:
                continue
            require(name in installed, '缺少依赖 Skill：' + name, 409)
            files = overrides.get(name, self.studio.source(name))
            from service_content import DISABLED_REFERENCES
            # Frozen bundles intentionally omit retired references. Validate a
            # saved bundle without demanding that those retired files reappear.
            validation = dict(files)
            disabled = DISABLED_REFERENCES.get(name, set())
            if disabled:
                source = self.studio.source(name)
                validation.update({k: source[k] for k in disabled if k in source and k not in validation})
            self.studio.validate(name, validation)
            files = {k: v for k, v in files.items() if k not in DISABLED_REFERENCES.get(name, set())}
            bundle[name] = files
            queue.extend(x for x in self.studio.dependencies(name) if x not in bundle)
            # Markdown Skill links supplement the installed dependency mapping.
            for body in files.values():
                queue.extend(x for x in re.findall(r'\.\./([a-z][a-z0-9-]+)/SKILL\.md', body) if x not in bundle)
            require(len(bundle) <= 64, 'Skill 依赖过多。')
        return bundle

    def config_save(self, actor, data):
        p = data['payload']
        require(isinstance(p.get('persona'), str) and 0 < len(p['persona']) <= 20000, 'Persona 不能为空或过长。')
        require(p.get('complete', False) is False, '保留原生系统提示与工具上下文。')
        require(isinstance(p.get('model'), str) and p['model'], '需要模型标识。')
        require(isinstance(p.get('parameters', {}), dict), '模型参数必须是对象。')
        from model_parameters import validate
        try: validate(p['model'], p.get('parameters', {}))
        except ValueError as exc: raise Fault(str(exc), 400) from None
        from reply_phases import pipeline
        try: phases=pipeline(p['model'], p.get('reply_pipeline'))
        except ValueError as exc: raise Fault(str(exc), 400) from None
        bundle = self.freeze_skills(p['primary_skill'], p.get('overrides', {}))
        examples = []
        for ident in p.get('examples', []):
            v = self.version(actor, ident, 'scenario')
            require(v['payload']['split'] == 'examples', '只有示例集可用于示例。')
            require(v['payload']['review'] == 'approved', '未人工审核的候选不能装配为示例。')
            examples.append({'version': ident, 'digest': v['digest'], 'family_group': v['payload']['family_group'],
                             'history': v['payload'].get('history', []), 'messages': v['payload']['messages'],
                             'reference': v['payload'].get('reference', '')})
        assets = [self.version(actor, ident, 'asset') for ident in p.get('assets', [])]
        for asset in assets:
            self.usable_asset(actor, asset)
        knowledge_index = self.studio.root/'runtime/emoji-knowledge-20261026/index.json'
        knowledge = json.loads(knowledge_index.read_text()) if knowledge_index.exists() else None
        frozen_assets=[]
        for asset in assets:
            item={'version':asset['id'],'digest':asset['digest'],'card':asset['payload']}
            if knowledge:
                doc=next((d for d in knowledge['documents'] if d['image_sha256']==asset['payload']['sha256'] and d['enabled']),None)
                require(doc is not None, '此表情没有对应的可用OpenViking场景卡。',409)
                item['knowledge']={**doc,'scope_uri':knowledge['uri'],'revision':knowledge['revision']}
            frozen_assets.append(item)
        if assets:
            from reply_lab_emojis import bind_rule, RULE
            p['asset_rule'] = p.get('asset_rule') or RULE
            bind_rule(bundle, p['asset_rule'])
        frozen = {'schema_version': 1, 'contract_version': 1, 'persona': p['persona'], 'complete': False,
                  'primary_skill': p['primary_skill'], 'skills': bundle, 'examples': examples,
                  'assets': frozen_assets,
                  'asset_rule': p.get('asset_rule', '允许不选表情；严肃纠错或投诉时不使用。'),
                  'model': p['model'], 'parameters': p.get('parameters', {})}
        if 'preference_memory' in p:
            memory = p['preference_memory']
            require(isinstance(memory, dict) and not set(memory) - {'enabled', 'slow_enabled'} and
                    all(type(v) is bool for v in memory.values()), '记忆开关必须为布尔值。')
            frozen['preference_memory'] = dict(memory)
        frozen['component_digests'] = {key: digest(frozen[key]) for key in ['persona', 'skills', 'examples', 'assets', 'asset_rule', 'model', 'parameters']}
        if 'preference_memory' in frozen:
            frozen['component_digests']['preference_memory'] = digest(frozen['preference_memory'])
        if phases:
            frozen['reply_pipeline']=phases
            frozen['component_digests']['reply_pipeline']=digest(phases)
        return self.save(actor, 'config', data['name'], frozen, data.get('parent'), data.get('note'), data.get('expected'))

    def usable_asset(self, actor, asset):
        with self.connect() as c:
            disabled = c.execute('SELECT 1 FROM lab_asset_disabled WHERE owner=? AND asset=?', (actor['id'], asset['payload']['sha256'])).fetchone()
        require(asset['payload']['review'] == 'approved' and not disabled, '素材未审核或已停用。', 409)

    def asset_save(self, actor, data):
        p = dict(data['payload'])
        require(p.get('rights') and p.get('caption') and 'visible_text' in p, '素材需要授权、描述和全部可见文字标注。')
        require(p.get('review') in {'candidate', 'approved'}, '素材审核状态无效。')
        require(p['review'] != 'approved' or bool(p.get('reviewer')), '素材审核需要具名审核人。')
        if p['review'] == 'approved':
            p['reviewer'] = {'id': actor['id'], 'name': actor['name']}
        for field in ['intents', 'suitable', 'avoid']:
            require(isinstance(p.get(field), list), '缺少素材适用与避用标注。')
        if data.get('parent') and not data.get('file_base64'):
            old = self.version(actor, data['parent'], 'asset')['payload']
            for key in ['sha256', 'mime', 'bytes', 'width', 'height', 'animated']:
                p[key] = old[key]
        else:
            from PIL import Image
            # New bytes are a new review target. An old card's approval cannot
            # silently approve a replacement picture with different content.
            p['review'] = 'candidate'
            p.pop('reviewer', None)
            try:
                raw = base64.b64decode(data.get('file_base64', ''), validate=True)
                require(0 < len(raw) <= 2000000, '素材试样须在2MB以内。')
                with Image.open(io.BytesIO(raw)) as im:
                    require(im.format in {'PNG', 'JPEG', 'GIF', 'WEBP'}, '仅接受位图素材。')
                    require(im.width * im.height <= 16000000, '素材像素过大。')
                    p.update(mime=Image.MIME[im.format], width=im.width, height=im.height,
                             animated=bool(getattr(im, 'is_animated', False)))
                    im.verify()
            except (ValueError, OSError):
                raise Fault('素材文件无效。') from None
            p.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
            path = self.home / 'assets' / p['sha256']
            path.parent.mkdir(exist_ok=True)
            if not path.exists():
                path.write_bytes(raw)
        # URLs, file paths and recipient routes are never accepted from a card.
        p = {key: p[key] for key in ['rights', 'caption', 'visible_text', 'intents', 'suitable', 'avoid', 'review', 'sha256', 'mime', 'bytes', 'width', 'height', 'animated'] + [k for k in ['reviewer', 'audience', 'intensity'] if k in p]}
        return self.save(actor, 'asset', data['name'], p, data.get('parent'), data.get('note'), data.get('expected'))

    def code_identity(self):
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.studio.root, capture_output=True, text=True, check=True)
        status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=self.studio.root, capture_output=True, text=True, check=True)
        return result.stdout.strip(), bool(status.stdout.strip())

    @staticmethod
    def model_scenario(p):
        # Allowlist: provenance paths, renewal labels, reference and future turns
        # never leave the evaluator, even if nested in scenario metadata.
        return {'mode': p['mode'], 'as_of': p['as_of'], 'family_group': p['family_group'], 'origin': p['origin'],
                'history': [{k: m[k] for k in ['role', 'content', 'at']} for m in p.get('history', [])],
                'messages': [{k: m[k] for k in ['role', 'content', 'at']} for m in p.get('messages', [])],
                'facts': p.get('facts', []), 'events': p.get('events', []),
                'initial_tasks': p.get('initial_tasks', []),
                **{k: p[k] for k in ['native_fixture', 'trigger_skill', 'gaps'] if k in p}}

    def start(self, actor, data, _batch=False):
        config = self.version(actor, data['config'], 'config')
        scenario = self.version(actor, data['scenario'], 'scenario')
        p = scenario['payload']
        require(p['family_group'] not in {e['family_group'] for e in config['payload']['examples']}, '运行场景与提示词示例来自同一家庭。', 409)
        engine = data.get('engine', 'native')
        require(engine in {'native', 'fixture'}, '运行引擎无效。')
        if engine == 'native':
            require(self.runner is not None, 'native_adapter_unavailable：B线原生运行接口尚未接入。', 503)
        for a in config['payload']['assets']:
            self.usable_asset(actor, self.version(actor, a['version'], 'asset'))
        sha, dirty = self.code_identity()
        require(engine != 'native' or not dirty, '原生实验必须固定已提交代码。', 409)
        prior = None
        if data.get('parent_run'):
            prior = self.run(actor, data['parent_run'])
            require(prior['status'] == 'done', '只能从已完成运行重建历史。', 409)
            require(prior['envelope']['scenario_version'] == scenario['id'], '历史分支必须保持同一场景版本。', 409)
            require(prior['envelope']['engine'] == engine, '不能跨引擎重放历史。')
        ident = uuid.uuid4().hex
        runtime = self.home / 'runs' / ident
        require(runtime.resolve().is_relative_to(self.home.resolve()), '运行根越界。', 409)
        history = self.delivered_history(actor, prior) if prior else []
        emoji_history=list(prior['envelope'].get('emoji_history',[])) if prior else []
        if prior and prior['result'].get('native_verified'):
            used=any(x['status'] in ('sent','accepted','unknown','partial') for x in prior['result'].get('emojis',[]))
            parts=prior['result'].get('parts',[])
            if used or (parts and all(p['status']=='sent' for p in parts)):
                emoji_history.append({'source_run':prior['id'],'has_emoji':used})
        messages = data.get('messages', [])
        require(isinstance(messages, list) and len(messages) <= 20 and all(isinstance(m, str) and 0 < len(m) <= 10000 for m in messages), '连续消息格式无效。')
        image_raw=image_info=None
        if data.get('image') is not None:
            require(engine=='native' and p['mode']=='passive','图片仅用于原生被动回复测试。')
            require(data.get('audio') is None,'请把图片和语音分成两条消息发送。')
            try:image_raw,image_info=__import__('image_inputs').decode(data['image'])
            except (ValueError,TypeError,KeyError):raise Fault('图片无效；支持PNG、JPEG，最大4MB、1200万像素。') from None
            if not messages:messages=['[图片消息]']
        audio_raw=audio_fmt=None
        if data.get('audio') is not None:
            require(engine=='native' and p['mode']=='passive','语音仅用于原生被动回复测试。')
            try:audio_raw,audio_fmt=__import__('voice_understanding').decode_audio(data['audio'])
            except (ValueError,TypeError,KeyError):raise Fault('语音文件无效；支持 WAV、MP3、M4A、OGG、FLAC，最大8MB，WAV最长180秒。') from None
            if not messages:messages=['[语音消息]']
        envelope = {'schema_version': 1, 'contract_version': 1, 'experiment_run_id': ident,
                    'baseline': self.baseline,
                    'config_version': config['id'], 'config_digest': config['digest'], 'config': config['payload'],
                    'scenario_version': scenario['id'], 'scenario_digest': scenario['digest'],
                    'scenario': self.model_scenario(p), 'code_commit': sha, 'dirty_code': dirty,
                    'runtime_root': str(runtime.resolve()), 'engine': engine, 'transport': 'simulation',
                    'parent_run': data.get('parent_run'), 'branch_mode': 'history_rebuild' if prior else 'new',
                    'history': history, 'emoji_history':emoji_history[-40:], 'messages': messages, 'replay_side_effects': False}
        if image_raw is not None:envelope['image']={**image_info,'file':'input-image.'+image_info['format']}
        if audio_raw is not None:
            envelope['audio']={**__import__('voice_understanding').inspect_audio(audio_raw,audio_fmt),'file':'input-audio.'+audio_fmt}
        with self.lock, self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            require(_batch or c.execute("SELECT count(*) FROM lab_runs WHERE status IN ('queued','running')").fetchone()[0] == 0, '串行实验正在运行，请等待完成。', 429)
            runtime.mkdir(parents=True, exist_ok=False)
            if image_raw is not None:
                image_path=runtime/envelope['image']['file'];image_path.write_bytes(image_raw);image_path.chmod(0o600)
            if audio_raw is not None:
                audio_path=runtime/envelope['audio']['file'];audio_path.write_bytes(audio_raw);audio_path.chmod(0o600)
            (runtime / 'input.json').write_text(encoded(envelope), encoding='utf8')
            c.execute('INSERT INTO lab_runs VALUES(?,?,?,?,?,?,?)', (ident, actor['id'], 'queued', encoded(envelope), digest(envelope), None, now()))
        self.pool.submit(self.execute, actor, ident)
        return {'id': ident, 'engine': engine, 'transport': 'simulation', 'status': 'queued'}

    def delivered_history(self, actor, run):
        """Rebuild old and new branches from immutable receipts, never display summaries."""
        chain=[];seen=set()
        while run:
            require(run['id'] not in seen and len(chain)<1000,'历史链损坏或过长。',409)
            seen.add(run['id']);chain.append(run)
            parent=run['envelope'].get('parent_run')
            run=self.run(actor,parent) if parent else None
        history=list(chain[-1]['envelope']['scenario']['history'])
        for run in reversed(chain):
            e=run['envelope'];messages=e['messages'] or [m['content'] for m in e['scenario']['messages']]
            note=__import__('voice_understanding').history_note(run['result'].get('voice_inputs',[]))
            if e.get('image'):note+='\n[历史图片未重新附入本轮；此前文字回复不等于当前可见原图，需要复核图片细节时请重新上传。]'
            history.extend({'role':'user','content':m+('\n'+note if i==0 and note else ''),'config_version':e['config_version']} for i,m in enumerate(messages))
            history.extend({'role':'assistant','content':p['body'],'config_version':e['config_version']}
                           for p in run['result'].get('parts',[]) if p.get('status')=='sent' and p.get('message_type')==7 and isinstance(p.get('body'),str))
        return history

    def batch(self, actor, data):
        configs, scenarios = data.get('configs'), data.get('scenarios')
        require(isinstance(configs, list) and 1 <= len(configs) <= 2 and isinstance(scenarios, list) and 1 <= len(scenarios) <= 50, '批次需1至2个配置和1至50个场景。')
        require(len(set(configs)) == len(configs) and len(set(scenarios)) == len(scenarios), '批次不能包含重复版本。')
        for ident in configs: self.version(actor, ident, 'config')
        for ident in scenarios: self.version(actor, ident, 'scenario')
        jobs, rejected = [], []
        with self.lock:
            with self.connect() as c:
                require(c.execute("SELECT count(*) FROM lab_runs WHERE status IN ('queued','running')").fetchone()[0] == 0, '已有实验运行，不能加入批次。', 429)
            for scenario in scenarios:
                for config in configs:
                    try:
                        job = self.start(actor, {'config': config, 'scenario': scenario, 'engine': data.get('engine', 'native')}, _batch=True)
                        jobs.append({**job, 'config': config, 'scenario': scenario})
                    except Fault as exc:
                        rejected.append({'config': config, 'scenario': scenario, 'error': str(exc)})
        return {'jobs': jobs, 'rejected': rejected, 'serial': True}

    def execute(self, actor, ident):
        started = time.monotonic()
        with self.connect() as c:
            c.execute("UPDATE lab_runs SET status='running' WHERE id=?", (ident,))
        try:
            e = self.run(actor, ident)['envelope']
            history = e['history'] or e['scenario']['history']
            messages = e['messages'] or [m['content'] for m in e['scenario']['messages']]
            if e['engine'] == 'fixture':
                result = {'output': '【界面契约模拟】已记录本轮输入；尚未调用原生模型，不作为回复质量样本。',
                          'simulation': True, 'native_verified': False, 'assembly': None,
                          'parts': [], 'trace': [], 'usage': None, 'harness_version': None}
            else:
                # B owns implementation. The browser cannot provide its result.
                sha, dirty = self.code_identity()
                require(sha == e['code_commit'] and not dirty, '排队后代码发生变化；请重新冻结运行。', 409)
                result = self.runner.run(json.loads(encoded(e)))
                self.verify_native(e, result)
            selected = result.get('emojis', [])
            body = result.get('output', '')
            if selected:
                body += '\n[本轮已发送表情：'+ '、'.join(a['caption'] for a in selected)+']'
            delivered=[]
            for part in result.get('parts',[]):
                if part.get('status')=='sent' and part.get('message_type')==7 and isinstance(part.get('body'),str):
                    delivered.append({'role':'assistant','content':part['body'],'config_version':e['config_version']})
            result['transcript'] = [*history, *({'role': 'user', 'content': m, 'config_version': e['config_version']} for m in messages), *delivered]
            result['elapsed_seconds'] = round(time.monotonic() - started, 3)
            result['quality_accepted'] = False
            status = 'done'
        except Exception as exc:
            result = {'error': str(exc) if isinstance(exc, Fault) else '实验失败，请查看隔离运行记录。',
                      'error_kind': type(exc).__name__, 'native_verified': False}
            status = 'failed'
        result['finished_at'] = now()
        result['content_digest'] = digest(result)
        # The DB remains the index. A per-run artifact makes the frozen input
        # and terminal evidence independently exportable without a live server.
        folder = self.home / 'runs' / ident
        (folder / 'result.json').write_text(encoded(result), encoding='utf8')
        with self.connect() as c:
            c.execute('UPDATE lab_runs SET status=?,result=? WHERE id=?', (status, encoded(result), ident))

    @staticmethod
    def verify_native(e, result):
        require(isinstance(result, dict) and result.get('simulation') is True, '原生实验必须使用模拟出口。', 409)
        require(result.get('session_id') and result.get('request_id') and result.get('harness_version'), '缺少原生会话、请求或Harness版本证据。', 409)
        expected_harness = e.get('baseline', {}).get('expected_harness_version')
        require(not expected_harness or result['harness_version'] == expected_harness, '实际Harness版本与冻结基线不一致。', 409)
        require(isinstance(result.get('output'), str), '缺少原生输出正文。', 409)
        a = result.get('assembly') or {}
        for key in ['config_digest', 'code_commit', 'runtime_root']:
            require(a.get(key) == e[key], '实际装配与冻结输入不一致：' + key, 409)
        require(a.get('component_digests') == e['config']['component_digests'], '组件加载摘要不匹配。', 409)
        require(a.get('loaded_skills') == {k: digest(v) for k, v in e['config']['skills'].items()}, '嵌套Skill加载证据缺失或不匹配。', 409)
        require(a.get('model') == e['config']['model'] and a.get('parameters') == e['config']['parameters'], '模型及参数实际装配不匹配。', 409)
        paths = a.get('writable_paths') or {}
        required = {'business_db', 'teacher_db', 'config', 'skills', 'sessions', 'queue', 'locks', 'logs', 'clock', 'cache', 'assets'}
        require(required <= set(paths), '缺少运行路径隔离证据。', 409)
        root = Path(e['runtime_root']).resolve()
        for value in paths.values():
            require(isinstance(value, str) and Path(value).is_absolute() and Path(value).resolve().is_relative_to(root), '原生可写路径未隔离。', 409)
        require(isinstance(result.get('trace'), list) and isinstance(result.get('parts'), list), '缺少工具轨迹或段状态。', 409)
        for part in result['parts']:
            require(part.get('simulation') is True and part.get('status') in {'draft', 'accepted', 'unknown', 'partial', 'failed', 'sent'}, '模拟交付状态或标记无效。', 409)
        result['native_verified'] = True

    def run(self, actor, ident):
        with self.connect() as c:
            row = c.execute('SELECT * FROM lab_runs WHERE id=? AND owner=?', (ident, actor['id'])).fetchone()
        require(row is not None, '运行不存在或无权读取。', 404)
        out = dict(row)
        out['envelope'] = json.loads(out['envelope'])
        require(digest(out['envelope']) == out['digest'], '运行快照校验失败。', 409)
        out['result'] = json.loads(out['result']) if out['result'] else None
        if out['result'] and 'content_digest' in out['result']:
            require(out['result']['content_digest'] == digest({k: v for k, v in out['result'].items() if k != 'content_digest'}), '运行结果校验失败。', 409)
        return out

    def compare(self, actor, data):
        require(data['left'] != data['right'], '请选择两次不同运行。')
        a, b = [self.run(actor, data[k]) for k in ['left', 'right']]
        require(a['status'] == b['status'] == 'done', '比较需要两次已完成运行。')
        for key in ['scenario_digest', 'code_commit', 'engine', 'messages', 'history']:
            require(a['envelope'][key] == b['envelope'][key], '比较条件不一致：' + key, 409)
        for key in ['model', 'parameters']:
            require(a['envelope']['config'][key] == b['envelope']['config'][key], '比较需固定模型及参数。', 409)
        if data.get('blind'):
            require(a['result'].get('native_verified') and b['result'].get('native_verified'), '契约模拟不能进入质量盲评。', 409)
            require(a['result']['harness_version'] == b['result']['harness_version'], '质量比较需固定Harness版本。', 409)
            pair = [a, b]
            secrets.SystemRandom().shuffle(pair)
            ident = uuid.uuid4().hex
            public = {'id': ident, 'scenario': a['envelope']['scenario'], 'left': pair[0]['result']['output'],
                      'right': pair[1]['result']['output'], 'rubric_version': 1, 'dimensions': RUBRIC}
            with self.connect() as c:
                c.execute('INSERT INTO lab_reviews VALUES(?,?,?,?,?,?)', (ident, actor['id'], encoded([p['id'] for p in pair]), encoded(public), None, now()))
            return public
        return {'left': a, 'right': b, 'quality_comparison': bool(a['result'].get('native_verified') and b['result'].get('native_verified'))}

    def dispatch(self, actor, path, data):
        if path=='input-image-preview':
            run=self.run(actor,data['id']);e=run['envelope'];image=e.get('image')
            require(image is not None,'此运行没有上传图片。',404)
            folder=(self.home/'runs'/run['id']).resolve();file=folder/image['file']
            require(file.resolve().is_relative_to(folder) and not file.is_symlink(),'图片路径无效。',409)
            raw=file.read_bytes();require(hashlib.sha256(raw).hexdigest()==image['sha256'],'图片文件校验失败。',409)
            return {**image,'file_base64':base64.b64encode(raw).decode()}
        if path=='audio-preview':
            run=self.run(actor,data['id']);e=run['envelope'];audio=e.get('audio')
            require(audio is not None,'此运行没有上传语音。',404)
            folder=(self.home/'runs'/run['id']).resolve();file=folder/audio['file']
            require(not file.is_symlink() and file.resolve().is_relative_to(folder),'语音文件路径无效。',409)
            raw=file.read_bytes()
            require(hashlib.sha256(raw).hexdigest()==audio['sha256'],'语音文件校验失败。',409)
            return {**audio,'file_base64':base64.b64encode(raw).decode()}

        if path == 'image-preview':
            from reply_lab_media import preview
            return preview(self, actor, data)
        if path in {'contexts', 'context-load', 'preset-load'}:
            from reply_lab_context import dispatch
            return dispatch(self, actor, path, data)
        if path == 'status':
            return {'contract_version': 1, 'native_available': self.runner is not None, 'transport': 'simulation',
                    'default_config': self.default_config(actor),
                    'notice': '模拟流程不等于原生模型或真实微信验收。', 'can_publish': False, 'rubric': RUBRIC}
        if path == 'versions':
            return {'items': self.versions(actor, data['kind'])}
        if path == 'version':
            return self.version(actor, data['id'])
        if path == 'config-save':
            return self.config_save(actor, data)
        if path == 'scenario-save':
            return self.scenario_save(actor, data)
        if path == 'asset-save':
            return self.asset_save(actor, data)
        if path == 'asset-preview':
            a = self.version(actor, data['id'], 'asset')['payload']
            raw = (self.home / 'assets' / a['sha256']).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == a['sha256'], '素材校验失败。', 409)
            return {'mime': a['mime'], 'file_base64': base64.b64encode(raw).decode()}
        if path == 'asset-disable':
            a = self.version(actor, data['id'], 'asset')
            with self.connect() as c:
                c.execute('INSERT OR IGNORE INTO lab_asset_disabled VALUES(?,?,?)', (actor['id'], a['payload']['sha256'], now()))
            return {'disabled': True, 'id': a['id']}
        if path == 'restore':
            old = self.version(actor, data['id'])
            return self.save(actor, old['kind'], old['name'], old['payload'], old['id'], data.get('note', '恢复历史版本'), data.get('expected'))
        if path == 'diff':
            a, b = [self.version(actor, data[k]) for k in ['left', 'right']]
            return {'diff': '\n'.join(difflib.unified_diff(json.dumps(a['payload'], ensure_ascii=False, indent=2, sort_keys=True).splitlines(), json.dumps(b['payload'], ensure_ascii=False, indent=2, sort_keys=True).splitlines(), fromfile=a['id'], tofile=b['id'], lineterm=''))}
        if path == 'start':
            return self.start(actor, data)
        if path == 'batch':
            return self.batch(actor, data)
        if path == 'run':
            return self.run(actor, data['id'])
        if path == 'runs':
            with self.connect() as c:
                return {'items': [dict(r) for r in c.execute('SELECT id,status,created FROM lab_runs WHERE owner=? ORDER BY rowid DESC LIMIT 200', (actor['id'],))]}
        if path == 'compare':
            return self.compare(actor, data)
        if path == 'review-report':
            with self.connect() as c:
                rows = c.execute('SELECT * FROM lab_reviews WHERE owner=?', (actor['id'],)).fetchall()
            report = {'reviewed': 0, 'pending': 0, 'tie': 0, 'both_bad': 0, 'hard_failure_reviews': 0,
                      'by_config': {}, 'families': [], 'quality_accepted': False,
                      'notice': '人工样本结果仅代表这些家庭和场景，不自动发布或宣称总体提升。'}
            families = set()
            for row in rows:
                if not row['vote']:
                    report['pending'] += 1
                    continue
                vote, public, mapping = json.loads(row['vote']), json.loads(row['payload']), json.loads(row['mapping'])
                report['reviewed'] += 1
                families.add(public['scenario']['family_group'])
                report['hard_failure_reviews'] += bool(vote.get('hard_failures'))
                if vote['choice'] in {'tie', 'both_bad'}: report[vote['choice']] += 1
                for side, run_id in zip(['left', 'right'], mapping):
                    config = self.run(actor, run_id)['envelope']['config_version']
                    totals = report['by_config'].setdefault(config, {'win': 0, 'loss': 0, 'tie': 0, 'both_bad': 0})
                    outcome = vote['choice'] if vote['choice'] in {'tie', 'both_bad'} else ('win' if vote['choice'] == side else 'loss')
                    totals[outcome] += 1
            report['families'] = sorted(families)
            return report
        if path == 'vote':
            require(data.get('choice') in {'left', 'right', 'tie', 'both_bad'}, '请选择胜负、平局或都不合格。')
            require(isinstance(data.get('reason'), str) and data['reason'].strip(), '请记录原句依据及判断理由。')
            scores = data.get('scores', {})
            require(set(scores) == {'left', 'right'} and all(set(v) == set(RUBRIC) and all(type(n) is int and 1 <= n <= 5 for n in v.values()) for v in scores.values()), '两侧七个维度须分别评分1至5分。')
            require(isinstance(data.get('hard_failures'), list), '硬失败必须逐项记录。')
            with self.connect() as c:
                row = c.execute('SELECT vote FROM lab_reviews WHERE id=? AND owner=?', (data['id'], actor['id'])).fetchone()
                require(row is not None and row['vote'] is None, '评审不存在或已经提交。', 409)
                c.execute('UPDATE lab_reviews SET vote=? WHERE id=?', (encoded({k: data[k] for k in ['choice', 'reason', 'scores', 'hard_failures']}), data['id']))
            return {'recorded': True, 'reviewer': actor['id']}
        raise Fault('实验接口不存在。', 404)
