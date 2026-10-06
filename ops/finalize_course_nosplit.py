"""Produce a truthful handoff: structural checks separate from retrieval and answer quality."""
import argparse,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('revision');a=p.parse_args()
    b=ROOT/'build/openviking/courses'/a.revision
    def read(name):return json.loads((b/name).read_text(encoding='utf-8'))
    def write(name,data):(b/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    m=read('manifest.json');coverage=read('coverage-w01-w02-w03-w04.json');nav=read('navigation.json')
    cards={c['unit_id']:c for c in nav['cards']};observed={c['unit_id']:c for c in coverage['units']}
    tasks=[]
    for w in range(1,5):
        paths=sorted((b/'cli-records').glob(f'task-w{w:02d}-*.json'))
        record=json.loads(paths[-1].read_text(encoding='utf-8'));r=record['parsed']['result'];result=r.get('result') or {};meta=result.get('meta') or {}
        okay=(record['exit_code']==0 and r['status']=='completed' and not r.get('error')
              and not result.get('errors') and len(meta.get('processed_files',[]))==7
              and not any(meta.get(k) for k in ['failed_files','unsupported_files','skipped_files']))
        tasks.append({'week':w,'passed':okay,'task_id':r['task_id'],'status':r['status'],'evidence':paths[-1].relative_to(b).as_posix()})
    navchecks=[]
    for path in sorted(b.glob('navigation-check-*.json')):
        navchecks.extend(json.loads(path.read_text(encoding='utf-8'))['checks'])
    navkeys={(x['unit_id'],x['kind']) for x in navchecks if x['exact_readback_except_terminal_newline']}
    source=ROOT/m['units'][0]['source_file']
    unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==m['source_sha256']
    inputs_match=all(hashlib.sha256((b/'content'/u['relative_path']).read_bytes()).hexdigest()==u['file_sha256'] for u in m['units'])
    units=[]
    for u in m['units']:
        obs=observed[u['unit_id']];card=cards[u['unit_id']]
        units.append({**u,'actual_read_uris':obs['actual_read_uris'],'canonical_scope_uri':obs['canonical_scope_uri'],
                      'navigation_summary':card['abstract'],'source_body_preserved':obs['source_body_preserved']})
    structure=(unchanged and inputs_match and len(units)==28 and all(t['passed'] for t in tasks)
               and coverage['one_file_per_unit'] and coverage['generated_text_identical'] and coverage['source_body_preserved'])
    navpass=all((u['unit_id'],kind) in navkeys for u in units for kind in ['abstract','overview'])
    week=read('retrieval-week1.json');book=read('retrieval-book.json')
    typed_find=read('retrieval-typed-find.json');typed_search=read('retrieval-typed-search.json');tags=read('retrieval-tags.json')
    validation={'revision':a.revision,'generated_at_utc':datetime.now(timezone.utc).isoformat(),
                'source_snapshot':m['source_snapshot'],'source_unchanged':unchanged,'generated_inputs_match':inputs_match,
                'source_units':len(units),'body_files':sum(len(u['actual_read_uris']) for u in units),
                'structural_checks_passed':structure,'directory_navigation_passed':navpass,'verified_sidecar_count':len(navkeys),
                'initial_auto_generated_summary_checks_passed':coverage['summary_checks_passed'],
                'tasks':tasks,'week1_retrieval':week['summary'],'book_retrieval':book['summary'],
                'explicit_type_tags_passed':tags['passed'],
                'typed_find':typed_find['summary'],'typed_search':typed_search['summary'],
                'typed_find_top3_misses':[{'id':x['id'],'query':x['query'],'expected_days':x['expected_days'],'returned_days':x['days']} for x in typed_find['cases'] if not x['top3']],
                'retrieval_expected_in_top3_all':all(x['top3'] for x in book['cases'] if x['version']=='nosplit' and 'top3' in x),
                'boundary_probe_count':sum(x['version']=='nosplit' and 'boundary' in x for x in book['cases']),
                'agent_answer_quality_tested':False,'business_review_status':'unverified',
                'limits':['File-level find abstracts and ancestor directory summaries remain generated; use navigation_summary plus provenance for agent input.',
                          'Retrieval probes with unknown pages, lessons, versions or student ability do not prove correct agent refusal.',
                          'Natural-language recall is measured on a small fixed set; known lesson queries should use exact scope or direct read.',
                          'No production agent binding or business approval is implied.']}
    access={'schema':'digitaltutor-course-access/2','snapshot':a.revision,'revision':a.revision,'source_snapshot':m['source_snapshot'],
            'target_root':m['target_root'],'baseline_root':m['baseline_root'],
            'usage_status':'technical_pilot_business_unverified',
            'read_contract':{'known_lesson':'Resolve verified course/book/version and original source day; read its sole actual_read_uris entry.',
                             'unknown_lesson':'Search only this revision. For daily teaching guidance, apply record_type=lesson; use week_overview or ability_alignment only for the corresponding purpose. Inspect candidates using navigation_summary and full bodies.',
                             'provenance':'Always supply review status and unknown version fields from this manifest.',
                             'missing':'No matching unit or missing evidence means a knowledge gap. Do not substitute adjacent lessons.',
                             'summaries':'Use navigation_summary; file summaries returned by find are generated and may omit scope caveats.'},'units':units}
    write('access-map.json',access);write('validation.json',validation)
    print(json.dumps({k:v for k,v in validation.items() if k not in ['tasks','limits']},ensure_ascii=False))
    if not structure or not navpass or not tags['passed']:raise SystemExit(1)

if __name__=='__main__':main()
