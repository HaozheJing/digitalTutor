import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from model_parameters import validate,apply
from experiment_parameter_proxy import ParameterProxy

MODEL='deepseek-v4.1-flash-economy'
class ParameterTests(unittest.TestCase):
    def test_omitted_preserves_legacy_wire(self):
        body={'model':MODEL,'messages':[{'role':'user','content':'原文'}],'max_completion_tokens':8192}
        self.assertEqual(apply(body,MODEL,{}),body)
    def test_invalid_and_ignored_combinations_rejected(self):
        for p in [{'seed':1},{'top_p':.7},{'temperature':True},{'temperature':float('nan')},{'max_tokens':1.5},{'thinking':'auto'},{'reasoning_effort':'medium'},{'thinking':'disabled','reasoning_effort':'low'},{'thinking':'disabled','top_p':.95}]:
            with self.subTest(p=p),self.assertRaises(ValueError):validate(MODEL,p)
        with self.assertRaises(ValueError):validate('other',{'temperature':1})
    def test_thinking_and_budget_replace_conflicting_sdk_fields(self):
        body={'model':MODEL,'thinking':{'type':'disabled'},'max_completion_tokens':8192,'tools':[{'function':{'name':'keep'}}]}
        wire=apply(body,MODEL,{'reasoning_effort':'low','max_tokens':16384,'top_p':.97,'temperature':.4})
        self.assertEqual(wire['thinking'],{'type':'enabled'});self.assertNotIn('max_completion_tokens',wire)
        self.assertEqual(wire['max_tokens'],16384);self.assertEqual(wire['tools'],body['tools'])
        off=apply(wire,MODEL,{'thinking':'disabled','top_p':1})
        self.assertEqual(off['reasoning_effort'],'none');self.assertEqual(off['thinking']['type'],'disabled')
    def test_authenticated_stream_forwarding_and_private_audit(self):
        class Response(io.BytesIO):
            status=200;headers={'Content-Type':'text/event-stream'}
        stream=b'data: {"model":"returned-model","choices":[]}\n\ndata: [DONE]\n\n'
        with tempfile.TemporaryDirectory() as tmp:
            audit=Path(tmp)/'audit.jsonl';proxy=ParameterProxy('https://ark.cn-beijing.volces.com/api/plan/v3','secret-key',MODEL,{'thinking':'disabled','temperature':.3,'top_p':1},audit);proxy.start()
            try:
                req=Request(proxy.url+'/chat/completions',json.dumps({'model':MODEL,'messages':[{'role':'user','content':'private-input'}],'stream':True}).encode(),{'Authorization':'Bearer '+proxy.token})
                with patch('experiment_parameter_proxy.urlopen',return_value=Response(stream)) as send:
                    with urlopen(req) as response:self.assertEqual(response.read(),stream)
                    sent=json.loads(send.call_args.args[0].data);self.assertEqual(sent['messages'][0]['content'],'private-input');self.assertEqual(sent['temperature'],.3)
                with patch('experiment_parameter_proxy.urlopen') as send:
                    req.remove_header('Authorization')
                    with self.assertRaises(HTTPError) as error:urlopen(req)
                    self.assertEqual(error.exception.code,401);send.assert_not_called()
            finally:proxy.close()
            text=audit.read_text();self.assertNotIn('private-input',text);self.assertNotIn('secret-key',text)
            rows=[json.loads(x) for x in text.splitlines()];self.assertEqual(len(rows),1);self.assertTrue(rows[0]['stream_complete'])
    def test_upstream_rejection_never_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            proxy=ParameterProxy('https://ark.cn-beijing.volces.com/api/plan/v3','secret',MODEL,{},Path(tmp)/'audit');proxy.start()
            try:
                with patch('experiment_parameter_proxy.urlopen',side_effect=HTTPError('unused',429,'limit',{},None)) as send:
                    req=Request(proxy.url+'/chat/completions',json.dumps({'model':MODEL}).encode(),{'Authorization':'Bearer '+proxy.token})
                    with self.assertRaises(HTTPError) as error:urlopen(req)
                    self.assertEqual(error.exception.code,429);self.assertEqual(send.call_count,1)
            finally:proxy.close()
    def test_destination_not_controlled_by_request(self):
        with self.assertRaises(ValueError):ParameterProxy('https://example.com/api/plan/v3','secret',MODEL,{},Path('unused'))

if __name__=='__main__':unittest.main()
