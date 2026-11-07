"""Run-local streaming forwarding seam for controls absent from the native SDK.

Keeps the native agent, messages, tools and delivery unchanged. No retry, no
prompt logging, no arbitrary destination. Only the frozen experiment model.
"""
import hmac,json,secrets,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit
from model_parameters import apply,validate,snapshot
from reply_phases import pipeline, request_for_phase
from request_budget import check as check_budget, BudgetExceeded

class UsageReader:
    """Retain usage/finish metadata only, including split UTF-8/SSE chunks."""
    def __init__(self, streaming=True):
        self.buffer=b'';self.streaming=streaming;self.usage=None;self.finished=False;self.finish_reasons=set();self.visible=False
    def record(self, raw):
        try: value=json.loads(raw)
        except (ValueError,UnicodeError): return
        if value.get('usage'):self.usage=value['usage']
        for choice in value.get('choices',[]):
            if choice.get('finish_reason'):self.finished=True;self.finish_reasons.add(choice['finish_reason'])
            message=choice.get('delta',choice.get('message',{}))
            if message.get('content') or message.get('tool_calls'):self.visible=True
    def feed(self, chunk):
        self.buffer+=chunk
        if self.streaming:
            while b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1)
                if line.startswith(b'data:'):self.record(line[5:].strip())
        if len(self.buffer)>2*1024*1024:raise ValueError('oversized_model_frame')
    def finish(self):
        if self.buffer:
            self.record(self.buffer.removeprefix(b'data:').strip())

class ParameterProxy:
    def __init__(self, upstream, api_key, model, parameters, audit, port=0, reply_pipeline=None, phase_state=None, image_workspace=None):
        parsed=urlsplit(upstream)
        if parsed.scheme!='https' or parsed.hostname!='ark.cn-beijing.volces.com' or parsed.path.rstrip('/')!='/api/plan/v3' or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('仅支持已授权的方舟 Agent Plan 地址')
        self.parameters=validate(model,parameters)
        self.pipeline=pipeline(model,reply_pipeline)
        self.phase_state=Path(phase_state) if phase_state else None
        if self.pipeline and self.phase_state is None:raise ValueError('phase_state_required')
        self.token=secrets.token_urlsafe(32)
        self.audit=Path(audit);self.lock=threading.Lock()
        self.image_workspace=Path(image_workspace) if image_workspace else None
        self.compose_started=False
        self.upstream=upstream.rstrip('/')+'/chat/completions'
        self.api_key=api_key;self.model=model
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                if self.path!='/chat/completions':self.send_error(404);return
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.token):self.send_error(401);return
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=32*1024*1024:raise ValueError('invalid body size')
                    body=json.loads(self.rfile.read(length));incoming=dict(body);parameters=owner.parameters;phase='native'
                    if owner.pipeline:
                        state=json.loads(owner.phase_state.read_text()) if owner.phase_state.exists() else {}
                        body,parameters,phase=request_for_phase(body,owner.pipeline,state)
                        if phase=='compose':
                            with owner.lock:
                                if owner.compose_started:
                                    owner.phase_state.with_suffix('.error.json').write_text(json.dumps({'error':'compose_request_limit'}))
                                    raise ValueError('compose_request_limit')
                                owner.compose_started=True
                    body=apply(body,owner.model,parameters)
                    image_records=[]
                    if owner.image_workspace:
                        import image_inputs
                        body,image_records=image_inputs.augment(body,owner.image_workspace)
                    budget=check_budget(body,incoming=incoming)
                    if body.get('stream'):body['stream_options']={**body.get('stream_options',{}),'include_usage':True}
                except BudgetExceeded as exc:
                    entry={'at':time.time(),'phase':phase,'error_code':exc.code,'budget':exc.budget,'upstream_sent':False}
                    with owner.lock:
                        with owner.audit.open('a',encoding='utf8') as f:f.write(json.dumps(entry)+'\n')
                    raw=json.dumps({'error':{'type':'context_budget','code':exc.code,'message':'Rebuild bounded task context; do not retry this request unchanged.'}}).encode()
                    self.send_response(400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                except (ValueError,TypeError):self.send_error(400,'invalid frozen model parameters');return
                entry={'at':time.time(),'phase':phase,'requested':parameters,'wire':snapshot(body),'tool_count':len(body.get('tools',[])),'tool_names':[x.get('function',{}).get('name') for x in body.get('tools',[])],'budget':budget,'upstream_sent':False}
                if image_records:entry['image_inputs']=image_records
                usage=UsageReader(bool(body.get('stream')))
                req=Request(owner.upstream,json.dumps(body,ensure_ascii=False,allow_nan=False).encode(),{'Authorization':'Bearer '+owner.api_key,'Content-Type':'application/json','Accept':'text/event-stream' if body.get('stream') else 'application/json'})
                started=False
                from family_lab import ModelSlot
                slot=ModelSlot(owner.image_workspace,owner.model)
                try:
                    slot.__enter__()
                    entry['upstream_sent']=True
                    with urlopen(req,timeout=180) as response:
                        entry['http_status']=response.status
                        if owner.image_workspace and 200<=response.status<300:
                            image_inputs.submitted(owner.image_workspace,image_records)
                        self.send_response(response.status);self.send_header('Content-Type',response.headers.get('Content-Type','application/json'));self.send_header('Connection','close');self.end_headers();started=True
                        while chunk:=response.read1(65536):
                            usage.feed(chunk);self.wfile.write(chunk);self.wfile.flush()
                        usage.finish()
                        entry['stream_complete']=True
                except HTTPError as exc:
                    slot.error='http_'+str(exc.code)
                    entry['http_status']=exc.code
                    self.send_error(exc.code,'upstream model request rejected')
                except Exception as exc:
                    slot.error=type(exc).__name__
                    entry['error_type']=type(exc).__name__
                    if not started:self.send_error(502,'model connection failed; not retried')
                finally:
                    slot.usage=usage.usage
                    slot.__exit__(None,None,None)
                    if slot.request:entry['lab_model_attempt_id']=slot.request
                    entry.update(seconds=round(time.time()-entry['at'],3),usage=usage.usage,model_finished=usage.finished,finish_reasons=sorted(usage.finish_reasons),response_class='length' if 'length' in usage.finish_reasons else 'empty' if usage.finished and not usage.visible else 'complete' if usage.finished else 'incomplete')
                    with owner.lock:
                        with owner.audit.open('a',encoding='utf8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
                    self.close_connection=True
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        self.server.daemon_threads=True
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def start(self):self.thread.start()
    def close(self):
        if self.thread.is_alive():self.server.shutdown();self.thread.join(timeout=5)
        self.server.server_close()
