"""Real tool-calling model evaluation, isolated from delivery and family databases."""
from pathlib import Path
import argparse,concurrent.futures,datetime,hashlib,json,os,re,sys,time,urllib.error,urllib.request

def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def digest(v):return hashlib.sha256(v).hexdigest()
def runtime_connection(family_root,output):
    import yaml
    profile=family_root/'runtime/dsh-home/profiles/web/cordis.patch.yml'
    entries=yaml.safe_load(profile.read_text());model_config=next(x['config'] for x in entries if isinstance(x,dict) and isinstance(x.get('config'),dict) and 'model' in x['config'] and 'provider' in x['config'])
    providers={}
    for x in entries:
        if isinstance(x,dict) and isinstance(x.get('config'),dict):providers.update(x['config'].get('providers',{}))
    # Reuse the project's authorized provider binding in a separate proxy without a family workspace.
    # The production proxy owns family scheduling/accounting and must not serve isolated test requests.
    runtime=Path('/home/ubuntu/digitalTutor-five-family-runtime-20261030')
    operator=json.loads((runtime/'operator.private.json').read_text())
    if model_config['model'] not in operator['allowed_models']:raise RuntimeError('MODEL_NOT_ALLOWED')
    sys.path.append('/home/ubuntu/digitalTutor-five-family-20261030/adapters')
    from experiment_parameter_proxy import ParameterProxy
    proxy=ParameterProxy(operator['base_url'],operator['api_key'],model_config['model'],
        {'thinking':'disabled','temperature':0,'max_tokens':8192},output/'model-transport-audit.jsonl',image_workspace=None)
    proxy.start()
    return {'model':model_config['model'],'base':proxy.url,'key':proxy.token,'profile_sha256':digest(profile.read_bytes()),'proxy':proxy,'upstream_secret':operator['api_key']}

def model_call(connection,messages,tools):
    body={'model':connection['model'],'messages':messages,'tools':tools,'tool_choice':'auto',
          'max_tokens':8192,'temperature':0,'thinking':{'type':'disabled'},'reasoning_effort':'none'}
    req=urllib.request.Request(connection['base'].rstrip('/')+'/chat/completions',data=json.dumps(body,ensure_ascii=False).encode(),
        headers={'Authorization':'Bearer '+connection['key'],'Content-Type':'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=120) as response:data=json.load(response)
            return data['choices'][0]['message'],data.get('usage',{}),digest(json.dumps(body,ensure_ascii=False,sort_keys=True).encode())
        except urllib.error.HTTPError as e:
            code=e.code
            if code not in [429,502,503,504] or attempt==2:raise RuntimeError('MODEL_HTTP_'+str(code)) from None
            time.sleep(2**attempt)
        except (TimeoutError,urllib.error.URLError):
            if attempt==2:raise RuntimeError('MODEL_TRANSPORT_UNAVAILABLE') from None
            time.sleep(2**attempt)

def grade(case,final,events):
    reasons=[]
    if not final:return {'passed':False,'reasons':['no_valid_final_result']}
    if final['status'] not in case['statuses']:reasons.append('unexpected_status')
    if case.get('scope') and final['scope']!=case['scope']:reasons.append('wrong_scope')
    evidence={x['document_id'] for x in final['evidence']}
    read={e['result'].get('document_id') for e in events if e['result'].get('status')=='source'}
    if case.get('documents') and not (set(case['documents']) & read):reasons.append('expected_document_not_read')
    if case.get('documents') and final['status']=='answered' and not (set(case['documents']) & evidence):reasons.append('expected_document_not_cited')
    for term in case.get('answer_terms',[]):
        if term not in final['answer']:reasons.append('missing_answer_term:'+term)
    if case.get('selected_book'):
        if not any(e['name']=='curriculum_books' for e in events):reasons.append('book_catalog_not_consulted')
        if not any(e['name']=='curriculum_book' and e['arguments'].get('book_id')==case['selected_book'] for e in events):reasons.append('book_id_not_confirmed')
    if case.get('no_search') and any(e['name'] in ['curriculum_search','curriculum_read','curriculum_lesson'] for e in events):reasons.append('out_of_scope_used_course_retrieval')
    return {'passed':not reasons,'reasons':reasons}

def main():
    p=argparse.ArgumentParser();p.add_argument('--code',required=True);p.add_argument('--catalog',required=True);p.add_argument('--config',required=True)
    p.add_argument('--family-root',required=True);p.add_argument('--cases',required=True);p.add_argument('--output',required=True);p.add_argument('--only');p.add_argument('--workers',type=int,default=2)
    a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,a.code);from curriculum_knowledge import Curriculum
    from curriculum_agent_tools import AgentTools,SYSTEM,TOOLS
    connection=runtime_connection(Path(a.family_root),out);cases=json.loads(Path(a.cases).read_text())
    if a.only:cases=[c for c in cases if c['id'] in a.only.split(',')]
    assert cases
    freeze={'model':connection['model'],'profile_sha256':connection['profile_sha256'],'cases_sha256':digest(Path(a.cases).read_bytes()),
            'entry_sha256':digest((Path(a.code)/'curriculum_knowledge.py').read_bytes()),'tool_contract_sha256':digest((Path(a.code)/'curriculum_agent_tools.py').read_bytes()),
            'system_prompt':SYSTEM,'tools':TOOLS,'cases':cases,'parameters':{'thinking':'disabled','temperature':0,'max_tokens':8192},
            'isolation':'Read-only tools only; synthetic context; no message tools, shell tools or family DB tools. Model selected from active F01 profile; dedicated project ParameterProxy uses authorized operator binding with no family workspace.'}
    frozen=out/'frozen.json'
    if frozen.exists():assert json.loads(frozen.read_text())==freeze
    else:save(frozen,freeze)
    def run(case):
        path=out/(case['id']+'.json')
        if path.exists():return json.loads(path.read_text())
        start=time.monotonic();toolset=AgentTools(Curriculum(a.catalog,a.config));calls=[];error=None
        messages=[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps({'question':case['question'],'user_context':case.get('context',{})},ensure_ascii=False)}]
        try:
            for turn in range(10):
                message,usage,request_sha=model_call(connection,messages,TOOLS)
                calls.append({'turn':turn+1,'usage':usage,'request_sha256':request_sha,'tool_names':[c['function']['name'] for c in message.get('tool_calls',[])]})
                # Provider reasoning, if any, is passed back only in memory and is not included in persisted evidence.
                messages.append(message)
                if not message.get('tool_calls'):
                    messages.append({'role':'user','content':'请用curriculum_finish提交最终结果；若证据不足，应明确澄清或资料缺口。'});continue
                for call in message['tool_calls']:
                    try:arguments=json.loads(call['function']['arguments'])
                    except (ValueError,TypeError):arguments=None
                    result=toolset.dispatch(call['function']['name'],arguments)
                    messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
                    if toolset.finished:break
                if toolset.finished:break
        except Exception as e:
            # Persist only a safe class / fixed HTTP code, never URLs or provider payloads.
            error=str(e) if re.fullmatch(r'MODEL_[A-Z_0-9]+',str(e)) else type(e).__name__
        final=toolset.finished;verdict=grade(case,final,toolset.events)
        record={'id':case['id'],'question':case['question'],'context':case.get('context',{}),'model':connection['model'],
                'final':final,'events':toolset.events,'model_calls':calls,'error':error,'grade':verdict,'elapsed_seconds':round(time.monotonic()-start,2)}
        # Credential scrubbing is a final safeguard even though model requests/headers are never recorded.
        text=json.dumps(record,ensure_ascii=False).replace(connection['key'],'[REDACTED]').replace(connection['upstream_secret'],'[REDACTED]')
        save(path,json.loads(text));print(json.dumps({'case':case['id'],'passed':verdict['passed'],'status':final.get('status') if final else None,'error':error,'seconds':record['elapsed_seconds']},ensure_ascii=False),flush=True)
        return record
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:records=list(pool.map(run,cases))
    finally:connection['proxy'].close()
    summary={'model':connection['model'],'cases':len(records),'passed':sum(r['grade']['passed'] for r in records),'failed':[{'id':r['id'],'grade':r['grade'],'error':r['error']} for r in records if not r['grade']['passed']],
             'model_requests':sum(len(r['model_calls']) for r in records),'actual_tool_calls':sum(len(r['events']) for r in records),
             'code_sha256':freeze['entry_sha256'],'tool_contract_sha256':freeze['tool_contract_sha256'],
             'no_alias_registry_added':True,'production_skill_activation':False}
    save(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
