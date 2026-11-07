"""Read-only fast-loop contract. No family selection, storage initialization or delivery."""
from datetime import datetime, timezone

PARTS=('reading_qa','course','reading_guide','week_overview','ability_alignment','weekly_review')

def schedule_coordinates(value,level,lessons):
    """Join an actual schedule result to the published catalog; never synthesize a schedule."""
    if isinstance(value,list):return [schedule_coordinates(x,level,lessons) for x in value]
    if not isinstance(value,dict):return value
    result={k:schedule_coordinates(v,level,lessons) for k,v in value.items()}
    if 'lesson_no' in value:
        number=value['lesson_no'];actual_level=value.get('class_type',level)
        match=next((x for x in lessons if x['level']==actual_level and type(number) is int and x['global_lesson_no']==number),None)
        if value.get('has_lesson') is False or ('has_lesson' in value and value['has_lesson'] is None):match=None
        result['curriculum_ref']={k:match[k] for k in ('lesson_id','level','week','day')} if match else None
        result['curriculum_mapping']='registered' if match else 'not_available'
    return result

def provenance(value, kind, historical=False):
    return {**value, 'context_contract': {'source_kind':kind, 'observed_at':datetime.now(timezone.utc).isoformat(),
            'historical':historical, 'grants_execution_authority':False,
            'instruction':'Keep original source timestamps, revisions and pagination. Observation time is not fact time.'}}

def conversation_args(args):
    if not isinstance(args,dict) or set(args)-{'source_id','business_day','snapshot','offset'}:
        raise ValueError('invalid_conversation_selector')
    if 'source_id' in args:
        if set(args)!={'source_id'} or not isinstance(args['source_id'],str) or not args['source_id'].strip():
            raise ValueError('source_and_window_are_mutually_exclusive')
        return 'conversation_read',args
    offset=args.get('offset',0)
    if type(offset) is not int or offset<0 or (offset and not args.get('snapshot')):
        raise ValueError('continuation_requires_snapshot')
    for key in ('business_day','snapshot'):
        if key in args and (not isinstance(args[key],str) or not args[key]):raise ValueError('invalid_window_field')
    return 'conversation_window_read',args

class Knowledge:
    def __init__(self,curriculum,business_catalog,business_read,business_search):
        self.c=curriculum;self.catalog=business_catalog;self.read_business=business_read;self.search_business=business_search

    def call(self,name,args):
        try:
            result=getattr(self,name.removeprefix('knowledge_'))(**args)
            return provenance(result,'public_knowledge')
        except (ValueError,KeyError,TypeError):
            return {'status':'invalid_request','reason':'invalid_or_conflicting_registered_selector'}
        except Exception as error:
            # Never return provider stderr or configuration values.
            if type(error).__name__=='KnowledgeUnavailable':return {'status':'unavailable','reason':'knowledge_provider_unavailable'}
            raise

    def browse(self,scope='curriculum',book_id=None):
        if scope=='curriculum':
            if book_id is None:return {'status':'catalog','books':self.c.books(),'revision':self.c.data['search_root']}
            if book_id not in {x['book_id'] for x in self.c.books()}:raise ValueError()
            return {'status':'catalog','book':self.c.book(book_id),'revision':self.c.data['search_root']}
        if scope not in ('faq','product') or book_id is not None:raise ValueError()
        return {'status':'catalog','documents':[{k:d[k] for k in ('id','title','kind','sha256') if k in d}
                for d in self.catalog()['documents'] if d['kind']==scope]}

    def search(self,query,scope='faq',limit=3,book_id=None,kind=None):
        if scope=='curriculum':
            if book_id is not None and book_id not in {x['book_id'] for x in self.c.books()}:raise ValueError()
            matches=[{**m,'read_args':{'target':{'document_id':m['document_id']}}} for m in self.c.search(query,limit,book_id,kind)]
            return {'status':'candidates' if matches else 'no_match','matches':matches,'is_answer':False,'requires_read':True}
        if scope not in ('faq','product','emoji') or book_id is not None or kind is not None:raise ValueError()
        result=self.search_business(query,scope,limit)
        if scope=='emoji':return result
        return {**result,'matches':[{k:v for k,v in m.items() if k!='body'} for m in result.get('matches',[])],
                'is_answer':False,'requires_read':True}

    def read(self,target=None,parts=None,document_id=None):
        if document_id is not None:
            if target is not None or parts is not None:raise ValueError()
            target={'document_id':document_id}
        if not isinstance(target,dict):raise ValueError()
        if set(target)=={'document_id'}:
            ident=target['document_id']
            if parts is not None and (ident not in self.c.documents or parts != [self.c.documents[ident]['kind']]):raise ValueError()
            if ident in self.c.documents:return self.document(ident)
            if any(d['id']==ident for d in self.catalog()['documents']):return self.read_business(ident)
            return {'status':'not_found','reason':'document_not_registered'}
        if set(target)=={'level','week','day'}:lesson=self.c.lesson(**target)
        elif set(target)=={'book_id','book_lesson_no'}:
            b=self.browse(book_id=target['book_id'])['book'];n=target['book_lesson_no']
            if type(n) is not int or not 1<=n<=len(b['lessons']):raise ValueError()
            item=b['lessons'][n-1];lesson=self.c.lesson('L2',item['week'],item['day'])
        else:raise ValueError()
        if not isinstance(parts,list) or not parts or len(parts)!=len(set(parts)) or any(p not in PARTS for p in parts):raise ValueError()
        available={**lesson['resources'],**lesson['week_resources']};results={}
        for part in parts:
            results[part]=self.document(available[part]['document_id']) if part in available else {'status':'not_available'}
        statuses=[x['status'] for x in results.values()]
        status='ok' if all(x=='ok' for x in statuses) else 'partial' if 'ok' in statuses else 'unavailable' if 'unavailable' in statuses else 'changed' if 'changed' in statuses else 'not_available'
        return {'status':status,'lesson_id':lesson['lesson_id'],'parts':results}

    def document(self,ident):
        try:return {**self.c.read(ident),'status':'ok','source_role':'curriculum_material','is_learner_fact':False}
        except ValueError:return {'status':'changed','document_id':ident,'reason':'registered_revision_not_verified'}
        except Exception as error:
            if type(error).__name__=='KnowledgeUnavailable':return {'status':'unavailable','document_id':ident}
            raise

def definitions(schema):
    s={'type':'string'};i={'type':'integer','minimum':1};scope={'type':'string','enum':['curriculum','faq','product','emoji']}
    target={'oneOf':[schema({'document_id':s},['document_id']),schema({'level':s,'week':i,'day':i},['level','week','day']),schema({'book_id':s,'book_lesson_no':i},['book_id','book_lesson_no'])]}
    return [
      ('knowledge_browse','查看正式目录及书内课次映射，不加载正文。',schema({'scope':{'type':'string','enum':['curriculum','faq','product']},'book_id':s})),
      ('knowledge_search','检索候选；显式课程坐标精确匹配。候选不是答案，需读取正文。emoji沿用现行权限。',schema({'query':s,'scope':scope,'limit':{'type':'integer','minimum':1,'maximum':5},'book_id':s,'kind':{'type':'string','enum':list(PARTS)}},['query'])),
      ('knowledge_read','按文档或L2/W/Day或书内课次精确读取。课次必填parts；文档直接使用搜索结果read_args，不需parts。逐项报告缺失和来源。',schema({'target':target,'document_id':s,'parts':{'type':'array','minItems':1,'uniqueItems':True,'items':{'type':'string','enum':list(PARTS)}}})),
      ('conversation_read','读取原话或窗口，两种选择互斥；续页保留snapshot和offset。',schema({'source_id':s,'business_day':s,'snapshot':s,'offset':{'type':'integer','minimum':0}}))]

def install(server):
    replaced={'course_browse','course_search','course_read','knowledge_browse','knowledge_search','knowledge_read','conversation_read','conversation_window_read'}
    server.base_mcp.TOOLS=[t for t in server.base_mcp.TOOLS if t[0] not in replaced]+definitions(server.schema)

def live_call(name,args):
    import sys,os
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    if str(root/'ops') not in sys.path:sys.path.insert(0,str(root/'ops'))
    from curriculum_knowledge import Curriculum
    import knowledge_search
    config=os.environ.get('TUTOR_KNOWLEDGE_CONFIG_FILE',str(root/'config/source-enterprise-ovcli.conf'))
    client=Curriculum(root/'knowledge/curriculum/L2/catalog.json',config)
    return Knowledge(client,knowledge_search.catalog,knowledge_search.read_document,knowledge_search.search).call(name,args)
