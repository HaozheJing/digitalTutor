"""Authenticated context selection. Data stays scoped to the actor and cutoff."""
import json
from reply_lab import require, digest


def presets(lab):
    path = lab.studio.root / 'runtime/quality-source-snapshot/active-presets.json'
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else []


def dispatch(lab, actor, path, data):
    if path == 'contexts':
        seen, items = set(), []
        for row in lab.versions(actor, 'scenario'):
            if row['mode'] != 'passive' or row['origin'] not in {'real_redacted', 'real_transformed'} or row['split'] != 'development' or row['name'] in seen:
                continue
            seen.add(row['name'])
            p = lab.version(actor, row['id'])['payload']
            items.append({'id': row['id'], 'label': p['family_group'].replace('family-', '家庭 ')+' · '+p['as_of'][5:10]+' · '+p.get('category', '真实对话')})
        return {'items': list(reversed(items)), 'presets': [{k: r[k] for k in ['id', 'title', 'description']} for r in presets(lab)]}
    if path == 'context-load':
        row = lab.version(actor, data['id'], 'scenario')
        require(row['payload']['origin'] in {'real_redacted', 'real_transformed'} and row['payload']['split'] == 'development', '请选择真实开发对话。')
        # Reference answer never belongs to the context-loading UI.
        row['payload'].pop('reference', None)
        return row
    preset = next((r for r in presets(lab) if r['id'] == data['id']), None)
    require(preset is not None, '主动 Skill 预设不存在。', 404)
    payload = preset['payload']
    name = '主动试测 · '+preset['title']
    old = next((v for v in lab.versions(actor, 'scenario') if v['name'] == name), None)
    if old and old['digest'] == digest({**payload, 'schema_version': 1}):
        return lab.version(actor, old['id'])
    return lab.scenario_save(actor, {'name': name, 'payload': payload, 'parent': old['id'] if old else None,
                                    'expected': old['id'] if old else None, 'note': '已预设日期和测试资料；任务种子仅用于表达试测'})


def passive_fixture(s):
    """Explicitly absent learning data, never borrow another year's learning."""
    day = s['as_of'][:10]
    return {'uid': s['family_group'], 'window': {'start': day, 'end': day},
            'profile': {'uid': s['family_group'], 'child_nickname': '', 'guardian_role': 'unknown', 'birthday': None,
                        'class_type': 'UNKNOWN', 'cohort': 'UNKNOWN',
                        'data_gaps': s.get('gaps', []) + ['同期课程和学情未提供，空集合不代表孩子未学习。']},
            'enrollments': [], 'course_calendar': [{'date': day, 'is_course_day': False, 'lesson_ids': [], 'source': 'unavailable_no_contemporaneous_data'}],
            **{k: [] for k in ['learning_daily', 'learning_events', 'quiz_attempts', 'expression_assignments', 'weekly_rankings', 'books', 'highlight_assets']}}
