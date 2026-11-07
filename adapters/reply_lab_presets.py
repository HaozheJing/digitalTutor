"""Build active expression fixtures; no model invocation or channel sending."""
import argparse
import copy
import json
from pathlib import Path

SPECS = [
    ('remind-reading', '课前提醒', '2025-10-13T07:00:00+08:00'),
    ('weekly-learning-review', '周六本周总结', '2025-10-18T09:00:00+08:00'),
    ('invite-reading-interaction', '周互动', '2025-10-17T19:00:00+08:00'),
    ('rebuild-reading-rhythm', '习惯拉升', '2025-10-22T19:00:00+08:00'),
    ('close-week-reading-gap', '本周最后一课', '2025-10-17T18:00:00+08:00'),
    ('plan-weekend-makeup', '周五补读邀请', '2025-10-17T20:00:00+08:00'),
]


def build(fixture):
    rows = []
    for skill, title, at in SPECS:
        f = copy.deepcopy(fixture)
        changed_lessons = set()
        for lesson in f['learning_daily']:
            clear = ((skill == 'rebuild-reading-rhythm' and '2025-10-15' <= lesson['date'] <= '2025-10-22') or
                     (skill in {'close-week-reading-gap', 'plan-weekend-makeup'} and lesson['date'] == '2025-10-17'))
            if clear:
                changed_lessons.add(lesson['id'])
                for key in ['attended_at', 'completed_at', 'checked_in_at']: lesson[key] = None
                f['learning_events'] = [e for e in f['learning_events'] if e.get('lesson_id') != lesson['id']]
                f['quiz_attempts'] = [e for e in f['quiz_attempts'] if e.get('lesson_id') != lesson['id']]
                for e in f['expression_assignments']:
                    if e.get('lesson_id') == lesson['id']: e.update(submitted_at=None, selection_recorded_at=None, answer=None)
        if changed_lessons:
            f['highlight_assets'] = []
            f['weekly_rankings'] = []
            for book in f['books']:
                if changed_lessons.intersection(book['lesson_ids']):
                    book.update(completed_at=None, shelf_lit_at=None)
        rows.append({'id': skill, 'title': title, 'description': at[:16].replace('T', ' ')+' · 预设测试资料（非真实家庭学情）',
                     'payload': {'mode': 'proactive_expression', 'origin': 'synthetic', 'review': 'candidate',
                                 'family_group': f['uid'], 'split': 'development', 'case_family': 'active-'+skill,
                                 'as_of': at, 'history': [], 'messages': [], 'facts': [], 'reference': '', 'source_refs': [],
                                 'transformations': ['首月测试资料；部分完课状态按规则调整以构造触发条件'],
                                 'events': [{'type': 'manual_skill_trigger', 'skill': skill, 'at': at}],
                                 'trigger_skill': skill, 'native_fixture': f, 'initial_tasks': [{'skill': skill, 'run_at': at}],
                                 'gaps': ['这是预设的主动触发条件，不代表真实家庭同期学情。']}})
    return rows


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--fixture', type=Path, required=True)
    args = p.parse_args()
    root = args.root.resolve()
    if root == Path('/home/ubuntu/digitalTutor-dsh-lab'):
        raise SystemExit('Use the isolated worktree.')
    from native_experiments import prepare
    rows = build(json.loads(args.fixture.read_text(encoding='utf8')))
    for row in rows:
        s = row['payload']
        prepare({'config_version': 'preset-preflight', 'persona': 'Only use provided facts.', 'model': 'deepseek-v4.1-flash-economy'},
                {'scenario_version': row['id'], 'family_uid': s['family_group'], 'as_of': s['as_of'], 'mode': 'execution',
                 'fixture': s['native_fixture'], 'history': [], 'initial_tasks': s['initial_tasks']}, source=root, skill_source=root)
    target = root / 'runtime/quality-source-snapshot/active-presets.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf8')
    print(json.dumps({'validated_presets': len(rows), 'native_model_calls': 0}))
