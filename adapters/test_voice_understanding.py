import base64
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import wave
import voice_understanding as v

def audio():
    out=io.BytesIO()
    with wave.open(out,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000);f.writeframes(b'\0'*16000)
    return out.getvalue()

class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'runtime').mkdir()
        self.db=self.root/'runtime/live.sqlite';self.state={'run_id':'r1','uid':'f1'}
        self.raw=audio();self.upload={'format':'wav','file_base64':base64.b64encode(self.raw).decode()}
    def register(self,**kw):
        with closing(sqlite3.connect(self.db)) as c,c:return v.register(c,self.root,self.state,'m1',**kw)
    def test_upload_validates_bytes_and_format(self):
        self.assertEqual(v.decode_audio(self.upload),(self.raw,'wav'))
        for value in [dict(self.upload,format='mp3'),{'format':'wav','file_base64':'x'},dict(self.upload,path='/etc/passwd')]:
            with self.assertRaises((ValueError,TypeError)):v.decode_audio(value)
        with self.assertRaises(ValueError):v.inspect_audio(self.raw[:-10],'wav')
    def test_callback_uses_voice_url_not_platform_text_or_silk(self):
        ident=self.register(payload={'payload':{'voiceUrl':'https://area-k.tos-cn-beijing.volces.com/audio.mp3','text':'UNTRUSTED','wxVoiceUrl':'https://elsewhere/silk'}})
        with closing(sqlite3.connect(self.db)) as c,c:loc=c.execute('SELECT locator FROM voice_inputs').fetchone()[0]
        self.assertNotIn('UNTRUSTED',loc);self.assertNotIn('silk',loc);self.assertIn('audio.mp3',loc)
        self.assertIn('understand_audio',v.marker(ident))
    def test_missing_url_is_unknown(self):
        self.register(payload={'payload':{'text':'fake transcript'}})
        with closing(sqlite3.connect(self.db)) as c,c:self.assertIn('audio_source_unavailable',c.execute('SELECT locator FROM voice_inputs').fetchone()[0])
    def test_url_scope_and_redirect(self):
        for url in ['http://area-k.tos-cn-beijing.volces.com/x.mp3','https://127.0.0.1/x.wav','https://area-k.tos-cn-beijing.volces.com.evil/x.mp3','https://user@area-k.tos-cn-beijing.volces.com/x.mp3']:
            with self.assertRaises(ValueError):v.safe_url(url)
        with self.assertRaises(ValueError):v.NoRedirect().redirect_request(None,None,None,None,None,None)
    def test_download_reads_actual_bytes_and_rejects_wrong_container(self):
        raw=self.raw
        class Opener:
            def open(self,request,timeout):
                assert request.full_url=='https://area-k.tos-cn-beijing.volces.com/voice.wav'
                return io.BytesIO(raw)
        with patch.object(v,'build_opener',return_value=Opener()):
            self.assertEqual(v.fetch_audio('https://area-k.tos-cn-beijing.volces.com/voice.wav'),(raw,'wav'))
            raw=b'not audio'*100
            with self.assertRaises(ValueError):v.fetch_audio('https://area-k.tos-cn-beijing.volces.com/voice.wav')
    def test_immutable_idempotency(self):
        ident=self.register(audio=self.upload);self.assertEqual(ident,self.register(audio=self.upload))
        altered=bytearray(self.raw);altered[-1]=1
        with self.assertRaises(ValueError):self.register(audio={'format':'wav','file_base64':base64.b64encode(altered).decode()})
    def test_capability_restart_and_tamper(self):
        private=self.root/'private.json';private.write_text(json.dumps({'base_url':v.ORIGIN,'model':v.MODEL,'api_key':'test-only'}))
        operator={'voice':{'credential_file':str(private)}}
        bound=v.bind(self.root,operator);self.assertEqual(bound,v.bind(self.root,operator))
        with patch.dict(os.environ,{'TUTOR_VOICE_BINDING_SHA256':bound['sha256']}):
            self.assertEqual(v.configuration(self.root)['model'],v.MODEL)
            (self.root/'runtime/voice-capability/provider.json').write_text('{}')
            with self.assertRaises(ValueError):v.configuration(self.root)
    def test_scope_cache_and_original_remain_distinct(self):
        import sql_tools as s,test_mode as tm
        ident=self.register(audio=self.upload)
        result={'status':'understood','transcript':'hello','derived':True,'audio_sha256':v.sha(self.raw),'format':'wav'}
        with patch.object(s,'R',self.root),patch.object(s,'DB',self.db),patch.object(tm,'state',return_value=self.state),patch.object(v,'configuration',return_value={}),patch.object(v,'perceive',return_value=result) as call:
            first=v.understand(ident);self.assertEqual(first,v.understand(ident));self.assertEqual(call.call_count,1)
            self.assertEqual(first['source_message_id'],'m1')
            with patch.object(tm,'state',return_value={'run_id':'r2','uid':'f1'}):
                with self.assertRaises(ValueError):v.understand(ident)
    def test_failure_does_not_guess_or_automatically_retry(self):
        import sql_tools as s,test_mode as tm
        ident=self.register(audio=self.upload)
        with patch.object(s,'R',self.root),patch.object(s,'DB',self.db),patch.object(tm,'state',return_value=self.state),patch.object(v,'configuration',side_effect=RuntimeError('SECRET')) as call:
            result=v.understand(ident);self.assertEqual(result['status'],'unavailable');self.assertNotIn('SECRET',json.dumps(result))
            self.assertEqual(result,v.understand(ident));self.assertEqual(call.call_count,1)
    def test_resend_policy_covers_partial_noise_and_failure_without_changing_clear_audio(self):
        for result in [{'status':'no_speech'},{'status':'unavailable'},
                       {'status':'understood','intelligibility':'partial'},
                       {'status':'unclear','intelligibility':'unintelligible'}]:
            with self.subTest(result=result):
                actual=v.reply_contract(result)
                self.assertTrue(actual['needs_audio_resend'])
                self.assertIn('重新发一次语音',actual['reply_guidance'])
                self.assertIn('不要追加课程',actual['reply_guidance'])
                self.assertIn('身份未确认',actual['reply_guidance'])
        clear=v.reply_contract({'status':'understood','intelligibility':'clear','transcript':'hello'})
        self.assertFalse(clear['needs_audio_resend']);self.assertNotIn('reply_guidance',clear)
        self.assertEqual(clear['transcript'],'hello')

    def test_speaker_binding_uses_explicit_current_caption_not_family_contact(self):
        import test_mode as tm
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('CREATE TABLE messages(source_id,speaker,body)')
            for name,caption in [('m','老师，我是侨侨妈妈，这条语音是我发的。'),('f','老师，我是侨侨爸爸，这条语音是我发的。'),('k','老师，我是侨侨，这条语音是我发的。'),('u','[语音消息]')]:
                c.execute('INSERT INTO messages VALUES(?,?,?)',(name,'user','[语音]附带文字：'+caption))
            with patch.object(tm,'fixture_data',return_value={'profile':{'child_nickname':'侨侨','guardian_role':'mother'}}):
                for ident,role in [('m','mother'),('f','father'),('k','child'),('u','neutral')]:
                    self.assertEqual(v.speaker_hint(c,ident)['address_mode'],role)
    def test_unknown_speaker_title_rejected_before_delivery(self):
        import sql_tools as sql
        from agent_plan import ServiceValidationError
        ident=self.register(audio=self.upload)
        with closing(sqlite3.connect(self.db)) as c,c:
            c.execute('CREATE TABLE events(id,payload)')
            c.execute('INSERT INTO events VALUES(?,?)',('e',json.dumps({'source_message_id':'m1'})))
            result={'status':'no_speech','needs_audio_resend':True,'current_speaker':{'address_mode':'neutral'}}
            c.execute('UPDATE voice_inputs SET result=?',(json.dumps(result),))
        with patch.object(sql,'DB',self.db):
            with self.assertRaises(ServiceValidationError):v.guard_reply('e','侨侨妈妈，不好意思，请再发一次。')
            v.guard_reply('e','不好意思，没太听清，能麻烦您再发一次吗？')

    def test_real_body_parser_no_speech_and_finish_guard(self):
        response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'intelligibility':'unintelligible','has_speech':False,'transcript':'hallucination','meaning':'guessed','requests':['invented'],'uncertainty':'noise'})}}]}
        class Opener:
            def open(self,request,timeout):
                sent=json.loads(request.data)
                assert sent['model']==v.MODEL and sent['reasoning_effort']=='none'
                assert 'input_audio'==sent['messages'][0]['content'][1]['type']
                return io.BytesIO(json.dumps(response).encode())
        with patch.object(v,'build_opener',return_value=Opener()):
            result=v.perceive(self.raw,'wav',{'api_key':'test-only'})
            self.assertEqual(result['transcript'],'');self.assertEqual(result['requests'],[])
            self.assertTrue(result['needs_audio_resend'])
            response['choices'][0]['finish_reason']='length'
            with self.assertRaises(ValueError):v.perceive(self.raw,'wav',{'api_key':'test-only'})

if __name__=='__main__':unittest.main()
