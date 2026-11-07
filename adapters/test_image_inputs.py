import base64,io,json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from PIL import Image
import image_inputs as images

def picture(color='red',fmt='PNG'):
    data=io.BytesIO();Image.new('RGB',(40,30),color).save(data,format=fmt);return data.getvalue()

class ImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'runtime').mkdir()
        self.db=self.root/'runtime/live.sqlite';self.state={'run_id':'run','uid':'family'};self.raw=picture()
        with closing(sqlite3.connect(self.db)) as c,c:
            c.executescript('CREATE TABLE messages(source_id TEXT,speaker TEXT,body TEXT,visible INTEGER);CREATE TABLE metadata(key TEXT,value TEXT);CREATE TABLE events(id TEXT,payload TEXT);')
            c.execute('INSERT INTO metadata VALUES(?,?)',('test_mode',json.dumps(self.state)))
    def register(self,source='source',raw=None,state=None,payload=None):
        with closing(sqlite3.connect(self.db)) as c,c:
            ident=images.register(c,self.root,state or self.state,source,image=None if payload else {'file_base64':base64.b64encode(raw or self.raw).decode()},payload=payload)
            c.execute('INSERT INTO messages VALUES(?,?,?,?)',(source,'user',images.marker(ident),1));return ident
    def body(self,*ids):return {'model':images.MODEL,'messages':[{'role':'user','content':'请看这几张图片 '+''.join(images.marker(x) for x in ids)}]}
    def test_bytes_not_suffix_and_corrupt_files_are_rejected(self):
        self.assertEqual(images.inspect(self.raw)['mime'],'image/png');self.assertEqual(images.inspect(picture(fmt='JPEG'))['mime'],'image/jpeg')
        for raw in [b'<svg>'+b'x'*100,self.raw[:-1],self.raw[:50]+b'x'+self.raw[51:],b'x'*(images.MAX_BYTES+1)]:
            with self.assertRaises(ValueError):images.inspect(raw)
        with self.assertRaises(ValueError):images.decode({'file_base64':'bad'})
    def test_actual_bytes_go_into_user_array_with_same_model_and_no_input_mutation(self):
        ident=self.register();body=self.body(ident);wire,records=images.augment(body,self.root)
        self.assertIsInstance(body['messages'][0]['content'],str)
        parts=wire['messages'][0]['content'];image=[x for x in parts if x['type']=='image_url'][0]
        self.assertEqual(base64.b64decode(image['image_url']['url'].split(',')[1]),self.raw)
        self.assertEqual(records[0]['sha256'],images.sha(self.raw));self.assertEqual(wire['model'],images.MODEL)
        images.submitted(self.root,records);self.assertEqual(images.evidence(self.db)[0]['result']['status'],'submitted')
        again,_=images.augment(body,self.root);self.assertEqual(again,wire)
    def test_no_foreign_hidden_or_forgotten_picture_and_no_assistant_injection(self):
        own=self.register();foreign=self.register('other',state={'run_id':'wrong','uid':'family'})
        body=self.body(own,foreign);body['messages'][0]['role']='assistant'
        self.assertEqual(images.augment(body,self.root),(body,[]))
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('CREATE TABLE preference_forgotten(source_id TEXT)');c.execute("INSERT INTO preference_forgotten VALUES('source')")
        wire,records=images.augment(self.body(own,foreign),self.root);self.assertEqual(records,[])
    def test_callback_url_is_acquired_once_no_redirects_no_platform_ocr(self):
        ident=self.register(payload={'payload':{'url':'https://area-k.tos-cn-beijing.volces.com/image?id=1','text':'fake OCR'}})
        with patch.object(images,'fetch',return_value=(self.raw,images.inspect(self.raw))) as fetch:
            wire,_=images.augment(self.body(ident),self.root);images.augment(self.body(ident),self.root);self.assertEqual(fetch.call_count,1)
        self.assertNotIn('fake OCR',json.dumps(wire))
        for url in ['https://127.0.0.1/x','http://area-k.tos-cn-beijing.volces.com/x','https://area-k.tos-cn-beijing.volces.com.evil/x']:
            with self.assertRaises(ValueError):images.fetch(url)
        with self.assertRaises(ValueError):images.NoRedirect().redirect_request(None,None,None,None,None,None)
    def test_bad_url_or_download_failure_has_explicit_unavailable_evidence_and_no_retry(self):
        ident=self.register(payload={'payload':{'url':'https://area-k.tos-cn-beijing.volces.com/expired'}})
        with patch.object(images,'fetch',side_effect=TimeoutError('PRIVATE URL')) as fetch:
            wire,records=images.augment(self.body(ident),self.root);images.augment(self.body(ident),self.root);self.assertEqual(fetch.call_count,1)
        self.assertEqual(records[0]['status'],'unavailable');self.assertIn('不得描述或猜测',json.dumps(wire,ensure_ascii=False));self.assertNotIn('PRIVATE',json.dumps(records))
    def test_tampered_asset_cannot_enter_request(self):
        ident=self.register();next((self.root/'runtime/image-inputs').iterdir()).write_bytes(picture('blue'))
        wire,records=images.augment(self.body(ident),self.root)
        self.assertEqual(records[0]['status'],'unavailable');self.assertNotIn('image_url',json.dumps(wire))
    def test_delivery_waits_for_input_then_allows_explicit_failure(self):
        import sql_tools as sql
        from agent_plan import ServiceValidationError
        ident=self.register()
        with closing(sqlite3.connect(self.db)) as c,c:c.execute('INSERT INTO events VALUES(?,?)',('event',json.dumps({'source_message_id':'source'})))
        with patch.object(sql,'DB',self.db):
            with self.assertRaises(ServiceValidationError):images.guard_reply('event')
            _,records=images.augment(self.body(ident),self.root)
            with self.assertRaises(ServiceValidationError):images.guard_reply('event')
            images.submitted(self.root,records);images.guard_reply('event')
    def test_model_and_batch_limits_do_not_silently_claim_images_seen(self):
        ids=[self.register('msg'+str(n)) for n in range(5)]
        _,records=images.augment(self.body(*ids),self.root)
        self.assertEqual(sum(x['status']=='ready' for x in records),4);self.assertEqual(records[-1]['status'],'unavailable')

class ProxyImageTests(ImageTests):
    def test_proxy_passes_actual_image_to_same_model_and_logs_hash_only(self):
        from experiment_parameter_proxy import ParameterProxy
        from urllib.request import Request,urlopen
        ident=self.register();audit=self.root/'audit.jsonl'
        class Response(io.BytesIO):status=200;headers={'Content-Type':'application/json'}
        proxy=ParameterProxy('https://ark.cn-beijing.volces.com/api/plan/v3','private',images.MODEL,{'thinking':'disabled'},audit,image_workspace=self.root);proxy.start()
        try:
            req=Request(proxy.url+'/chat/completions',json.dumps(self.body(ident)).encode(),{'Authorization':'Bearer '+proxy.token})
            with patch('experiment_parameter_proxy.urlopen',return_value=Response(b'{"choices":[{"finish_reason":"stop"}]}')) as send:
                with urlopen(req) as f:f.read()
                wire=json.loads(send.call_args.args[0].data)
                self.assertEqual(wire['thinking'],{'type':'disabled'})
                self.assertTrue(any(x['type']=='image_url' for x in wire['messages'][0]['content']))
        finally:proxy.close()
        text=audit.read_text();self.assertNotIn('base64',text);self.assertNotIn('private',text);self.assertIn(images.sha(self.raw),text)

if __name__=='__main__':unittest.main()
