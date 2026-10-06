"""Generate lossless, source-traceable course units; never rewrite source prose."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'knowledge/lesson/【数字员工】L2-课程大纲.md'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    raw = SOURCE.read_bytes()
    text = raw.decode('utf-8-sig').replace('\r\n', '\n')
    lines = text.splitlines(keepends=True)
    snapshot = 'source-' + sha(raw)[:12]
    out = ROOT / 'build/openviking/courses' / snapshot
    bundle = out / 'content'
    headers = [(i, line.strip()[2:]) for i, line in enumerate(lines) if line.startswith('# ')]
    units = []
    covered = set()
    for pos, (start, heading) in enumerate(headers):
        if pos == 0:
            continue
        end = headers[pos + 1][0] if pos + 1 < len(headers) else len(lines)
        note = next((i for i in range(start, end) if lines[i].strip() == '#（注：内容由AI生成）'), None)
        if note is not None:
            end = note
        day = re.fullmatch(r'第(\d+)天', heading)
        span = re.search(r'第(\d+)-(\d+)天', heading)
        if day:
            number = int(day[1]); week = (number - 1) // 5 + 1
            kind = 'lesson'; rel = f'week-{week:02d}/day-{number:02d}/content.md'
            unit_id = f'LESSON-L2-FOX-D{number:02d}'
        elif span:
            number = None; week = (int(span[1]) - 1) // 5 + 1
            kind = 'week_overview' if heading.endswith('总览') else 'ability_alignment'
            filename = 'week-overview.md' if kind == 'week_overview' else 'ability-alignment.md'
            rel = f'week-{week:02d}/{filename}'
            unit_id = f'L2-FOX-W{week:02d}-{kind.upper()}'
        else:
            raise ValueError(f'Unrecognized source section: {heading}')
        body = ''.join(lines[start:end])
        assert not covered.intersection(range(start, end))
        covered.update(range(start, end))
        metadata = {
            'unit_id': unit_id, 'record_type': kind, 'course_level': 'L2',
            'book_title': '了不起的狐狸爸爸', 'source_day': number, 'source_week': week,
            'course_content_version': None, 'book_edition': None, 'actual_lesson_id': None,
            'cohort_mapping': None, 'source_declares_ai_generated': True,
            'business_review_status': 'unverified', 'source_snapshot': snapshot,
            'source_file': SOURCE.relative_to(ROOT).as_posix(),
            'source_lines': [start + 1, end], 'source_sha256': sha(raw),
            'source_body_sha256_lf': sha(body.encode('utf-8')),
        }
        frontmatter = '---\n' + ''.join(f'{k}: {json.dumps(v, ensure_ascii=False)}\n' for k, v in metadata.items()) + '---\n\n'
        target = bundle / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = frontmatter + body
        if target.exists() and target.read_text(encoding='utf-8') != rendered:
            raise ValueError(f'Refusing to overwrite differing generated file: {target}')
        target.write_text(rendered, encoding='utf-8', newline='\n')
        assert target.read_text(encoding='utf-8').split('---\n\n', 1)[1] == body
        units.append({**metadata, 'source_heading': heading, 'relative_path': rel,
                      'file_sha256': sha(target.read_bytes()), 'body_characters': len(body)})
    assert len(units) == 28
    assert sorted(x['source_day'] for x in units if x['record_type'] == 'lesson') == list(range(1, 21))
    omitted = [(i+1, line.strip()) for i, line in enumerate(lines) if i not in covered and line.strip()]
    assert omitted == [(1, '# 【数字员工】L2-课程大纲'), (len(lines), '#（注：内容由AI生成）')], omitted
    manifest = {'schema': 'digitaltutor-course-import/1', 'snapshot': snapshot,
                'source_sha256': sha(raw), 'source_line_count': len(lines),
                'target_root': f'viking://resources/digitaltutor/courses/L2/fantastic-mr-fox/{snapshot}',
                'source_notice': '内容由AI生成；正式课程版本、出版版本和真实课节映射尚未核实。',
                'source_coverage_check': 'PASS: all 28 section bodies preserved; document title and notice carried as metadata',
                'units': units}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'snapshot': snapshot, 'units': len(units), 'out': str(out), 'source_coverage': 'PASS'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
