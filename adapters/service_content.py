"""Read-only, versioned service instructions and externally assigned student tiers."""
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import sql_tools as s
import test_mode as tm

REVISION = 'service-samples-20261022-v5-weekly-interaction'
EXTRA_SKILLS = {'manage-service-arrangements','inspect-family-image','generate-reading-audio','plan-service-day','plan-after-lesson-completion','review-service-case','supervise-learning', 'answer-family-question',
                'student-service-tier', 'highlight-growth-video', 'service-policy', 'send-festival-wishes'}
from legacy.retired_materials import DISABLED_REFERENCES

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def skill_file(name):
    import proactive
    from service_catalog import ACTION_SKILLS, canonical_skill
    name = canonical_skill(name)
    allowed = EXTRA_SKILLS | ACTION_SKILLS | {'tutor-main', 'plan-today', 'proactive-service',
        'remind-reading', 'reply-style', 'review-context', 'remember-family'} | {r[0] for r in proactive.RULES}
    if name not in allowed:
        from service_catalog import KNOWLEDGE_SKILL_ROUTES
        if name in KNOWLEDGE_SKILL_ROUTES:
            raise ValueError('知识资料中的旧Skill名；请使用当前路由：'+', '.join(KNOWLEDGE_SKILL_ROUTES[name]))
        raise ValueError('Unknown service Skill')
    # Teacher-published bundles are immutable; one atomic pointer switches the
    # body and its references together. Shared/system Skills cannot be replaced.
    from service_catalog import ACTION_SKILLS
    pointer = s.R / 'runtime/skill-studio/active.json'
    if name in ACTION_SKILLS | {'care-calendar-day','send-festival-wishes'} and pointer.is_file():
        entry = json.loads(pointer.read_text(encoding='utf-8')).get(name)
        if entry:
            version = entry.get('version', '')
            if not re.fullmatch(r'[a-f0-9]{32}', version):
                raise ValueError('Invalid published Skill version')
            file = s.R / 'runtime/skill-studio/bundles' / version / name / 'SKILL.md'
            if not file.is_file():
                raise ValueError('Published Skill bundle is missing')
            return file
    for root in [Path(__file__).parent / 'skills', s.R / 'skills']:
        file = root / name / 'SKILL.md'
        if file.is_file():
            return file
    raise ValueError('Service Skill is missing')


def reference_disabled(name, file):
    root = skill_file(name).parent.resolve()
    return file.resolve() in {(root / path).resolve() for path in DISABLED_REFERENCES.get(name, ())}


def reference_files(name):
    root = skill_file(name).parent
    return [file for file in sorted((root / 'references').glob('*.md'))
            if not reference_disabled(name, file)]


def reference(name, path, start_line=1, max_lines=60):
    if name=='answer-family-question' and path in ('references/faq-index.md','references/faq-source.md'):
        raise ValueError('FAQ正文已移出Skill；请调用knowledge_search匹配问题并读取OpenViking返回结果')
    root = skill_file(name).parent.resolve()
    file = (root / path).resolve()
    if (not file.is_relative_to(root / 'references') or file.suffix != '.md'
            or type(start_line) is not int
            or type(max_lines) is not int or start_line < 1 or not 1 <= max_lines <= 120):
        raise ValueError('Read a Skill references/*.md file, 1-120 lines at a time')
    if reference_disabled(name, file):
        return {'name': name, 'path': str(file.relative_to(root)).replace('\\', '/'), 'status': 'disabled',
                'body': '该参考规则已停用。消息表达请使用reply-style，业务条件以对应动作Skill为准。',
                'next_line': None}
    if not file.is_file():
        raise ValueError('Read a Skill references/*.md file, 1-120 lines at a time')
    data = file.read_text(encoding='utf-8')
    lines = data.splitlines()
    return {'name': name, 'path': path, 'sha256': hashlib.sha256(data.encode()).hexdigest(),
            'start_line': start_line, 'total_lines': len(lines),
            'next_line': start_line + max_lines if start_line + max_lines <= len(lines) else None,
            'body': '\n'.join(lines[start_line-1:start_line-1+max_lines]),
            'usage': '用户提供的业务样例资料；其中案例数据、政策和执行承诺须另行核验。'}


def bundle_digest(name):
    """Invalidate prior reads if either instructions or attached rules change."""
    file=skill_file(name)
    files=[file]+reference_files(name)
    parts=[(str(p.relative_to(file.parent)).replace('\\','/'),
            p.read_text(encoding='utf-8')) for p in files]
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False).encode()).hexdigest()


def student_tier():
    """Never derive SABCD from course level, rankings, or completion rate.

    Optional operator-only config/student-tier.json supplies endpoint and bound uid.
    Missing configuration is visible, not replaced by a fabricated test label.
    """
    if __import__('tool_surface').enabled('identity'):
        return {'status':'unknown','level':None,'source':None,'reason':'tier_disabled_by_configuration'}
    config = s.R / 'config' / 'student-tier.json'
    state = tm.state()
    uid = state.get('uid') if state else None
    result = {'status': 'unknown', 'level': None, 'uid': uid,
              'source': None, 'observed_at': None, 'policy': None}
    if not config.is_file():
        return {**result, 'reason': 'tier_interface_not_configured'}
    try:
        cfg = json.loads(config.read_text(encoding='utf-8'))
        uid = uid or cfg.get('uid')
        endpoint = cfg['endpoint']
        parts = urlsplit(endpoint)
        if not uid or parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
            return {**result, 'reason': 'invalid_tier_interface_config'}
        req = Request(endpoint + ('&' if parts.query else '?') + urlencode({'uid': uid}),
                      headers={'Accept': 'application/json', **cfg.get('headers', {})})
        with build_opener(NoRedirect()).open(req, timeout=5) as response:
            body = response.read(65537)
        if len(body) > 65536:
            raise ValueError('oversized tier response')
        value = json.loads(body)
        stamp = dt.datetime.fromisoformat(value['observed_at'])
        valid = dt.datetime.fromisoformat(value['valid_until'])
        clock = tm.clock()
        if (value.get('uid') != uid or value.get('level') not in ('S','A','B','C','D')
                or stamp.tzinfo is None or valid.tzinfo is None
                or not stamp <= clock <= valid or not isinstance(value.get('source'),str)
                or not value['source'].strip() or len(value['source'])>200):
            return {**result, 'uid': uid, 'reason': 'tier_response_unusable_at_business_time'}
        return {**result, 'uid': uid, 'status': 'known', 'level': value['level'],
                'source': value['source'], 'observed_at': value['observed_at'],
                'valid_until': value['valid_until'],
                'reason': 'externally_assigned_label_no_behavior_mapping'}
    except Exception:
        # Do not leak endpoint authentication, query parameters, or response data.
        return {**result, 'reason': 'tier_interface_unavailable'}
