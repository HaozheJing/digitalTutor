"""Live OpenViking retrieval; no embedded FAQ answers and no write capability."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sql_tools as s

CATALOG = Path(__file__).with_name('knowledge_catalog.json')
CLI = '/home/ubuntu/digitalTutor/runtime/openviking/bin/ov'


def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def cli(args):
    env = dict(os.environ, OPENVIKING_CLI_CONFIG_FILE=os.environ.get('TUTOR_KNOWLEDGE_CONFIG_FILE',str(s.R/'config/source-enterprise-ovcli.conf')),
               NO_COLOR='1')
    try:
        process = subprocess.run([CLI, *args, '-o', 'json'], env=env,
                                 capture_output=True, text=True, encoding='utf-8', timeout=45)
        if process.returncode or len(process.stdout) > 1000000:
            raise ValueError()
        for index, ch in enumerate(process.stdout):
            if ch not in '{["':
                continue
            try:
                value, end = json.JSONDecoder().raw_decode(process.stdout[index:])
                if process.stdout[index+end:].strip():
                    continue
                if isinstance(value, dict):
                    if value.get('ok') is False:
                        raise RuntimeError()
                    return value.get('result', value.get('data', value))
                return value
            except ValueError:
                continue
    except Exception:
        pass
    # Never expose CLI stderr/configuration or silently fall back to Skill copies.
    raise RuntimeError('OpenViking knowledge service unavailable')


def read_document(document_id):
    # Search still retrieves live matches; canonical bodies reuse a registered version.
    return __import__('passive_context').query('knowledge_read', {'document_id':document_id},
                                               lambda:_read_document(document_id))


def _read_document(document_id):
    item = next((x for x in catalog()['documents'] if x['id'] == document_id), None)
    if item is None:
        return {'status': 'unknown', 'reason': 'document_not_registered'}
    try:
        value = cli(['read', item['uri']])
        if isinstance(value, dict):
            value = value.get('content')
        if not isinstance(value, str):
            raise ValueError()
        sha = hashlib.sha256(value.encode('utf-8')).hexdigest()
        if sha != item['sha256']:
            return {'status': 'changed', 'document_id': document_id, 'uri': item['uri'],
                    'reason': 'knowledge_version_changed_requires_review', 'can_answer_directly': False}
        return {**item, 'document_id': document_id, 'status': 'ok', 'body': value,
                'current_skill_routes':__import__('service_catalog').KNOWLEDGE_SKILL_ROUTES,
                'live_read': True, 'can_answer_directly': False,
                'usage': '知识导航或未核实资料；据此确定核查路径，政策和家庭事实另查，不能直接当已批准标准答案。',
                'verified_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    except Exception:
        return {'status': 'unavailable', 'document_id': document_id,
                'can_answer_directly': False, 'reason': 'openviking_read_unavailable'}


def search(query, scope='faq', limit=3):
    if scope == 'emoji':
        return __import__('emoji_knowledge').search(query,limit)
    if not isinstance(query, str) or not query.strip() or len(query)>500:
        raise ValueError('检索问题需为1至500字文本')
    if scope not in ('faq','product') or type(limit) is not int or not 1<=limit<=5:
        raise ValueError('scope仅支持faq/product，limit为1至5')
    data = catalog()
    allowed = {x['uri']: x for x in data['documents'] if x['kind']==scope
               and (scope!='product' or x['retrieval_scope']=='core')}
    try:
        found = cli(['find', '问题：'+query.strip(), '--uri', data[scope]['search_uri'],
                     '--tags', data[scope]['tags'], '--limit', str(limit), '-L', '2'])
        if not isinstance(found, dict) or not isinstance(found.get('resources'),list):
            raise ValueError()
    except Exception:
        return {'status': 'unavailable', 'matches': [],
                'reason': 'openviking_search_unavailable', 'fallback': '说明需核实，不读取Skill里的FAQ副本'}
    matches = []
    seen = set()
    for hit in found['resources']:
        if not isinstance(hit,dict):
            continue
        uri = hit.get('uri','')
        item = allowed.get(uri) or allowed.get(uri.rstrip('/')+'/content.md')
        if item is None or item['id'] in seen:
            continue
        seen.add(item['id'])
        # Read the canonical, hash-pinned body, not the semantic summary.
        body = read_document(item['id'])
        score = hit.get('score')
        matches.append({**body, 'score': score if type(score) in (int,float) else None})
        if len(matches)>=limit:
            break
    return {'status': 'ok' if matches else 'no_match', 'query': query, 'scope': scope,
            'revision': data[scope]['revision'], 'matches': matches,
            'approved_business_answers': 0,
            'instruction': '按语义和适用范围判断命中；相似度不等于答案正确。导航命中不等于问题已解决。'}
