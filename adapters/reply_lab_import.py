"""Read-only batch importer. Raw input and extracted cases stay outside Git.

Usage: python reply_lab_import.py --pointer .../TEST_DATA_CURRENT.json --output <private-new-directory>
Requires openpyxl for read-only extraction, never rewrites a workbook.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(pointer):
    data = json.loads(pointer.read_text(encoding='utf8'))
    manifest = (pointer.parent / data['manifest']).resolve()
    if not manifest.is_relative_to(pointer.parent.resolve()):
        raise ValueError('Manifest path escapes data root')
    if sha(manifest) != data['manifest_sha256']:
        raise ValueError('Manifest digest mismatch')
    m = json.loads(manifest.read_text(encoding='utf8'))
    for f in m['files']:
        path = (manifest.parent / f['path']).resolve()
        if not path.is_relative_to(manifest.parent) or path.is_symlink():
            raise ValueError('Input path escapes manifest root')
        if sha(path) != f['sha256'] or path.stat().st_size != f['bytes']:
            raise ValueError('Input digest or size mismatch')
    return manifest.parent, m, data['manifest_sha256']


def category(text):
    for name, pattern in [('拒绝与暂停', r'不用|不需要|不想|别发|不参加|暂停'),
                          ('时间安排', r'明天|周末|星期|周[一二三四五六日]|没时间|有空|几点|晚上'),
                          ('学习困难', r'不会|不懂|不愿|困难|太难|听不|读不|不爱'),
                          ('纠正与质疑', r'不是|不对|怎么回事|为什么|明明|没有收到'),
                          ('孩子分享', r'喜欢|有趣|开心|读完|完成|看完'),
                          ('自然结束', r'谢谢|好的|收到|知道了|好哒')]:
        if re.search(pattern, text):
            return name
    return '普通答疑'


def extract(pointer, output):
    from openpyxl import load_workbook
    root, manifest, manifest_hash = verify(pointer)
    output.mkdir(parents=True, exist_ok=False)
    entries = sorted((f for f in manifest['files'] if f['role'] == 'test_unstructured_cleaned'), key=lambda f: f['sha256'])
    cases, mapping, counts, exclusions = [], [], Counter(), Counter()
    total_rows = 0
    for index, f in enumerate(entries):
        family = 'family-' + str(index + 1).zfill(2)
        split = ['examples', 'examples', 'development', 'development', 'blind', 'blind'][index]
        mapping.append({'family_group': family, 'uid': f['uid'], 'path': f['path'], 'sha256': f['sha256'], 'split': split})
        workbook = load_workbook(root / f['path'], read_only=True, data_only=True)
        sheet = workbook['聊天记录']
        raw = list(sheet.values)
        if list(raw[0]) != ['日期', '时间', '发送人', '具体内容', '类型（微信/电话）']:
            raise ValueError('Unrecognized chat schema')
        if len(raw) - 1 != f['message_rows']:
            raise ValueError('Chat row count mismatch')
        total_rows += len(raw) - 1
        names = set()
        for row in raw[1:]:
            names.update(re.findall(r'([\u4e00-\u9fff]{1,5})(?:妈妈|爸爸|宝贝)', str(row[3])))
        # These are candidate replacements, not a claim of complete anonymization.
        names -= {'孩子', '小朋友', '家长', '给', '和', '的', '让', '谢谢', '好的'}
        def clean(text):
            text = re.sub(r'https?://\S+', '[链接已隐藏]', text)
            text = re.sub(r'(?<!\d)\d{5,}(?!\d)', '[账号或号码已隐藏]', text)
            text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱已隐藏]', text)
            for name in sorted(names, key=len, reverse=True):
                text = text.replace(name, '小朋友')
            return text
        messages = []
        for rownum, row in enumerate(raw[1:], 2):
            day, clock, speaker, body, channel = row
            if channel != '微信' or speaker not in {'客户', '老师'} or not isinstance(body, str):
                exclusions['telephone_or_unknown_role'] += 1
                # A barrier prevents joining dialogue across unknown material.
                messages.append(None)
                continue
            at = datetime.combine(day.date(), clock).replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
            messages.append({'role': 'user' if speaker == '客户' else 'assistant', 'content': clean(body), 'at': at, 'row': rownum})
        candidates = []
        i = 0
        while i < len(messages):
            if not messages[i] or messages[i]['role'] != 'user':
                i += 1
                continue
            begin = i
            day = messages[i]['at'][:10]
            while i < len(messages) and messages[i] and messages[i]['role'] == 'user' and messages[i]['at'][:10] == day:
                i += 1
            current = messages[begin:i]
            if i >= len(messages) or not messages[i] or messages[i]['role'] != 'assistant' or messages[i]['at'] < current[-1]['at']:
                continue
            reference = []
            j = i
            while j < len(messages) and messages[j] and messages[j]['role'] == 'assistant' and messages[j]['at'][:10] == day:
                reference.append(messages[j]);j += 1
            text = '\n'.join(m['content'] for m in current)
            if len(text) < 3 or len(text) > 1500 or not reference:
                continue
            if re.search(r'续费|优惠|名额|付款|支付|红包|学费|价格|购买|报名', text + '\n'.join(m['content'] for m in reference)):
                exclusions['sales_scope'] += 1
                continue
            if not re.search(r'[\u4e00-\u9fff]', re.sub(r'\[[^]]*\]', '', text)):
                exclusions['media_without_text'] += 1
                continue
            history = []
            for m in reversed(messages[max(0, begin - 8):begin]):
                if not m or m['at'][:10] != day:
                    break
                history.insert(0, m)
            if any(m['at'] > current[-1]['at'] for m in history):
                exclusions['nonmonotonic_time'] += 1
                continue
            candidates.append((category(text), history, current, reference))
        # First cover observed categories, then add chronologically separated
        # exchanges. Never synthesize records to reach a target sample count.
        selected, seen, occupied = [], set(), set()
        for diversified in [True, False]:
            for cat, history, current, ref in candidates:
                if len(selected) >= 8:
                    break
                rows = {m['row'] for m in history + current + ref}
                if rows & occupied or (diversified and cat in seen):
                    continue
                selected.append((cat, history, current, ref));seen.add(cat);occupied |= rows
        for n, (cat, history, current, ref) in enumerate(selected, 1):
            case_id = f'{family}-case-{n:02}'
            strip = lambda ms: [{k: m[k] for k in ['role', 'content', 'at']} for m in ms]
            p = {'schema_version': 1, 'mode': 'passive', 'origin': 'real_transformed', 'review': 'candidate',
                 'family_group': family, 'case_family': case_id, 'split': split, 'category': cat,
                 'as_of': current[-1]['at'], 'history': strip(history), 'messages': strip(current),
                 'facts': [], 'events': [], 'initial_tasks': [],
                 'source_refs': [{'batch': manifest['batch_id'], 'manifest_sha256': manifest_hash,
                                  'file_sha256': f['sha256'], 'sheet': sheet.title,
                                  'visible_rows': [m['row'] for m in history + current],
                                  'reference_rows': [m['row'] for m in ref], 'role_source': '发送人列：客户/老师；仅微信'}],
                 'transformations': ['身份称呼候选替换，仍需人工隐私复核', '链接、账号号码与邮箱隐藏', '按家庭冻结划分', '同日上下文截取，参考回复留在评测侧', '未拼接2026年学情'],
                 'reference': '\n'.join(m['content'] for m in ref),
                 'reference_kind': '原始老师回复的加工候选，非标准答案',
                 'gaps': ['真实素材隐私与质量待人工复核', '未确认孩子昵称及家长身份，不得猜测', '媒体占位不代表有原件', '缺少同期课程及学情事实']}
            cases.append({'name': case_id + '-' + cat, 'note': '来源核验后的待审候选', 'payload': p})
            counts[split] += 1
        workbook.close()
    report = {'batch_id': manifest['batch_id'], 'manifest_sha256': manifest_hash, 'verified_files': len(manifest['files']),
              'chat_rows': total_rows, 'families': len(entries), 'case_count': len(cases), 'by_split': dict(counts),
              'by_category': dict(Counter(c['payload']['category'] for c in cases)), 'excluded': dict(exclusions),
              'approved_examples': 0, 'raw_modified': False, 'privacy_review_pending': True,
              'limitations': ['Top3高发言家庭，非总体代表样本', '自动加工候选，不能称优秀示例', '2025年聊天未与2026年学情合并', '家庭映射和原件路径只保存在受控目录']}
    for name, value in [('cases.json', cases), ('private-source-map.json', mapping), ('report.json', report)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pointer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(extract(args.pointer.resolve(), args.output.resolve()), ensure_ascii=True, indent=2))
