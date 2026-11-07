"""Private Agent Plan media adapter; no credentials or upstream bodies in receipts."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import uuid
import wave
import zlib
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError

IMAGE_ENDPOINT='https://ark.cn-beijing.volces.com/api/plan/v3/images/generations'
TTS_ENDPOINT='https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional'
MAX_BYTES=24*1024*1024

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def atomic(path,data):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as handle:handle.write(data)
    os.chmod(temporary,0o600);temporary.replace(path)

def png_dimensions(data):
    if data[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('invalid_image')
    pos=8;dimensions=None;image_data=False
    while pos+12<=len(data):
        size=struct.unpack('>I',data[pos:pos+4])[0];kind=data[pos+4:pos+8]
        end=pos+8+size
        if end+4>len(data):raise ValueError('incomplete_image')
        if zlib.crc32(data[pos+4:end])&0xffffffff!=struct.unpack('>I',data[end:end+4])[0]:raise ValueError('image_checksum')
        if kind==b'IHDR':dimensions=struct.unpack('>II',data[pos+8:pos+16])
        if kind==b'IDAT':image_data=True
        if kind==b'IEND' and dimensions and image_data and end+4==len(data):return dimensions
        pos=end+4
    raise ValueError('incomplete_image')

def read_audio(response):
    pcm=bytearray();complete=False;total=0
    while True:
        line=response.readline(MAX_BYTES+1)
        if not line:break
        total+=len(line)
        if total>MAX_BYTES:raise ValueError('response_too_large')
        if not line.strip():continue
        item=json.loads(line);code=item.get('code',0)
        if code not in (0,20000000):raise ValueError('tts_rejected')
        if item.get('data'):pcm.extend(base64.b64decode(item['data'],validate=True))
        if code==20000000:complete=True;break
    if not complete or not pcm or len(pcm)%2:raise ValueError('incomplete_audio')
    duration=len(pcm)/48000
    if not 0<duration<=60:raise ValueError('audio_exceeds_60_seconds')
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(pcm)
    return out.getvalue(),duration

class ArkMediaProvider:
    def __init__(self,config_path=None,opener=None):
        self.path=Path(config_path or os.environ['DIGITAL_TUTOR_MEDIA_CONFIG']).resolve()
        self.config=json.loads(self.path.read_text(encoding='utf8'))
        self.root=Path(self.config['asset_directory']).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.opener=opener or build_opener(ProxyHandler({}),NoRedirect())

    def generate_audio(self,*,content,idempotency_key):return self.generate('audio',content,idempotency_key)
    def generate_image(self,*,content,idempotency_key):return self.generate('image',content,idempotency_key)

    def generate(self,kind,content,key):
        # Content fingerprint prevents a reused operation key silently returning another request.
        ident=hashlib.sha256(key.encode()).hexdigest()
        fingerprint=hashlib.sha256(json.dumps([kind,content,self.config.get('image_model'),self.config.get('speaker')],ensure_ascii=False).encode()).hexdigest()
        receipt=self.root/(ident+'.json');lock=self.root/(ident+'.lock')
        if receipt.exists():
            cached=json.loads(receipt.read_text())
            if cached['fingerprint']!=fingerprint:return {'status':'failed','reason':'media_idempotency_conflict'}
            result=cached['result'];asset=self.root/Path(result['url']).name
            if asset.is_file() and hashlib.sha256(asset.read_bytes()).hexdigest()==result['sha256']:return result
            return {'status':'failed','reason':'media_cache_integrity_failed'}
        try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:return {'status':'failed','reason':'media_request_pending_review'}
        os.close(fd)
        try:
            private=json.loads(Path(self.config['credential_file']).read_text(encoding='utf8'))
            if private.get('base_url')!='https://ark.cn-beijing.volces.com/api/plan/v3':raise ValueError('plan_route_required')
            credential=private['api_key']
            headers={'Content-Type':'application/json'}
            if kind=='image':
                headers['Authorization']='Bearer '+credential
                body={'model':self.config['image_model'],'prompt':content,'size':'1024x1024','output_format':'png','response_format':'b64_json','watermark':True,'stream':False}
                endpoint=IMAGE_ENDPOINT
            else:
                headers.update({'X-Api-Key':credential,'X-Api-Resource-Id':'seed-tts-2.0','X-Api-Request-Id':uuid.uuid4().hex})
                body={'req_params':{'text':content,'speaker':self.config['speaker'],'audio_params':{'format':'pcm','sample_rate':24000}}}
                endpoint=TTS_ENDPOINT
            req=Request(endpoint,json.dumps(body,ensure_ascii=False).encode(),headers,method='POST')
            with self.opener.open(req,timeout=120) as response:
                logid=response.headers.get('X-Tt-Logid','')[:200]
                if kind=='audio':data,duration=read_audio(response);ext='wav';mime='audio/wav'
                else:
                    raw=response.read(MAX_BYTES+1)
                    if len(raw)>MAX_BYTES:raise ValueError('response_too_large')
                    payload=json.loads(raw);data=base64.b64decode(payload['data'][0]['b64_json'],validate=True)
                    width,height=png_dimensions(data);ext='png';mime='image/png'
            sha=hashlib.sha256(data).hexdigest();asset=self.root/(sha+'.'+ext);atomic(asset,data)
            result={'status':'ready','url':'/media/'+asset.name,'sha256':sha,'mime_type':mime}
            if kind=='audio':result['duration_seconds']=round(duration,4)
            evidence={'fingerprint':fingerprint,'result':result,'model':self.config['image_model'] if kind=='image' else 'doubao-seed-tts-2.0','request_logid':logid,'bytes':len(data)}
            if kind=='image':evidence.update(width=width,height=height)
            atomic(receipt,json.dumps(evidence,ensure_ascii=False,indent=2).encode())
            lock.unlink();return result
        except HTTPError as exc:
            # Never retain an API body: it can include prompts, credentials or signed links.
            return {'status':'failed','reason':'media_http_'+str(exc.code)}
        except Exception:
            # Keep pending marker on ambiguous failure; no silent retries/duplicate billing.
            return {'status':'failed','reason':'media_generation_failed'}
