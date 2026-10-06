"""Approved service Skills plus mandatory guides, read through native Hermes."""
import hashlib
import json
from pathlib import Path
import re

from .domain import Strict, ID, require, BusinessError


ALIASES = {
    'recommend-catch-up-reading': 'prompt-backlog-makeup',
    'reinforce-reading-streak': 'celebrate-learning-streak',
    'restore-course-sequence': 'support-course-continuity',
    'review-weekly-learning': 'weekly-learning-review',
}
FAMILY_SKILLS = frozenset('''remind-reading celebrate-lesson-completion prompt-backlog-makeup
celebrate-learning-streak plan-makeup-learning support-course-continuity rebuild-reading-rhythm
close-week-reading-gap plan-weekend-makeup weekly-learning-review share-reading-reflection
quiz-course-knowledge practice-reading-method connect-reading-to-life celebrate-important-day
highlight-course-start highlight-first-perfect-answer highlight-seven-day-completion
highlight-book-completion highlight-second-week-attendance highlight-reading-milestone
highlight-quiz-ranking highlight-expressive-answer highlight-ranking-progress highlight-reading-birthday
review-learning-progress prepare-growth-review-package produce-growth-video explain-lesson
answer-course-service-question address-learning-concern handle-complaint-escalation
respond-to-child-sharing-help follow-up-unanswered-question'''.split())
INTERNAL_SKILLS = {'plan-proactive-service', 'close-service-case'}
EXTENSION_SKILLS = {'prepare-renewal-evidence'}
GUIDES = {'service-basics', 'communication', 'supervision', 'interaction', 'learning-feedback', 'qa'}
V1_CONSTRAINTS = (
    '当前后端只有 daily 和绑定服务报告的 progress 两个规划入口。普通来信直接由 service 处理。'
    '只有 daily 可新增正式事项和确定今日成员；progress 仅更新绑定报告关联的原事项。'
    'service 通过 progress.submit 报告，不能直接关闭事项。Skill 不授权工具，不替代发送、暂停、完成证据校验。'
    '旧正文出现不符当前入口或写入方式的步骤时，以当前后端合同为准，独立新需求写待规划输入。'
)


class SkillRead(Strict):
    skill_id: ID


ROLES = {'service', 'planner_daily', 'planner_progress', 'admin'}
SPECS = {
    'skills.list': (ROLES, None, '列出当前角色批准的规范Skill入口；别名不生成额外能力'),
    'skills.read': (ROLES, SkillRead, '通过Hermes原生只读接口加载完整Skill及所有必读规范'),
}


def native_skill_reader(name):
    """Call only in the isolated Hermes process configured with the approved skill root."""
    try:
        from tools.skills_tool import skill_view
        return json.loads(skill_view(name, preprocess=False))
    except Exception:
        raise BusinessError('SKILL_RUNTIME_UNAVAILABLE', 'Hermes原生Skill读取暂不可用') from None


def _contained(root, path):
    resolved = path.resolve()
    require(resolved.is_relative_to(root.resolve()), 'SKILL_PATH_FORBIDDEN', 'Skill或规范路径越界')
    require(resolved.is_file(), 'SKILL_MISSING', 'Skill或必读规范缺失')
    return resolved


def _frontmatter(content):
    # The approved project format uses scalar name/description and comma-separated guide IDs.
    # Reject unsupported/missing metadata rather than silently losing mandatory dependencies.
    match = re.match(r'^---\r?\n(.*?)\r?\n---(?:\r?\n|$)', content, re.S)
    require(match is not None, 'INVALID_SKILL_METADATA', '缺少Skill front matter')
    header = match[1]
    def scalar(key):
        values = re.findall(r'^\s*' + re.escape(key) + r':\s*([^\r\n]+)$', header, re.M)
        require(len(values) == 1, 'INVALID_SKILL_METADATA', 'Skill元数据字段缺失或重复')
        return values[0].strip().strip('"\'')
    return scalar('name'), scalar('description'), scalar('tutor-required-guides').split(',')


class SkillCatalog:
    def __init__(self, agent_root, native_reader=native_skill_reader, allowed_skill_ids=None):
        self.root = Path(agent_root).resolve()
        self.native_reader = native_reader
        self.entries = {}
        self.guide_hashes = {}
        approved = FAMILY_SKILLS | INTERNAL_SKILLS | EXTENSION_SKILLS | set(ALIASES)
        self.approved = set(allowed_skill_ids) if allowed_skill_ids is not None else approved
        require(self.approved <= approved, "SKILL_SCOPE_FORBIDDEN", "包含未批准Skill")
        for name in sorted(self.approved):
            path = _contained(self.root, self.root / 'skills' / name / 'SKILL.md')
            content = path.read_text(encoding='utf-8')
            parsed_name, description, guides = _frontmatter(content)
            guides = list(dict.fromkeys(g.strip() for g in guides))
            require(parsed_name == name and set(guides) <= GUIDES and 'service-basics' in guides,
                    'INVALID_SKILL_METADATA', 'Skill名称或必读规范映射无效')
            for guide in guides:
                guide_path = _contained(self.root, self.root / 'guides' / (guide + '.md'))
                self.guide_hashes[guide] = hashlib.sha256(guide_path.read_text(encoding='utf-8').encode()).hexdigest()
            self.entries[name] = {'skill_id': name, 'description': description,
                                  'required_guides': guides, 'sha256': hashlib.sha256(content.encode()).hexdigest()}

    @staticmethod
    def _allowed(role):
        require(role in ROLES, 'ROLE_FORBIDDEN', '角色不可读取Skill')
        if role == 'admin':
            return FAMILY_SKILLS | INTERNAL_SKILLS | EXTENSION_SKILLS
        if role.startswith('planner_'):
            return {'plan-proactive-service'}
        return FAMILY_SKILLS | EXTENSION_SKILLS | {'close-service-case'}

    def list(self, role):
        return {'skills': [{**self.entries[name],
                            'aliases': [a for a, canonical in ALIASES.items() if canonical == name]}
                           for name in sorted(self._allowed(role) & self.approved)],
                'automatic_triggers_enabled': False}

    def read(self, role, skill_id):
        require(re.fullmatch('[a-z][a-z0-9-]*', skill_id) is not None,
                'SKILL_PATH_FORBIDDEN', '仅接受Skill目录编号')
        canonical = ALIASES.get(skill_id, skill_id)
        require(canonical in self._allowed(role) and canonical in self.approved, 'SKILL_SCOPE_FORBIDDEN', 'Skill不在当前角色范围')
        entry = self.entries[canonical]
        path = _contained(self.root, self.root / 'skills' / canonical / 'SKILL.md')
        current = path.read_text(encoding='utf-8')
        require(hashlib.sha256(current.encode()).hexdigest() == entry['sha256'],
                'SKILL_REVISION_CHANGED', 'Skill读取前已变更，请重新加载审核目录')
        result = self.native_reader(canonical)
        require(isinstance(result, dict) and result.get('success') is True,
                'SKILL_RUNTIME_UNAVAILABLE', '原生Skill读取未成功')
        require(isinstance(result.get('_source_path'), str)
                and Path(result['_source_path']).resolve() == path
                and result.get('content') == current,
                'SKILL_SOURCE_MISMATCH', '原生读取未命中批准的Skill原文')
        guides = []
        for name in entry['required_guides']:
            guide = _contained(self.root, self.root / 'guides' / (name + '.md'))
            body = guide.read_text(encoding='utf-8')
            require(hashlib.sha256(body.encode()).hexdigest() == self.guide_hashes[name],
                    'SKILL_REVISION_CHANGED', '必读规范已变更，请重新加载审核目录')
            guides.append({'guide_id': name, 'content': body,
                           'sha256': hashlib.sha256(body.encode()).hexdigest()})
        return {'requested_skill_id': skill_id, 'canonical_skill_id': canonical, 'content': current,
                'sha256': entry['sha256'], 'required_guides': guides,
                'runtime_constraints': V1_CONSTRAINTS, 'native_reader': 'Hermes.skill_view(preprocess=False)',
                'automatic_triggers_enabled': False}

    def dispatch(self, name, args, role):
        if name == 'skills.list':
            require(not args, 'INVALID_SCHEMA', 'Skill目录不接受参数')
            return self.list(role)
        require(name == 'skills.read', 'TOOL_NOT_FOUND', 'Skill工具不存在')
        return self.read(role, SkillRead.model_validate(args).skill_id)
