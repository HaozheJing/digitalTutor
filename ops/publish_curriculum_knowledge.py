"""Publish and verify an immutable curriculum revision; does not alter runtime skill catalogs."""
from pathlib import Path
import argparse, concurrent.futures, hashlib, json
from curriculum_knowledge import Curriculum, ov

def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['import','status','verify'])
    p.add_argument('--bundle',required=True);p.add_argument('--config',required=True)
    a=p.parse_args();base=Path(a.bundle);m=json.loads((base/'manifest.json').read_text());target=m['target_root']
    assert target.startswith('viking://resources/digitaltutor/curriculum/L2/')
    docs=m['documents'];assert len(docs)==308 and len({d['uri'] for d in docs})==308
    for d in docs:assert sha((base/'content'/d['relative_path']).read_bytes())==d['sha256']
    call=lambda argv:ov(argv,a.config,timeout=180)
    receipt=base/'import-receipt.json'
    if a.action=='import':
        if receipt.exists():
            old=json.loads(receipt.read_text());assert old['target_root']==target
            print(json.dumps({'status':'existing_receipt','task_id':old['result']['task_id']}));return
        try:call(['stat',target])
        except RuntimeError as e:
            if str(e)!='OpenViking operation failed: NOT_FOUND':raise
        else:raise ValueError('Target already exists; refuse overwrite without receipt')
        sources=[d for d in docs if d.get('source_uri')]
        def verify_source(d):
            body=call(['read',d['source_uri']]);body=body.get('content') if isinstance(body,dict) else body
            return {'uri':d['source_uri'],'matches':isinstance(body,str) and sha(body.encode())==d['source_body_sha256']}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:checks=list(pool.map(verify_source,sources))
        save(base/'source-verification.json',checks);assert len(checks)==268 and all(c['matches'] for c in checks)
        assets=[]
        for d in docs:
            if d.get('asset_path'):
                path=Path('/home/ubuntu/digitalTutor-five-family-20261030')/d['asset_path']
                assets.append({'path':d['asset_path'],'matches':path.is_file() and sha(path.read_bytes())==d['asset_sha256']})
        save(base/'asset-verification.json',assets);assert len(assets)==24 and all(x['matches'] for x in assets)
        result=call(['add-resource',str(base/'content'),'--to',target,'--args','parse_mode:no_split',
                     '--tags','knowledge_type=curriculum,course_level=L2,revision=current-20261107-v1',
                     '--instruction','主导航严格按L2、全课程周W1到W48、周内Day1到Day5组织，同一坐标代表同一课。reading-qa是引读题和来源答案，course是剧情与能力方法，reading-guide是原图索引。每周另有总览、能力对标和周总结。books仅为书籍辅助索引。保留全部原文与来源审核状态，原页码和77期起页码列不能混用；不推断家庭完成状态。不要按书内天数替代全课程坐标。',
                     '--no-progress'])
        save(receipt,{'target_root':target,'result':result});assert result.get('task_id')
        print(json.dumps({'status':'submitted','task_id':result['task_id'],'documents':len(docs)}));return
    old=json.loads(receipt.read_text());status=call(['task','status',old['result']['task_id']]);save(base/'task-status.json',status)
    meta=(status.get('result') or {}).get('meta') or {}
    if a.action=='status':
        print(json.dumps({'status':status.get('status'),'stage':status.get('stage'),'processed':len(meta.get('processed_files',[])),
                          'failed':len(meta.get('failed_files',[])),'has_error':bool(status.get('error'))}));return
    assert status['status']=='completed' and not status.get('error')
    assert not (status.get('result') or {}).get('errors') and not any(meta.get(k) for k in ['failed_files','skipped_files','unsupported_files'])
    tree=call(['tree',target,'--level-limit','10','--node-limit','3000']);save(base/'tree.json',tree)
    actual={x['uri'] for x in tree if not x['isDir'] and not x['uri'].rsplit('/',1)[-1].startswith('.')}
    assert actual=={d['uri'] for d in docs}
    client=Curriculum(base/'manifest.json',a.config)
    def verify_body(d):
        body=client.read(d['document_id'])
        return {'document_id':d['document_id'],'uri':d['uri'],'sha256':sha(body['body'].encode()),'matches':True}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:checks=list(pool.map(verify_body,docs))
    save(base/'body-verification.json',checks)
    exact=[]
    for l in m['lessons']:
        result=client.lesson('L2',l['week'],l['day'])
        assert result['global_lesson_no']==(l['week']-1)*5+l['day']
        assert result['resources']['reading_qa']['lesson_id']==l['lesson_id']
        exact.append({'lesson_id':l['lesson_id'],'uri':result['resources']['reading_qa']['uri'],'matches':True})
    save(base/'coordinate-verification.json',exact)
    books=[]
    for b in m['books']:
        found=client.book(b['title']);assert found['lesson_ids']==b['lesson_ids']
        books.append({'book_id':b['book_id'],'lesson_count':len(found['lessons']),'matches':True})
    save(base/'book-verification.json',books)
    cases=json.loads((base/'retrieval-cases.json').read_text());results=[]
    for case in cases:
        hits=client.search(case['query'],limit=5,book=case.get('book'),kind=case.get('kind'))
        ids=[h['document_id'] for h in hits]
        results.append({**case,'hit_ids':ids,'top1':bool(ids) and ids[0]==case['expected_document_id'],'top5':case['expected_document_id'] in ids})
    save(base/'retrieval-verification.json',results)
    summary={'revision':m['revision'],'target_root':target,'status':'verified' if all(c['top5'] for c in results) else 'retrieval_needs_review',
             'exact_body_matches':len(checks),'exact_lesson_lookups':len(exact),'book_lookups':len(books),
             'retrieval_cases':len(results),'retrieval_top1':sum(c['top1'] for c in results),'retrieval_top5':sum(c['top5'] for c in results),
             'coverage':m['coverage'],'runtime_skill_activation':False}
    save(base/'verification.json',summary);print(json.dumps(summary,ensure_ascii=False))
    assert all(c['top5'] for c in results),'Content retrieval needs review'

if __name__=='__main__':main()
