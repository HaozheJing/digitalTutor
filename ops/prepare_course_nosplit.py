"""Prepare a new processing revision; preserve every original source section."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'source-c764d8b325cc'
REVISION = BASELINE + '-nosplit-v2'


def main():
    old = ROOT / 'build/openviking/courses' / BASELINE
    out = old.parent / REVISION
    manifest = json.loads((old / 'manifest.json').read_text(encoding='utf-8'))
    source = ROOT / manifest['units'][0]['source_file']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == manifest['source_sha256']
    units = []
    for u in manifest['units']:
        previous = (old / 'content' / u['relative_path']).read_text(encoding='utf-8')
        body = previous.split('---\n\n', 1)[1]
        assert hashlib.sha256(body.encode()).hexdigest() == u['source_body_sha256_lf']
        scope = f"第{u['source_day']}天" if u['source_day'] else f"第{(u['source_week']-1)*5+1}至{u['source_week']*5}天"
        kind = {'lesson': '课程正文', 'week_overview': '课程总览', 'ability_alignment': '能力对标说明'}[u['record_type']]
        unit_dir = f"day-{u['source_day']:02d}" if u['source_day'] else {'week_overview':'week-overview','ability_alignment':'ability-alignment'}[u['record_type']]
        rel = f"week-{u['source_week']:02d}/{unit_dir}/content.md"
        prefix = (f'# L2《了不起的狐狸爸爸》{scope}{kind}\n\n'
                  f'资料编号：{u["unit_id"]}。适用范围：L2《了不起的狐狸爸爸》{scope}。\n\n'
                  '来源声明：内容由AI生成，尚未业务审核；正式课程版本、书籍出版版本及班期课节映射未知。'
                  '以下是课程设计资料，不能据此认定任何学生已经掌握相应能力。\n\n'
                  '<!-- SOURCE_BODY_START -->\n')
        text = prefix + body
        target = out / 'content' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            assert target.read_text(encoding='utf-8') == text, 'Immutable processing revision differs'
        target.write_text(text, encoding='utf-8', newline='\n')
        units.append({**u, 'relative_path': rel, 'display_title': f'L2《了不起的狐狸爸爸》{scope}{kind}',
                      'file_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                      'generated_prefix_characters': len(prefix)})
    result = {**manifest, 'snapshot': REVISION, 'source_snapshot': BASELINE, 'processing_revision':'nosplit-v2',
              'target_root': manifest['target_root'] + '-nosplit-v2', 'baseline_root':manifest['target_root'],
              'parse_mode':'no_split', 'units':units}
    (out/'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'revision':REVISION,'units':len(units),'source_unchanged':True}))


if __name__ == '__main__':
    main()
