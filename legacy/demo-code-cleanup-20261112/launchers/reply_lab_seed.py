"""Seed private, reviewed-source candidates; never publish or call a model."""
import argparse
import json
from pathlib import Path

from teacher_workbench import TeacherStudio
from reply_lab import ReplyLab

PERSONA_ADDITION = '''
把本轮连续消息合起来理解，以家长最后确认的需求和条件为准。已有回答、尝试和约定会影响本次判断，不每轮重新开场。
先解决眼前的问题，再决定是否需要解释或建议。普通确认可以简短结束，确实缺少会改变处理方式的信息时才追问。
区分可见行为、家长自述与推断。解释具体事实为什么与当前问题有关，不能把一次完课或答对夸大为能力提升。
家长纠正时直接修正；明确拒绝时尊重当次范围。涉及取消、改时、交付的承诺，以工具实际保存和最终回执为准。
保留当前授权服务边界，不增加回访、七天复查、新事项类型或联系频次。不会通过虚构真人经历来拉近距离。
'''.strip()

STYLE_ADDITION = '''

## 回复实验候选补充（保留上述全部人工规则）

先把本轮连续消息与此前已确认的安排连起来。后来的补充改变条件时，按新条件回应，不继续回答已被纠正的问题。
遇到纠正，直接承认并修正具体错误，省去重复的解释和固定共情。家长已回答的问题不用再次追问。
先给本次需要的答案，再挑一条真正有帮助的事实或建议。课程资料缺失时保留缺口，不猜题干、原图或孩子表现。
操作结果不明确时，具体说清当前能确认的状态。受理不等于送达，提出调整不等于已经保存。
事情说清楚就自然停住，不为了显得热情增加表扬、提问、表情或新的家庭任务。每段称呼、字数、标点及身份规则全部保留。
'''


def synthetic_cases():
    specs = [
        ('连续消息修正', 'passive', ['今天七点可以', '我刚看了安排，七点不行，八点才有空']),
        ('纠正错误理解', 'passive', ['不是孩子不愿意读，是今天还没时间']),
        ('缺少图片原件', 'passive', ['您能看看孩子这张作业图吗？测试条件只有图片占位，原件不可用']),
        ('确认后自然结束', 'passive', ['好的，我知道了，谢谢']),
        ('取消需真实结果', 'mixed', ['今天的阅读提醒不用发了']),
        ('到点已完成', 'proactive_expression', ['合成条件：执行前权威完课查询已完成，应复核是否无需联系']),
        ('无适用服务的规划', 'planning', ['合成条件：06:30规划，当前没有适用的授权服务机会']),
        ('部分交付与未知回执', 'mixed', ['合成条件：文本已sent，表情unknown，不应重发文本或宣称全部送达']),
    ]
    for index, (name, mode, messages) in enumerate(specs, 1):
        yield {'name': 'boundary-'+str(index).zfill(2)+'-'+name, 'note': '合成工程边界，等待B线原生事件映射',
               'payload': {'mode': mode, 'origin': 'synthetic', 'review': 'candidate',
                           'family_group': 'synthetic-family-'+str(index), 'case_family': 'boundary-'+str(index),
                           'split': 'development', 'as_of': '2025-11-07T21:30:00+08:00', 'history': [],
                           'messages': [{'role': 'user', 'content': t, 'at': '2025-11-07T21:30:00+08:00'} for t in messages],
                           'facts': [], 'events': [], 'initial_tasks': [], 'source_refs': [], 'transformations': [],
                           'gaps': ['描述性契约fixture，尚未映射为原生任务或信号，不构成流程验收'], 'reference': ''}}


def seed(root, cases, persona):
    if root == Path('/home/ubuntu/digitalTutor-dsh-lab') or not root.is_absolute():
        raise ValueError('An isolated absolute root is required')
    studio = TeacherStudio(root)
    lab = ReplyLab(studio)
    actor = {'id': 'workbench-owner', 'name': '工作台管理员', 'admin': True}
    try:
        existing = {v['name'] for v in lab.versions(actor, 'scenario')}
        for row in [*cases, *synthetic_cases()]:
            if row['name'] not in existing: lab.scenario_save(actor, row)
        existing_configs = {v['name'] for v in lab.versions(actor, 'config')}
        for name, extra_persona, extra_style in [('现场快照基线', '', ''), ('当前问题优先候选', PERSONA_ADDITION, STYLE_ADDITION)]:
            if name in existing_configs: continue
            files = studio.source('reply-style')
            files['SKILL.md'] += extra_style
            lab.config_save(actor, {'name': name, 'note': '保留人工正文与规则，候选仅附加交流判断，未人工验收',
                                   'payload': {'persona': persona + ('\n'+extra_persona if extra_persona else ''), 'complete': False,
                                               'primary_skill': 'tutor-main', 'overrides': {'reply-style': files},
                                               'model': 'deepseek-v4.1-flash-economy', 'parameters': {}, 'examples': [], 'assets': []}})
        configs = lab.versions(actor, 'config')
        if len(configs) == 2:
            diff = lab.dispatch(actor, 'diff', {'left': configs[1]['id'], 'right': configs[0]['id']})
            (lab.home / 'candidate.diff').write_text(diff['diff'], encoding='utf8')
        return {'configs': configs, 'scenario_count': len(lab.versions(actor, 'scenario')), 'approved_examples': 0,
                'native_runs': 0, 'real_channel_runs': 0, 'runtime_root': str(lab.home)}
    finally:
        lab.close(); studio.pool.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--persona', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(seed(args.root.resolve(), json.loads(args.cases.read_text(encoding='utf8')),
                          json.loads(args.persona.read_text(encoding='utf8'))['prefix']), ensure_ascii=False, indent=2))
