"""Process-local HTTP observation after SDK serialization, without body logging."""
import json,time,threading
from urllib.parse import urlsplit
from .context_transport import fingerprint,dumps

class RequestMetrics:
    def __init__(self,path,share_context=False,result_reader=None,required_prompt=None,history_references=None,full_prompt=None,source_guard=None,identity=None,system_localizer=None):
        self.path=path;self.lock=threading.Lock();self.counter=0;self.share_context=share_context
        self.result_reader=result_reader;self.required_prompt=required_prompt
        self.history_references=history_references or [];self.full_prompt=full_prompt
        self.source_guard=source_guard;self.identity=identity or {}
        self.system_localizer=system_localizer
    def emit(self,value):
        with self.lock:
            with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
    def install(self):
        import httpx
        old=httpx.Client.send;observer=self
        def send(client,request,*args,**kwargs):
            try:payload=json.loads(request.content)
            except (ValueError,UnicodeError,httpx.RequestNotRead):return old(client,request,*args,**kwargs)
            if not isinstance(payload,dict) or not ('messages' in payload or 'input' in payload):return old(client,request,*args,**kwargs)
            messages=payload.get('messages',[])
            primary=any(str(t.get('function',{}).get('name','')).startswith('dt_') for t in payload.get('tools',[])) if observer.source_guard else any(t.get('function',{}).get('name')=='read_file' for t in payload.get('tools',[]))
            if observer.source_guard and not primary:
                from .context_budget import decoded
                primary=any(decoded(m) is not None for m in messages)
            source_coverage=None;guard_changed=False
            if primary and observer.source_guard:
                payload,source_coverage=observer.source_guard.wire(payload)
                messages=payload['messages'];guard_changed=True
            wire_results=observer.result_reader.audit(messages) if observer.result_reader and primary else []
            prompt_present=None
            if observer.required_prompt and primary:
                prompt_present=any(m.get('role')=='user' and observer.required_prompt in str(m.get('content','')) for m in messages)
                from .domain import require
                require(prompt_present,'CONTEXT_WIRE_MISSING','当前必要输入在实际请求中缺失；停止请求，保留业务责任')
            restored=[];changed=guard_changed
            if primary and observer.history_references:
                from .native_session import missing_wire_references
                restored=missing_wire_references(observer.history_references,messages)
                if restored:
                    require(bool(observer.full_prompt),'CONTEXT_HISTORY_REFERENCE_MISSING','压缩后原文引用缺失且没有完整检查点')
                    for m in messages:
                        if m.get('role')=='user' and observer.required_prompt in str(m.get('content','')):
                            m['content']=m['content'].replace(observer.required_prompt,observer.full_prompt,1);changed=True
                    require(changed,'CONTEXT_HISTORY_REFERENCE_MISSING','找不到本轮原文恢复位置')
            sharing={'shared':False}
            if observer.share_context:
                from .wire_context import share
                payload,sharing=share(payload)
                changed=changed or sharing['shared']
            prompt_language=None
            if primary and observer.system_localizer and any(t.get('function',{}).get('name','').startswith('dt_') for t in payload.get('tools',[])):
                prompt_language=observer.system_localizer.localize(payload);changed=True
            if changed:
                headers=dict(request.headers);headers.pop('content-length',None)
                request=httpx.Request(request.method,request.url,headers=headers,content=dumps(payload).encode(),extensions=request.extensions)
            with observer.lock:observer.counter+=1;seq=observer.counter
            started=time.monotonic();parts={};messages=payload.get('messages',[])
            for m in messages:
                role=m.get('role','unknown');parts[role]=parts.get(role,0)+len(dumps(m))
            observer.emit({**observer.identity,'event':'request','sequence':seq,'started_at_ns':time.time_ns(),
                'request_kind':'primary' if primary else 'auxiliary','source_coverage':source_coverage,'model':payload.get('model'),
                'endpoint_host':urlsplit(str(request.url)).hostname,'endpoint_path':urlsplit(str(request.url)).path,
                'request_sha256':fingerprint(payload),'body_bytes':len(request.content),'message_chars_by_role':parts,'context_sharing':sharing,
                'required_prompt_present':prompt_present,'tool_results':wire_results,
                'history_references_restored':restored,
                'prompt_language':prompt_language,
                'tool_schema_chars':len(dumps(payload.get('tools',[]))),'messages':len(messages),'usage':None,
                'note':'Exact serialized request observed; component sizes are characters, not provider tokens.'})
            try:response=old(client,request,*args,**kwargs)
            except Exception as e:
                observer.emit({'event':'request_error','sequence':seq,'error_type':type(e).__name__,'seconds':time.monotonic()-started});raise
            state={'usage':None,'finish_reasons':[],'buffer':b'','done':False,'sse':False}
            def parse(raw):
                try:v=json.loads(raw)
                except (ValueError,UnicodeError):return
                if not isinstance(v,dict):return
                if isinstance(v.get('usage'),dict):state['usage']=v['usage']
                if isinstance(v.get('response'),dict) and isinstance(v['response'].get('usage'),dict):state['usage']=v['response']['usage']
                for choice in v.get('choices',[]):
                    if choice.get('finish_reason'):state['finish_reasons'].append(choice['finish_reason'])
            def consume(chunk):
                state['buffer']+=chunk
                if state['buffer'].lstrip().startswith(b'data:'):state['sse']=True
                if state['sse']:
                    while b'\n' in state['buffer']:
                        line,state['buffer']=state['buffer'].split(b'\n',1)
                        if line.startswith(b'data:'):parse(line[5:].strip())
                # Bound memory; no model text is retained in the ledger.
                if len(state['buffer'])>4_000_000:state['buffer']=b''
            def finish():
                if state['done']:return
                state['done']=True
                if state['buffer']:parse(state['buffer'].removeprefix(b'data:').strip())
                observer.emit({'event':'response','sequence':seq,'status':response.status_code,
                    'seconds':time.monotonic()-started,'usage':state['usage'],'usage_missing':state['usage'] is None,
                    'finish_reasons':state['finish_reasons']})
            if response.is_stream_consumed:
                consume(response.content);finish()
            else:
                original=response.stream
                class Stream(httpx.SyncByteStream):
                    def __iter__(self):
                        try:
                            for chunk in original:consume(chunk);yield chunk
                        finally:finish()
                    def close(self):
                        try:original.close()
                        finally:finish()
                response.stream=Stream()
            return response
        self.original=old;httpx.Client.send=send
    def close(self):
        import httpx
        httpx.Client.send=self.original
