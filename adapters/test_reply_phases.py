import copy, importlib, io, json, sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
from reply_phases import pipeline, request_for_phase, validate_handoff
from experiment_parameter_proxy import UsageReader, ParameterProxy
from urllib.request import Request, urlopen
from urllib.error import HTTPError

MODEL='deepseek-v4.1-flash-economy'
SCHEMA={'type':'object','properties':{'event_id':{'type':'string'},'body':{'type':'string'},'generation_token':{'type':'string'}},'required':['event_id','body']}
READY={'delivery_tool':'reply_to_message','delivery_args':{'event_id':'event-1','generation_token':'authority-1'},'points':['只确认已收到，不新增安排。']}
CFG={'mode':'two_stage','prepare':{'thinking':'enabled','reasoning_effort':'low'},'compose':{'thinking':'disabled','top_p':1}}

class PhaseTests(unittest.TestCase):
    def test_config_no_silent_fallback(self):
        self.assertEqual(pipeline(MODEL,None),{})
        for c in [{'mode':'wrong'}, {**CFG,'compose':{}}, {**CFG,'prepare':{'seed':1}}]:
            with self.assertRaises(ValueError):pipeline(MODEL,c)
    def test_prepare_fences_delivery_and_compose_selects_exact_tool(self):
        body={'messages':[{'role':'user','content':'原话'}],'tools':[{'type':'function','function':{'name':'mcp__tutor__'+n,'parameters':copy.deepcopy(SCHEMA)}} for n in ['reply_to_message','knowledge_search','reply_material_ready']]}
        old=copy.deepcopy(body)
        request,p,phase=request_for_phase(body,CFG,{'context_loaded':True})
        self.assertEqual(phase,'prepare');self.assertEqual(len(request['tools']),2)
        ready=validate_handoff(READY,{'reply_to_message':SCHEMA})
        request,p,phase=request_for_phase(body,CFG,ready)
        self.assertEqual(p['thinking'],'disabled');self.assertEqual(len(request['tools']),1)
        self.assertFalse(request['parallel_tool_calls']);self.assertEqual(request['tool_choice']['function']['name'],'mcp__tutor__reply_to_message')
        self.assertEqual(body,old)
        with self.assertRaises(ValueError):request_for_phase(body,CFG,{'phase':'terminal'})
    def test_handoff_requires_target_and_rejects_body(self):
        for args in [{**READY,'delivery_args':{}},{**READY,'delivery_args':{'event_id':'x','body':'draft'}},{**READY,'delivery_tool':'unknown'},{**READY,'points':[]}]:
            with self.assertRaises(ValueError):validate_handoff(args,{'reply_to_message':SCHEMA})
    def test_usage_split_frames_without_content_retention(self):
        r=UsageReader();data=('data: '+json.dumps({'choices':[{'delta':{'content':'private'},'finish_reason':'tool_calls'}]})+'\n\ndata: '+json.dumps({'usage':{'prompt_tokens':123,'completion_tokens':45,'prompt_tokens_details':{'cached_tokens':100}}})+'\n\ndata: [DONE]\n\n').encode()
        for b in data:r.feed(bytes([b]))
        r.finish();self.assertEqual(r.usage['prompt_tokens'],123);self.assertTrue(r.finished);self.assertEqual(r.buffer,b'')
    def test_second_expression_request_blocked_before_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'reply-phase.json';state.write_text(json.dumps(validate_handoff(READY,{'reply_to_message':SCHEMA})))
            proxy=ParameterProxy('https://ark.cn-beijing.volces.com/api/plan/v3','private',MODEL,{},Path(tmp)/'audit',reply_pipeline=CFG,phase_state=state)
            proxy.start()
            body={'model':MODEL,'messages':[],'tools':[{'type':'function','function':{'name':'mcp__tutor__reply_to_message','parameters':SCHEMA}}]}
            req=Request(proxy.url+'/chat/completions',json.dumps(body).encode(),{'Authorization':'Bearer '+proxy.token})
            response=io.BytesIO(b'{"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}');response.status=200;response.headers={}
            try:
                with patch('experiment_parameter_proxy.urlopen',return_value=response) as send:
                    with urlopen(req) as result:result.read()
                    with self.assertRaises(HTTPError) as error:urlopen(req)
                    self.assertEqual(error.exception.code,400);self.assertEqual(send.call_count,1)
                self.assertTrue(state.with_suffix('.error.json').exists())
            finally:proxy.close()
    def test_wrapper_prevents_bypass_changed_authority_and_second_send(self):
        calls=[]
        def invoke(name,args):calls.append((name,args));return {'status':'sent'}
        fake=types.SimpleNamespace(base_mcp=types.SimpleNamespace(TOOLS=[('reply_to_message','',SCHEMA)],call=invoke))
        with patch.dict(sys.modules,{'mcp_server':fake}):
            sys.modules.pop('experiment_phase_mcp',None)
            module=importlib.import_module('experiment_phase_mcp')
        with tempfile.TemporaryDirectory() as tmp:
            module.STATE=Path(tmp)/'phase.json'
            with self.assertRaises(ValueError):module.call('reply_to_message',{'body':'b'})
            module.save_state({'phase':'prepare','context_loaded':True})
            module.call('reply_material_ready',READY)
            with self.assertRaises(ValueError):module.call('reply_to_message',{**READY['delivery_args'],'generation_token':'forged','body':'b'})
            result=module.call('reply_to_message',{**READY['delivery_args'],'body':'收到。'})
            self.assertTrue(result['reply_phase_terminal'])
            with self.assertRaises(ValueError):module.call('reply_to_message',{**READY['delivery_args'],'body':'again'})
            self.assertEqual(sum(n=='reply_to_message' for n,a in calls),1)
            with self.assertRaises(ValueError):module.call('knowledge_search',{'query':'late'})
            module.save_state({'phase':'prepare','context_loaded':True})
            invalid={**READY,'delivery_args':{**READY['delivery_args'],'day':'2025-10-26'}}
            first=module.call('reply_material_ready',invalid)
            self.assertEqual(first['status'],'rejected');self.assertIn('event_id',first['allowed_fields'])
            self.assertFalse(first['reply_phase_terminal'])
            module.call('reply_material_ready',invalid)
            self.assertTrue(module.call('reply_material_ready',invalid)['reply_phase_terminal'])
            self.assertEqual(module.read_state()['phase'],'terminal')
        sys.modules.pop('experiment_phase_mcp',None)

if __name__=='__main__':unittest.main()
