"""Manifest-pinned knowledge reads; model arguments never contain storage paths."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit

from .domain import Strict, ID, require, BusinessError


READ_ROLES = {'service', 'planner_daily', 'planner_progress', 'admin'}


class KnowledgeRead(Strict):
    document_id: ID


SPECS = {
    'knowledge.list': (READ_ROLES, None, '列出宿主绑定版本和课节范围的资料导航；摘要不是正文证据'),
    'knowledge.read': (READ_ROLES, KnowledgeRead, '按绑定资料编号读取完整正文与来源；不跨版本或拿相邻课补位'),
}


@dataclass(frozen=True)
class KnowledgeBinding:
    revision: str
    allowed_unit_ids: tuple[str, ...]
    course_version: str | None = None
    simulation: bool = False
    allow_reference: bool = False


def decode_cli_json(text):
    """Reuse course_cli's trailing-document rule without importing its CLI side effects."""
    for index, char in enumerate(text):
        if char in '{["':
            try:
                value, end = json.JSONDecoder().raw_decode(text[index:])
                if not text[index + end:].strip():
                    return value
            except ValueError:
                continue
    raise BusinessError('KNOWLEDGE_UNAVAILABLE', '知识读取未返回有效JSON')


class OpenVikingCLI:
    """Trusted host supplies executable/config; no credential content is read or logged."""
    def __init__(self, executable, config_path, timeout=45):
        self.executable, self.config_path = str(executable), str(config_path)
        self.timeout = timeout

    def read(self, uri):
        env = dict(os.environ, OPENVIKING_CLI_CONFIG_FILE=self.config_path, NO_COLOR='1')
        try:
            result = subprocess.run([self.executable, 'read', uri, '-o', 'json'],
                                    capture_output=True, text=True, encoding='utf-8', env=env,
                                    timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise BusinessError('KNOWLEDGE_UNAVAILABLE', '知识读取暂不可用') from None
        require(result.returncode == 0, 'KNOWLEDGE_UNAVAILABLE', '知识读取失败，未使用其他课节替代')
        value = decode_cli_json(result.stdout)
        if isinstance(value, dict):
            require(value.get('status') != 'error' and value.get('success') is not False and value.get('ok') is not False,
                    'KNOWLEDGE_UNAVAILABLE', '知识服务返回错误')
            value = value.get('result', value.get('data', value))
        if isinstance(value, dict):
            value = value.get('content')
        require(isinstance(value, str) and value.strip(), 'KNOWLEDGE_UNAVAILABLE', '知识正文缺失')
        return value


class KnowledgeCatalog:
    def __init__(self, manifests, client):
        self.client, self.units = client, {}
        for manifest in manifests:
            if isinstance(manifest, (str, Path)):
                manifest = json.loads(Path(manifest).read_text(encoding='utf-8'))
            revision, root = manifest['revision'], manifest['target_root'].rstrip('/')
            require(urlsplit(root).scheme == 'viking', 'INVALID_MANIFEST', '知识根地址无效')
            for unit in manifest['units']:
                key = (revision, unit['unit_id'])
                require(key not in self.units, 'INVALID_MANIFEST', '资料版本编号重复')
                uris = unit.get('actual_read_uris', [])
                require(len(uris) == 1, 'INVALID_MANIFEST', '资料必须有唯一实际正文地址')
                uri = uris[0]
                decoded = unquote(uri)
                require(uri.startswith(root + '/') and decoded == uri and '..' not in uri.split('/')
                        and not urlsplit(uri).query and not urlsplit(uri).fragment,
                        'INVALID_MANIFEST', '正文地址不在固定版本范围')
                require(len(unit.get('file_sha256', '')) == 64, 'INVALID_MANIFEST', '缺少正文指纹')
                self.units[key] = {**unit, 'revision': revision,
                                   'usage_status': manifest.get('usage_status', 'unknown')}

    def _visible(self, role, binding):
        require(role in READ_ROLES, 'ROLE_FORBIDDEN', '角色不可读取知识')
        require(isinstance(binding, KnowledgeBinding) and binding.revision and binding.allowed_unit_ids,
                'KNOWLEDGE_BINDING_UNKNOWN', '尚未确认适用版本与资料范围')
        records = []
        for uid in binding.allowed_unit_ids:
            record = self.units.get((binding.revision, uid))
            require(record is not None, 'KNOWLEDGE_GAP', '绑定资料缺失，不用相邻课替代')
            if record.get('record_type') in {'lesson', 'week_overview', 'ability_alignment'}:
                if not binding.simulation:
                    require(binding.course_version is not None
                            and record.get('course_content_version') == binding.course_version
                            and record.get('business_review_status') == 'verified',
                            'KNOWLEDGE_BINDING_UNKNOWN', '正式课程版本或审核状态尚未确认')
            if record.get('retrieval_scope') == 'reference' and not binding.allow_reference:
                continue
            records.append(record)
        return records

    @staticmethod
    def _provenance(record, binding):
        keys = ('revision', 'record_type', 'course_level', 'book_title', 'source_day', 'source_week',
                'course_content_version', 'book_edition', 'product_version', 'business_review_status',
                'effective_from', 'verified_at', 'source_snapshot', 'source_lines', 'source_sections',
                'scope_note', 'source_declares_ai_generated', 'usage_status', 'retrieval_scope')
        return {**{key: record.get(key) for key in keys}, 'simulation_binding': binding.simulation,
                'not_verified_current_entitlements': record.get('business_review_status') != 'verified'}

    def list(self, role, binding):
        return {'documents': [
            {'document_id': r['unit_id'], 'title': r.get('display_title', r.get('title', r['unit_id'])),
             'navigation_summary': r.get('navigation_summary'), 'provenance': self._provenance(r, binding)}
            for r in self._visible(role, binding)], 'navigation_is_evidence': False}

    def read(self, role, binding, document_id):
        record = next((r for r in self._visible(role, binding) if r['unit_id'] == document_id), None)
        require(record is not None, 'KNOWLEDGE_SCOPE_FORBIDDEN', '资料不属于当前绑定范围')
        try:
            body = self.client.read(record['actual_read_uris'][0])
        except BusinessError:
            raise
        except Exception:
            raise BusinessError('KNOWLEDGE_UNAVAILABLE', '知识读取失败，未使用其他课节替代') from None
        require(isinstance(body, str) and body.strip(), 'KNOWLEDGE_GAP', '原资料正文为空')
        # CLI may omit the final newline. No other content normalization is permitted.
        candidates = (body, body + '\n') if not body.endswith('\n') else (body,)
        require(any(hashlib.sha256(v.encode()).hexdigest() == record['file_sha256'] for v in candidates),
                'KNOWLEDGE_REVISION_CHANGED', '正文与固定清单指纹不一致，需要重新审核映射')
        return {'document_id': document_id, 'content': body, 'content_sha256': record['file_sha256'],
                'provenance': self._provenance(record, binding), 'complete_body': True}

    def dispatch(self, name, args, role, binding):
        if name == 'knowledge.list':
            require(not args, 'INVALID_SCHEMA', '目录读取不接受参数')
            return self.list(role, binding)
        require(name == 'knowledge.read', 'TOOL_NOT_FOUND', '知识工具不存在')
        return self.read(role, binding, KnowledgeRead.model_validate(args).document_id)
