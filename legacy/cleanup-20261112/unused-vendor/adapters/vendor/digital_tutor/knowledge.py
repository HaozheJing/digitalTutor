"""Manifest-pinned knowledge reads; model arguments never contain storage paths."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import re
from pydantic import Field
from urllib.parse import unquote, urlsplit

from .domain import Strict, ID, require, BusinessError


READ_ROLES = {'service', 'planner_daily', 'planner_progress', 'admin'}


class KnowledgeRead(Strict):
    document_id: ID

class KnowledgeList(Strict):
    query: str | None = Field(default=None, min_length=1, max_length=200, description='可选定位词，按标题和导航摘要匹配；用简短主题词，如挖掘机、午饭。导航只供定位，答题前仍须读取正文。')
    limit: int | None = Field(default=None,ge=1,le=50)
    after: int = Field(default=0,ge=0)
    snapshot: str | None = None


SPECS = {
    'knowledge.list': (READ_ROLES, KnowledgeList, '列出宿主绑定版本和课节范围的资料导航，可用query定位主题；摘要不是正文证据，未命中不表示故事没有此事'),
    'knowledge.read': (READ_ROLES, KnowledgeRead, '按绑定资料编号读取完整正文与来源；不跨版本或拿相邻课补位'),
}


@dataclass(frozen=True)
class KnowledgeBinding:
    revision: str
    allowed_unit_ids: tuple[str, ...]
    course_version: str | None = None
    simulation: bool = False
    allow_reference: bool = False
    available_at: dict[str, str] | None = None
    as_of: str | None = None
    unit_revisions: dict[str, str] | None = None


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
        self.navigation_paths = {}
        for manifest in manifests:
            navigation_root=None
            if isinstance(manifest, (str, Path)):
                manifest_path=Path(manifest).resolve()
                navigation_root=manifest_path.parent/'content'
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            revision, root = manifest['revision'], manifest['target_root'].rstrip('/')
            require(urlsplit(root).scheme == 'viking', 'INVALID_MANIFEST', '知识根地址无效')
            for unit in manifest['units']:
                if unit.get('publication_status')=='internal_navigation_only' and manifest.get('approved_business_answers')==0:
                    unit={**unit, 'actual_read_uris':[unit['expected_read_uri']],
                          'record_type':'faq_navigation', 'business_review_status':unit.get('business_status','unverified'),
                          'retrieval_scope':'internal_navigation_only', 'scope_note':manifest.get('read_contract')}
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
                if navigation_root and unit.get('relative_path'):
                    path=(navigation_root/unit['relative_path']).resolve()
                    require(path.is_relative_to(navigation_root.resolve()),'INVALID_MANIFEST','导航镜像路径越界')
                    self.navigation_paths[key]=path

    def _visible(self, role, binding):
        require(role in READ_ROLES, 'ROLE_FORBIDDEN', '角色不可读取知识')
        require(isinstance(binding, KnowledgeBinding) and binding.revision and binding.allowed_unit_ids,
                'KNOWLEDGE_BINDING_UNKNOWN', '尚未确认适用版本与资料范围')
        records = []
        if binding.unit_revisions is not None:
            require(set(binding.unit_revisions)==set(binding.allowed_unit_ids)
                    and all(isinstance(v,str) and v for v in binding.unit_revisions.values()),
                    'KNOWLEDGE_BINDING_UNKNOWN','每份资料需有明确的独立版本，不能回退到其他版本')
        for uid in binding.allowed_unit_ids:
            if binding.available_at is not None:
                from .domain import timestamp
                require(binding.as_of is not None and uid in binding.available_at,
                        'KNOWLEDGE_AVAILABILITY_UNKNOWN','资料缺少明确可用时间')
                if timestamp(binding.available_at[uid])>timestamp(binding.as_of):
                    continue
            revision=binding.unit_revisions[uid] if binding.unit_revisions is not None else binding.revision
            record = self.units.get((revision, uid))
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

    def list(self, role, binding, query=None, *, focus=False):
        documents=[]
        scores={}
        matches={}
        terms=[v.casefold() for v in re.split(r'[\s，,、；;]+',query.strip()) if v] if query else []
        for r in self._visible(role,binding):
            doc={'document_id':r['unit_id'],'title':r.get('display_title',r.get('title',r['unit_id'])),
                 'navigation_summary':r.get('navigation_summary'),'provenance':self._provenance(r,binding)}
            locator=doc['title']+' '+str(doc['navigation_summary'] or '')
            path=self.navigation_paths.get((r['revision'],r['unit_id']))
            if path and path.is_file():
                raw=path.read_bytes()
                require(hashlib.sha256(raw).hexdigest()==r['file_sha256'],
                        'KNOWLEDGE_REVISION_CHANGED','定位镜像与批准正文指纹不同，不能用它定位')
                text=raw.decode('utf-8')
                heading=re.search(r'^## 【书籍名称】\s*\n+([^\n]+)',text,re.M)
                if heading:doc['chapter_title']=heading[1].strip()
                doc['locator_source_sha256']=r['file_sha256']
                doc['locator_is_body_read']=False
                locator+=' '+text
                hits=[text.casefold().find(term) for term in terms if term in text.casefold()]
                if hits:
                    at=min(hits)
                    doc['navigation_match_excerpt']=text[max(0,at-35):at+100]
            score=sum(term in locator.casefold() for term in terms)
            title=doc['title']+' '+doc.get('chapter_title','')
            score+=10*sum(term in title.casefold() for term in terms)
            scores[doc['document_id']]=score
            matches[doc['document_id']]={term for term in terms if term in locator.casefold()}
            documents.append(doc)
        if focus and terms:
            book_titles={str(doc['provenance'].get('book_title') or '').casefold() for doc in documents}
            broad={t for t in terms if all(t in hit for hit in matches.values()) or
                   (len(t)>=2 and any(t in title for title in book_titles))}
            selective={t for t in set(terms)-broad if any(t in hit for hit in matches.values())}
            if selective:
                # A locator is not a relevance oracle. Preserve all matching IDs, navigation and provenance.
                # Only discard snippets whose match was the generic book name in the boilerplate header.
                for doc in documents:
                    if not (matches[doc['document_id']] & selective):
                        doc.pop('navigation_match_excerpt',None)
        if query:
            documents=sorted([doc for doc in documents if scores[doc['document_id']]],
                             key=lambda doc:-scores[doc['document_id']])
        return {'documents':documents,'query':query,'navigation_is_evidence':False,
                'no_match_is_absence_proof':False}

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

    def dispatch(self, name, args, role, binding, *, focus=False):
        if name == 'knowledge.list':
            cmd=KnowledgeList.model_validate(args)
            result=self.list(role, binding, cmd.query,focus=focus)
            if cmd.limit is not None:
                from .storage import digest
                rows=result.get('documents',[])
                stamp=digest([role,cmd.query,rows])
                require(cmd.after==0 or cmd.snapshot==stamp,'RESOURCE_SNAPSHOT_CHANGED','知识目录版本已变，请重新分页')
                page=rows[cmd.after:cmd.after+cmd.limit]
                result={**result,'documents':page,'total':len(rows),'snapshot':stamp,
                    'has_more':cmd.after+len(page)<len(rows),'next_after':cmd.after+len(page)}
            if focus:result['search_policy']='全部匹配资料入口、导航摘要和来源保留；仅去掉只由泛书名命中的片段，避免重复书名和导入声明。其他待答主题仍须定位并读正文；目录或已读章节未提到，不等于课程没有。省略query可查看完整目录。'
            return result
        require(name == 'knowledge.read', 'TOOL_NOT_FOUND', '知识工具不存在')
        return self.read(role, binding, KnowledgeRead.model_validate(args).document_id)
