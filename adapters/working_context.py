"""Model working sets, separate from immutable audit and full authorization facts.

Never truncate obligations. If the remaining essential set is too large, the
wire budget gate stops generation. Archived pages are evidence, not authority.
"""
from copy import deepcopy
from contextlib import closing
import hashlib,json,sqlite3
import sql_tools as s
import test_mode as tm
FINAL={'done','completed','resolved','closed','cancelled','expired','no_contact'}
PAGE_CHARS=6000
TASK_KEYS=('id','case_id','kind','skill','day','due_at','expires_at','status','revision','candidate_id','dedupe_key','needs_revalidation','execution_token','goal','window_start')

def tasks(rows, day='', selected=()):
    result=[]
    for row in rows:
        terminal=row.get('status') in FINAL
        if terminal and row.get('id') not in selected and (not day or str(row.get('day',row.get('due_at','')))[:10]!=day):continue
        item={k:v for k,v in row.items() if k in TASK_KEYS}
        # Active goals/agreements and failures are essential, never silently clipped.
        if not terminal:
            for k in ('reason','source','result'):
                if k in row:item[k]=row[k]
        result.append(item)
    return result

def ledger(value,day='',selected=()):
    result=deepcopy(value);kept=[];feedback=[]
    for case in result.get('cases',[]):
        dialogue=[x for x in case.get('timeline',[]) if x.get('kind')=='family_feedback' or
                  isinstance(x.get('body'),dict) and isinstance(x['body'].get('source'),dict) and x['body']['source'].get('owner',{}).get('type')=='dialogue']
        protected=any(k not in {'id','status','timeline','history','tasks'} for k in case) or any(t.get('status') in {'unknown','accepted','partial','awaiting_delivery'} for t in case.get('tasks',[]))
        if case.get('status') in FINAL and case.get('id') not in selected and not protected:
            if dialogue:feedback.append({'case_id':case.get('id'),'status':case.get('status'),'family_evidence':dialogue})
            continue
        case.pop('history',None)
        case['timeline']=dialogue
        # Receipt status remains program-owned; retain the current linked result.
        case['tasks']=[{k:v for k,v in t.items() if k in ('id','kind','due_at','status','result','event_id','code')} for t in case.get('tasks',[])]
        kept.append(case)
    result['cases']=kept;result['family_evidence']=feedback
    result.pop('plans',None);result.pop('runs',None)
    result['history_access']=('完整历史见context_archive；evidence_read按引用分页读取。' if __import__('tool_surface').enabled('knowledge') else '完整历史见context_archive；context_page按页读取。')+'暂停、家庭原话及未结事项保留；交付前程序仍核验全量回执。'
    return result

def project(value,day=''):
    result=deepcopy(value)
    day=day or str(result.get('business_time',result.get('business_day','')))[:10]
    selected=[result.get('task_id'),result.get('case_id'),(result.get('owner') or {}).get('id')]
    for key in ('tasks','workflow_versions'):
        if isinstance(result.get(key),list):result[key]=tasks(result[key],day,selected)
    for key in ('ledger','service_ledger'):
        if isinstance(result.get(key),dict):result[key]=ledger(result[key],day,selected)
    for entry in result.get('catalog',[]):
        entry.pop('body',None);entry.pop('references',None)
        entry['content_access']={'tool':'skill_read' if __import__('capability_surface').enabled() else 'service_skill','name':entry['skill'],'note':'这里是规划契约与实时机会；业务正文未装载，需要时读取，执行时完整装载。'}
    if isinstance(result.get('service_agenda'),dict) and isinstance(result['service_agenda'].get('tasks'),list):
        result['service_agenda']['tasks']=tasks(result['service_agenda']['tasks'],day,selected)
    # Repeated full learning facts have a single owner; never synthesize a summary.
    if isinstance(result.get('workflow_facts'),dict) and result['workflow_facts'].get('learning')==result.get('learning'):
        result['workflow_facts'].pop('learning',None);result['workflow_facts']['learning_location']='learning'
    return result

def model(value,day=''):
    state=tm.state()
    if not state:return value
    view=project(value,day)
    for key in ('learning',):
        if key in view and len(s.encoded(view[key]))>24000:
            view[key]={'section':key,'requires_read':True,'read':('使用evidence_read按引用读取本次事实快照，可指定path子字段；提交前仍核验完整事实版本。' if __import__('tool_surface').enabled('knowledge') else '使用context_page读取本次事实快照；可指定path读取子字段。提交前仍核验完整事实版本。'),'fields':list(view[key]) if isinstance(view[key],dict) else None}
    if view==value:return view
    body=s.encoded(value);scope=[state['run_id'],state['uid']]
    ident=hashlib.sha256(s.encoded([scope,body]).encode()).hexdigest()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('CREATE TABLE IF NOT EXISTS model_context_archive(id TEXT PRIMARY KEY,run_id TEXT,uid TEXT,body TEXT)')
        c.execute('INSERT OR IGNORE INTO model_context_archive VALUES(?,?,?,?)',(ident,*scope,body))
    view['context_archive']={'context_id':ident,'sections':list(value),'page_chars':PAGE_CHARS,
        'read':('evidence_read(ref={kind:context,id:context_id},section,offset=0)，沿next_offset读取；快照只作溯源。' if __import__('tool_surface').enabled('knowledge') else 'context_page(context_id,section,offset=0)，沿next_offset读取完整历史；此快照只作溯源，当前事实需刷新原接口。')}
    return view

def page(context_id,section,offset=0,path=None):
    if type(offset) is not int or offset<0:raise ValueError('invalid_context_offset')
    state=tm.state()
    if not state:raise ValueError('bound_family_required')
    rows=s.read('SELECT body FROM model_context_archive WHERE id=? AND run_id=? AND uid=?',(context_id,state['run_id'],state['uid']))
    if not rows:raise ValueError('context_not_in_current_family_run')
    data=json.loads(rows[0]['body'])
    if section not in data:raise ValueError('unknown_context_section')
    selected=data[section]
    if path is not None:
        if not isinstance(path,list) or len(path)>12:raise ValueError('invalid_context_path')
        for key in path:
            if isinstance(selected,dict) and isinstance(key,str) and key in selected:selected=selected[key]
            elif isinstance(selected,list) and type(key) is int and 0<=key<len(selected):selected=selected[key]
            else:raise ValueError('invalid_context_path')
    raw=s.encoded(selected)
    if offset>len(raw):raise ValueError('invalid_context_offset')
    end=min(len(raw),offset+PAGE_CHARS)
    return {'context_id':context_id,'section':section,'offset':offset,'next_offset':end if end<len(raw) else None,
            'total_chars':len(raw),'json_fragment':raw[offset:end],'historical_snapshot':True,'authority':False}
