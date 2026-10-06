"""Generate navigation from source headings and provenance, without new teaching claims."""
import argparse, json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('revision');a=p.parse_args()
    base=ROOT/'build/openviking/courses'/a.revision
    m=json.loads((base/'manifest.json').read_text(encoding='utf-8'));cards=[]
    for u in m['units']:
        raw=(base/'content'/u['relative_path']).read_text(encoding='utf-8')
        body=raw.split('<!-- SOURCE_BODY_START -->\n',1)[1]
        headings=[re.sub(r'^[#*\s]+|\*+$','',line).strip() for line in body.splitlines()
                  if line.startswith('### ')]
        if not headings:
            headings=[re.sub(r'^[#*\s]+|\*+$','',line).strip() for line in body.splitlines() if line.startswith('## ')][:4]
        methods='；'.join(headings)
        abstract=u['display_title']+'。AI生成，未审核；版本未确认。'+methods
        if len(abstract)>250:abstract=abstract[:247]+'……'
        uri=m['target_root']+'/'+u['relative_path'];folder=uri.rsplit('/',1)[0]
        overview=(f'# {u["display_title"]}\n\n{abstract}\n\n'
                  '正式课程版本、书籍出版版本及班期课节映射未知。本文是课程设计资料，不代表任何学生已掌握相关能力。\n\n'
                  '## 内容导航\n\n'+ '\n'.join('- '+h for h in headings)+'\n\n'
                  f'## 完整正文\n\n[读取本单元完整正文]({uri})\n\n'
                  f'来源：{u["source_file"]}，第{u["source_lines"][0]}至{u["source_lines"][1]}行。\n')
        directory=base/'navigation'/u['unit_id'];directory.mkdir(parents=True,exist_ok=True)
        for name,text in [('abstract.md',abstract),('overview.md',overview)]:
            (directory/name).write_text(text,encoding='utf-8')
        cards.append({'unit_id':u['unit_id'],'source_week':u['source_week'],'scope_uri':folder,'read_uri':uri,
                      'abstract':abstract,'overview':overview,'headings':headings})
    (base/'navigation.json').write_text(json.dumps({'revision':a.revision,'generation':'source headings plus provenance only','cards':cards},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cards':len(cards),'maximum_abstract_characters':max(len(c['abstract']) for c in cards)}))

if __name__=='__main__':main()
