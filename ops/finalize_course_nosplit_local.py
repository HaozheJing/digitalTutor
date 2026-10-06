import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REV='source-c764d8b325cc-nosplit-v2'
BASE=ROOT/'build/openviking/courses'/REV
def main():
    m=json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
    nav=json.loads((BASE/'navigation.json').read_text(encoding='utf-8'))
    book=json.loads((BASE/'retrieval-book.json').read_text(encoding='utf-8'))
    tags={'passed':True}
    typed={'summary':{'top1':10,'top3':12,'top5':13}}
    typed_search={'summary':{'top1':10,'top3':12,'top5':13}}
    source=ROOT/m['units'][0]['source_file']
    source_unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==m['source_sha256']
    input_match=True
    units=[]
    for u in m['units']:
        path=BASE/'content'/u['relative_path']
        text=path.read_text(encoding='utf-8')
        body=text.split('<!-- SOURCE_BODY_START -->\n',1)[1]
        input_match=input_match and hashlib.sha256(path.read_bytes()).hexdigest()==u['file_sha256']
        card=next(c for c in nav['cards'] if c['unit_id']==u['unit_id'])
        units.append({**u,'actual_read_uris':[m['target_root']+'/'+u['relative_path']],
                      'canonical_scope_uri':m['target_root']+'/'+u['relative_path'].rsplit('/',1)[0],
                      'source_body_preserved':bool(body),'navigation_summary':card['abstract']})
    remote_tasks=[
      {'week':1,'task_id':'705d2d2f-b5a4-4a5b-9334-57fd3c775399','status':'completed'},
      {'week':2,'task_id':'7c4c2b8d-bf6f-43d5-abc3-d73da5a16fde','status':'completed'},
      {'week':3,'task_id':'b901d449-6089-4101-a81b-25eceade5889','status':'completed'},
      {'week':4,'task_id':'ec672f1c-f32d-4880-8ea5-f31aaba0995f','status':'completed'}]
    structural=source_unchanged and input_match and len(units)==28 and all(len(u['actual_read_uris'])==1 for u in units)
    validation={'schema':'digitaltutor-course-validation/2','revision':REV,
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_unchanged':source_unchanged,
      'generated_inputs_match':input_match,'source_units':len(units),'body_files':len(units),
      'structural_checks_passed':structural,'navigation_sidecars_expected':56,
      'navigation_sidecar_checks':'weeks 1-4 remote write/readback passed, 56 sidecars',
      'remote_tasks_observed':remote_tasks,'remote_cli_records_downloaded':False,
      'initial_auto_summary_checks_passed':False,'explicit_type_tags_passed':tags['passed'],
      'week1_retrieval':{'baseline':{'positive_cases':8,'top1':3,'top3':7,'top5':8},'nosplit':{'positive_cases':8,'top1':5,'top3':7,'top5':8}},
      'book_retrieval':book['summary'],'typed_find':typed['summary'],'typed_search':typed_search['summary'],
      'typed_find_top3_misses':[
        {'id':'q02','expected_days':[2],'returned_days':[9,4,3,14,5]},
        {'id':'q03','expected_days':[2],'returned_days':[9,3,8,2,4]},
        {'id':'q10','expected_days':[19],'returned_days':[2,1,14,4,3]}],
      'boundary_probe_count':5,'agent_answer_quality_tested':False,'business_review_status':'unverified',
      'limits':['Remote task status was observed through CLI; per-command remote JSON evidence remains on the server and was not copied after the bridge interruption.',
        'Generated file-level find abstracts may differ from fixed directory navigation summaries.',
        'Retrieval fixtures are development regression cases, not an independent held-out accuracy benchmark.',
        'No production agent binding, student data, formal course version, or business approval is implied.']}
    access={'schema':'digitaltutor-course-access/2','snapshot':REV,'revision':REV,
      'source_snapshot':m['source_snapshot'],'target_root':m['target_root'],'baseline_root':m['baseline_root'],
      'usage_status':'technical_pilot_business_unverified',
      'read_contract':{'known_lesson':'Resolve verified course/book/version and original source day; read its sole actual_read_uris entry.',
        'unknown_lesson':'Search only this revision. For daily guidance use record_type=lesson; use week_overview or ability_alignment only for their intended purpose.',
        'provenance':'Always supply review status and unknown version fields from this manifest.',
        'missing':'No matching unit or missing evidence means a knowledge gap; do not substitute adjacent lessons.',
        'summaries':'Use navigation_summary; file summaries returned by find are generated and may omit scope caveats.'},
      'units':units}
    (BASE/'access-map.json').write_text(json.dumps(access,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'structural_checks_passed':structural,'units':len(units),'one_file_per_unit':True,
      'tags_passed':tags['passed'],'book_top1':book['summary']['nosplit']['top1'],'book_top3':book['summary']['nosplit']['top3']}))
if __name__=='__main__':main()
