"""Read-only curriculum access: exact L2/W/Day first, book index and content search second."""
from pathlib import Path
import argparse, contextlib, hashlib, json, os, re, subprocess, time, unicodedata

CLI='/home/ubuntu/digitalTutor/runtime/openviking/bin/ov'
TRANSIENT_CODES={'QuotaExceeded','QUOTA_EXCEEDED','RATE_LIMIT_EXCEEDED','RateLimitExceeded','TIMEOUT','Timeout','SERVICE_UNAVAILABLE'}

class KnowledgeUnavailable(RuntimeError):
    def __init__(self,code,attempts=0):
        self.code=code if re.fullmatch(r'[A-Za-z0-9_]+',str(code)) else 'CLI_FAILED'
        self.attempts=attempts
        super().__init__('OpenViking operation failed: '+self.code)

@contextlib.contextmanager
def quota_gate(deadline):
    """Share one in-flight CLI request across all curriculum reader processes for this OS user."""
    path=Path.home()/'.cache/digitalTutor/curriculum-openviking.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0,2)
        if not handle.tell():handle.write(b'0');handle.flush()
        locked=False
        try:
            while not locked:
                try:
                    if os.name=='nt':
                        import msvcrt
                        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    locked=True
                except (BlockingIOError,OSError):
                    if time.monotonic()>=deadline:raise KnowledgeUnavailable('QUEUE_TIMEOUT')
                    time.sleep(.05)
            yield
        finally:
            if locked:
                if os.name=='nt':
                    import msvcrt
                    handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

def decode(text):
    for i,ch in enumerate(text):
        if ch not in '{["':continue
        try:
            value,end=json.JSONDecoder().raw_decode(text[i:])
            if not text[i+end:].strip():return value
        except ValueError:pass
    return None

def ov(args,config,timeout=90):
    env={**os.environ,'OPENVIKING_CLI_CONFIG_FILE':str(config),'NO_COLOR':'1'}
    deadline=time.monotonic()+timeout
    retryable_read=bool(args) and (args[0] in {'read','find','search','ls','tree','stat','abstract','overview'} or args[:2]==['task','status'])
    with quota_gate(deadline):
        for attempt in range(1,5):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise KnowledgeUnavailable('TIMEOUT',attempt-1)
            try:r=subprocess.run([CLI,*args,'-o','json'],env=env,capture_output=True,text=True,timeout=remaining)
            except subprocess.TimeoutExpired:code='TIMEOUT'
            else:
                result=decode(r.stdout) or decode(r.stderr)
                if isinstance(result,dict) and result.get('ok'):return result['result']
                error=result.get('error') if isinstance(result,dict) else None
                code=error.get('code','CLI_FAILED') if isinstance(error,dict) else 'CLI_OUTPUT_UNAVAILABLE'
            if not retryable_read or code not in TRANSIENT_CODES or attempt==4:raise KnowledgeUnavailable(code,attempt)
            delay=.5*(2**(attempt-1))
            if time.monotonic()+delay>=deadline:raise KnowledgeUnavailable(code,attempt)
            time.sleep(delay)

def number(text):
    if text.isdigit():return int(text)
    digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits['两']=2;digits['〇']=0
    if text in digits:return digits[text]
    if text.count('十')==1:
        left,right=text.split('十')
        if (not left or left in digits) and (not right or right in digits):return (digits.get(left,1)*10)+digits.get(right,0)
    raise ValueError('Invalid coordinate number')

def coordinates(query):
    """Parse only explicit level/week/day notation, never infer a book or course identity."""
    text=unicodedata.normalize('NFKC',query)
    numerals=r'[0-9零〇一二两三四五六七八九十]+'
    levels=re.findall(r'(?i)(?<![A-Z0-9])L\s*([0-9]+)(?![0-9])',text)
    weeks=re.findall(r'(?i)(?<![A-Z])W\s*([0-9]+)(?![0-9])',text)
    weeks+=re.findall(r'第?('+numerals+r')\s*周',text)
    days=re.findall(r'(?i)(?<![A-Z])(?:DAY|D)\s*([0-9]+)(?![0-9])',text)
    days+=re.findall(r'第?('+numerals+r')\s*(?:天|日|课)(?![A-Za-z])',text)
    # A bare "第1课" does not identify a curriculum coordinate; the agent needs book/context.
    if not weeks and not (levels and days):return None
    if not levels or not weeks or not days:raise ValueError('Incomplete coordinates: specify level, curriculum week and day')
    level_set={'L'+str(int(x)) for x in levels};week_set={number(x) for x in weeks};day_set={number(x) for x in days}
    if len(level_set)!=1 or len(week_set)!=1 or len(day_set)!=1:raise ValueError('Ambiguous coordinates: select one lesson at a time')
    return next(iter(level_set)),next(iter(week_set)),next(iter(day_set))

class Curriculum:
    def __init__(self,catalog,config=None):
        self.data=json.loads(Path(catalog).read_text(encoding='utf-8'))
        if self.data['schema']!='digitaltutor.curriculum.v1':raise ValueError('Unsupported curriculum schema')
        self.config=config
        self.lessons={l['lesson_id']:l for l in self.data['lessons']}
        self.documents={d['document_id']:d for d in self.data['documents']}
        self.by_uri={d['uri']:d for d in self.data['documents']}
    def books(self):
        return [{'book_id':b['book_id'],'title':b['title'],'weeks':b['weeks'],'lesson_count':len(b['lesson_ids'])} for b in self.data['books']]
    def lesson(self,level,week,day):
        if level!='L2' or type(week) is not int or type(day) is not int or not 1<=week<=48 or not 1<=day<=5:
            raise ValueError('Current coordinates require L2, W1..W48, Day1..Day5')
        result=dict(self.lessons[f'L2-W{week:02d}-D{day}'])
        result['resources']={k:self.documents[v] for k,v in result['resources'].items()}
        w=next(w for w in self.data['weeks'] if w['week']==week)
        result['week_resources']={k:self.documents[v] for k,v in w['resources'].items()}
        return result
    def book(self,name):
        query=name.strip()
        matches=[b for b in self.data['books'] if query in [b['book_id'],b['title'],*b['aliases']]]
        if len(matches)!=1:raise ValueError('Book must match one registered title, alias or ID')
        b=dict(matches[0]);b['lessons']=[self.lessons[k] for k in b['lesson_ids']]
        return b
    def read(self,document_id):
        if document_id not in self.documents:raise ValueError('Unregistered document')
        if not self.config:raise ValueError('Explicit OpenViking config path is required for live reads')
        d=self.documents[document_id];body=ov(['read',d['uri']],self.config)
        if isinstance(body,dict):body=body.get('content')
        if not isinstance(body,str) or hashlib.sha256(body.encode('utf-8')).hexdigest()!=d['sha256']:
            raise ValueError('Published body differs from registered revision')
        return {**d,'body':body}
    def search(self,query,limit=5,book=None,kind=None):
        if not self.config:raise ValueError('Explicit OpenViking config path is required for live search')
        if not isinstance(query,str) or not query.strip() or len(query)>500 or type(limit) is not int or not 1<=limit<=10:
            raise ValueError('Query must be 1..500 characters and limit 1..10')
        kinds={d['kind'] for d in self.data['documents'] if d['uri'].startswith(self.data['search_root']+'/')}
        if kind and kind not in kinds:raise ValueError('Unsupported content kind')
        b=self.book(book) if book else None
        coords=coordinates(query)
        if coords:
            lesson=self.lesson(*coords)
            if b and lesson['book_id']!=b['book_id']:raise ValueError('Book and explicit curriculum coordinates conflict')
            available={**lesson['resources'],**lesson['week_resources']}
            chosen=([available[kind]] if kind in available else []) if kind else list(available.values())
            return [{**d,'match_type':'exact_coordinate'} for d in chosen[:limit]]
        scopes=[f'{self.data["search_root"]}/W{w:02d}' for w in b['weeks']] if b else [self.data['search_root']]
        matches={}
        for scope in scopes:
            response=ov(['find',query,'--uri',scope,'--limit','30','-L','2'],self.config)
            for hit in response.get('resources',[]):
                uri=hit.get('uri','').rstrip('/');d=self.by_uri.get(uri) or self.by_uri.get(uri+'/content.md')
                if not d or not d['uri'].startswith(scope+'/'):continue
                if b and d.get('book_id')!=b['book_id']:continue
                if kind and d['kind']!=kind:continue
                score=hit.get('score');score=score if isinstance(score,(int,float)) else 0
                old=matches.get(d['document_id'])
                if not old or score>old['score']:matches[d['document_id']]={**d,'score':score,'match_type':'semantic_candidate'}
        return sorted(matches.values(),key=lambda x:(-x['score'],x['document_id']))[:limit]

def main():
    p=argparse.ArgumentParser();p.add_argument('--catalog',default=str(Path(__file__).resolve().parents[1]/'knowledge/curriculum/L2/catalog.json'))
    p.add_argument('--config',default=os.environ.get('TUTOR_KNOWLEDGE_CONFIG_FILE'))
    sub=p.add_subparsers(dest='action',required=True)
    lesson=sub.add_parser('lesson');lesson.add_argument('level');lesson.add_argument('week',type=int);lesson.add_argument('day',type=int)
    lesson.add_argument('--kind');lesson.add_argument('--read',action='store_true')
    book=sub.add_parser('book');book.add_argument('name')
    search=sub.add_parser('search');search.add_argument('query');search.add_argument('--book');search.add_argument('--kind');search.add_argument('--limit',type=int,default=5)
    a=p.parse_args();client=Curriculum(a.catalog,a.config)
    if a.action=='lesson':
        result=client.lesson(a.level,a.week,a.day)
        if a.read and not a.kind:p.error('--read requires --kind; select only the required material')
        if a.kind:
            doc=result['resources'].get(a.kind) or result['week_resources'].get(a.kind)
            result=({'status':'not_available','lesson_id':result['lesson_id'],'kind':a.kind} if doc is None else
                    client.read(doc['document_id']) if a.read else doc)
    elif a.action=='book':result=client.book(a.name)
    else:result=client.search(a.query,a.limit,a.book,a.kind)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
