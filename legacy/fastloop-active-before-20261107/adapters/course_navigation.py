"""Read-only navigation over the registered Fox course revision, without eager bodies."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import knowledge_search as knowledge

CATALOG=Path(__file__).with_name('course_resources.json')
DETAILS=('abstract','overview','full')


def catalog():
    return json.loads(CATALOG.read_text(encoding='utf8'))


def nodes():
    data=catalog();root=data['root_uri']
    common={k:data[k] for k in ('revision','class_type','book_title','business_review_status')}
    result={root:{**common,'uri':root,'title':data['book_title'],'node_type':'directory','record_type':'book'}}
    for doc in data['documents']:
        week=root+'/week-'+str(doc['week_no']).zfill(2)
        result[week]={**common,'uri':week,'title':data['book_title']+' 第'+str(doc['week_no'])+'周',
                      'week_no':doc['week_no'],'node_type':'directory','record_type':'week'}
        directory=doc['uri'].rsplit('/',1)[0]
        result[directory]={**common,**doc,'uri':directory,'node_type':'directory','content_uri':doc['uri'],
                           'content_sha256':doc['sha256']}
        result[doc['uri']]={**common,**doc,'node_type':'file'}
    return result


def resolve(uri=None):
    if uri is None:uri=catalog()['root_uri']
    if not isinstance(uri,str):raise ValueError('需要课程知识地址')
    parsed=urlsplit(uri)
    if parsed.scheme!='viking' or parsed.netloc!='resources' or parsed.query or parsed.fragment or '%' in uri or '\\' in uri:
        raise ValueError('仅支持已登记的课程知识地址')
    uri=uri.rstrip('/')
    item=nodes().get(uri)
    if item is None:raise ValueError('该地址不在当前已登记课程版本中')
    return item


def entry_points(class_type,lesson_no):
    data=catalog()
    doc=next((d for d in data['documents'] if d['class_type']==class_type and d['lesson_no']==lesson_no),None)
    if doc is None:return None
    return {'book_title':doc['book_title'],'revision':data['revision'],
            'book_uri':data['root_uri'],'week_uri':doc['uri'].rsplit('/',2)[0],
            'lesson_uri':doc['uri'].rsplit('/',1)[0],'content_uri':doc['uri'],
            'available_details':list(DETAILS)}


def course_browse(uri=None,offset=0,limit=20):
    item=resolve(uri)
    if item['node_type']!='directory':raise ValueError('目录浏览需要目录地址')
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=30:
        raise ValueError('offset需非负整数，limit需为1至30')
    try:
        rows=knowledge.cli(['ls',item['uri'],'--offset',str(offset),'--limit',str(limit)])
        if not isinstance(rows,list):raise ValueError()
        registered=nodes();children=[]
        for row in rows:
            if not isinstance(row,dict):continue
            child=registered.get(row.get('uri','').rstrip('/'))
            if child is None or child['uri'].rsplit('/',1)[0]!=item['uri']:continue
            # File hashes are provenance, never a reason to fetch every body here.
            value={**child,'available_details':['abstract','overview'] if child['node_type']=='directory' else ['full']}
            if isinstance(row.get('abstract'),str):value['abstract']=row['abstract']
            children.append(value)
        return {**item,'status':'ok','live_read':True,'children':children,'offset':offset,
                'next_offset':offset+len(rows) if len(rows)==limit else None}
    except Exception:
        return {**item,'status':'unavailable','children':[],'reason':'课程目录暂时不可读'}


def course_read(uri,detail='overview'):
    item=resolve(uri)
    if detail not in DETAILS:raise ValueError('detail仅支持abstract、overview、full')
    requested_uri=item['uri']
    if detail=='full' and item['node_type']=='directory':
        if not item.get('content_uri'):raise ValueError('书目和周目录包含多份资料，请先浏览并选择具体材料')
        item=resolve(item['content_uri'])
    elif detail!='full' and item['node_type']=='file':
        item=resolve(item['uri'].rsplit('/',1)[0])
    command={'abstract':'abstract','overview':'overview','full':'read'}[detail]
    try:
        value=knowledge.cli([command,item['uri']])
        if isinstance(value,dict):value=value.get('content',value.get(command))
        if not isinstance(value,str) or not value.strip():raise ValueError()
        sha=hashlib.sha256(value.encode()).hexdigest()
        if detail=='full' and sha!=item['sha256']:
            return {**item,'status':'changed','detail':detail,'reason':'正文版本变化，需核对课程索引'}
        return {**item,'status':'ok','detail':detail,'requested_uri':requested_uri,'body':value,'sha256':sha,
                'live_read':True,'exact_match':detail=='full',
                'usage':'摘要和概览可用于定位与理解课程目标；具体情节、练习和教学步骤请读取对应正文核实。' if detail!='full' else '对应来源正文；课程内容不代表孩子已掌握。'}
    except Exception:
        return {**item,'status':'unavailable','detail':detail,'reason':'该层课程资料暂时不可读'}


def course_search(query,scope_uri=None,limit=5):
    scope=resolve(scope_uri)
    if scope['node_type']!='directory':raise ValueError('检索范围需要目录地址')
    if not isinstance(query,str) or not query.strip() or len(query)>500 or type(limit) is not int or not 1<=limit<=10:
        raise ValueError('检索问题需为1至500字，limit需为1至10')
    try:
        found=knowledge.cli(['find',query.strip(),'--uri',scope['uri'],'--level','0,1,2','--limit',str(min(30,limit*3))])
        if not isinstance(found,dict) or not isinstance(found.get('resources'),list):raise ValueError()
        registered=nodes();matches=[];seen=set()
        for row in found['resources']:
            if not isinstance(row,dict):continue
            uri=row.get('uri','')
            if not isinstance(uri,str):continue
            if uri.endswith(('/.abstract.md','/.overview.md')):uri=uri.rsplit('/',1)[0]
            item=registered.get(uri.rstrip('/'))
            if item is None or not (item['uri']==scope['uri'] or item['uri'].startswith(scope['uri']+'/')):continue
            if item['uri'] in seen:continue
            seen.add(item['uri'])
            value={**item,'abstract':row.get('abstract','') if isinstance(row.get('abstract',''),str) else '',
                   'available_details':['abstract','overview'] if item['node_type']=='directory' else ['full']}
            if type(row.get('score')) in (int,float):value['score']=row['score']
            matches.append(value)
            if len(matches)>=limit:break
        return {'status':'ok' if matches else 'no_match','query':query,'scope_uri':scope['uri'],
                'revision':scope['revision'],'matches':matches,'live_read':True}
    except Exception:
        return {'status':'unavailable','query':query,'scope_uri':scope['uri'],'matches':[],
                'reason':'课程检索暂时不可用'}


def matching_material(trace,class_type,lesson_no,full_only=False):
    """Accept either reader with the same lesson binding; summaries cannot replace full-only uses."""
    from course_service import entry
    item=entry(class_type,lesson_no)
    if not item:return None
    for call in reversed(trace):
        args=call.get('arguments',{});result=call.get('result',{})
        if call['name']=='course_knowledge' and args.get('class_type')==class_type and args.get('lesson_no')==lesson_no:
            return result
        if call['name']!='course_read':continue
        exact=args.get('class_type')==class_type and args.get('lesson_no')==lesson_no and 'uri' not in args
        detail=args.get('detail','full' if exact else 'overview')
        expected=item['uri'] if detail=='full' else item['uri'].rsplit('/',1)[0]
        if full_only and detail!='full':continue
        requested=item['uri'] if exact else args.get('uri','')
        if (detail in DETAILS and isinstance(requested,str) and requested.rstrip('/') in (item['uri'],item['uri'].rsplit('/',1)[0]) and result.get('uri')==expected
                and result.get('class_type')==class_type and result.get('lesson_no')==lesson_no
                and result.get('detail')==detail):return result
    return None
