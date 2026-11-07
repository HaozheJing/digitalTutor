"""Media requests are mocked; no test here contacts a model or WeChat."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import media
from bridge import classify


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=patch.object(media,'DB',Path(self.tmp.name)/'media.sqlite');self.db.start()
        self.mode=patch('replyer.enabled',return_value=False);self.mode.start()
        self.route={'orgId':'org','imBotId':'bot','chatId':'chat','imContactId':'contact'}
        self.payload={**self.route,'messageId':'image-1','messageType':6,'isSelf':False,
                      'payload':{'imageUrl':'https://area-k.tos-cn-beijing.volces.com/test.jpg'}}
        self.cfg={'auto_reply_enabled':True,'expected_org_id':'org','allowed_routes':[self.route],
                  'auto_reply_not_before':'2026-10-19','expected_token':'test-only',
                  'api_base_url':'https://example.invalid/hub-api'}

    def tearDown(self):
        self.mode.stop();self.db.stop();self.tmp.cleanup()

    def test_media_keeps_scope_and_direction_gates(self):
        for kind in (2,6):
            msg={**self.payload,'messageType':kind}
            self.assertEqual(classify(self.cfg,msg,'2026-10-20','received_token_verified')[0],'eligible')
            for change in ({'isSelf':True},{'chatId':'other'},{'imRoomId':'room'},{'orgId':'other'}):
                self.assertNotEqual(classify(self.cfg,{**msg,**change},'2026-10-20','received_token_verified')[0],'eligible')

    def test_silence_discards_platform_and_model_phantom_transcript(self):
        parsed=media.parse('{"has_speech":false,"transcript":"嗯。"}',2)
        body,meta=media.inbound(parsed,'src')
        self.assertEqual(parsed['transcript'],'')
        self.assertNotIn('嗯',body)
        self.assertNotIn('transcript',meta)
        voice={**self.payload,'messageType':2,'payload':{
            'text':'平台虚构文字','duration':4.68,
            'voiceUrl':'https://area-k.tos-cn-beijing.volces.com/test.mp3'}}
        self.assertTrue(media.media_url(voice,self.cfg).endswith('.mp3'))

    def test_image_original_api_fallback_and_no_token_to_model_url(self):
        p={**self.payload,'payload':{}}
        with patch.object(media,'http_json',return_value={'errcode':0,'url':self.payload['payload']['imageUrl']}) as request:
            url=media.media_url(p,self.cfg)
            self.assertNotIn('token',url)
            self.assertIn('/api/v2/message/loadArtWorkImage?',request.call_args.args[0])
            self.assertEqual(request.call_args.args[1],{'chatId':'chat','messageId':'image-1'})

    def test_unsafe_or_unsupported_media_is_not_sent_to_model(self):
        for url in ('http://127.0.0.1/x','https://localhost/x','https://evil.invalid/x',
                    'https://user@area-k.tos-cn-beijing.volces.com/x'):
            with self.assertRaises(ValueError):media.safe_url(url)
        with self.assertRaises(ValueError):
            media.media_url({**self.payload,'messageType':2,'payload':{'wxVoiceUrl':'x','text':'do not trust'}},self.cfg)

    def test_durable_deduplication_and_bounded_failure(self):
        self.assertIsNone(media.prepare(self.payload,17))
        with patch.object(media,'still_current',return_value=True), patch.object(media,'perceive',side_effect=TimeoutError) as call:
            media.run_one(self.cfg)
            self.assertIsNone(media.prepare(self.payload,17))
            media.run_one(self.cfg)
            result=media.prepare(self.payload,17)
            self.assertIn('error',result)
            media.run_one(self.cfg)
            self.assertEqual(call.call_count,2)
        self.assertEqual(media.inbound(result,'src')[1]['media_status'],'media_unavailable')

    def test_success_is_cached_and_old_context_is_cancelled(self):
        media.prepare(self.payload,17)
        result={'kind':'image','description':'红色球','visible_text':'','uncertainty':''}
        with patch.object(media,'still_current',return_value=True), patch.object(media,'perceive',return_value=result) as call:
            media.run_one(self.cfg);media.run_one(self.cfg)
            self.assertEqual(media.prepare(self.payload,18),result)
            self.assertEqual(call.call_count,1)
        second={**self.payload,'messageId':'old-context'};media.prepare(second,19)
        with patch.object(media,'still_current',return_value=False), patch.object(media,'perceive') as call:
            media.run_one(self.cfg);call.assert_not_called()
        with sqlite3.connect(media.DB) as db:
            self.assertEqual(db.execute('select status from media where key=?',(media.key_for(second),)).fetchone()[0],'cancelled')

    def test_transcript_provenance_and_structured_image_validation(self):
        result=media.parse('{"has_speech":true,"transcript":"你能听到吗"}',2)
        body,metadata=media.inbound(result,'src')
        self.assertIn('你能听到吗',body)
        self.assertEqual(metadata['transcript']['input_form'],'speech_transcript')
        for invalid in ('[]','{"description":"x"}','{"has_speech":"false","transcript":"x"}'):
            with self.assertRaises(ValueError):media.parse(invalid,2 if 'has_speech' in invalid else 6)

    def test_pending_image_orders_later_text_but_does_not_block_receipts(self):
        import bridge
        inbox=Path(self.tmp.name)/'inbox.sqlite'
        with sqlite3.connect(inbox) as db:
            db.execute('CREATE TABLE callbacks(id INTEGER,kind TEXT,body_json TEXT,first_received_at TEXT,status TEXT)')
            db.execute('CREATE TABLE bridge_events(callback_id INTEGER PRIMARY KEY,disposition TEXT,processed_at TEXT)')
            image={**self.payload,'timestamp':1792395867554}
            text={**image,'messageId':'text-2','messageType':7,'payload':{'text':'看这张图'}}
            for rid,kind,p in ((1,'message',image),(2,'message',text),(3,'send_result',{})):
                db.execute('INSERT INTO callbacks VALUES(?,?,?,?,?)',(rid,kind,json.dumps(p),'2026-10-20','received_token_verified'))
        cfg={**self.cfg,'database':str(inbox)}
        app=MagicMock();app.store.connection.execute.return_value=[]
        with patch.object(bridge,'app_open',return_value=app), patch('test_context.current',return_value={'cutoff_id':0}), \
             patch('digital_tutor.mock_channel.channel_for') as channel:
            channel.return_value.observe_receipt.return_value=True
            with patch.object(media,'prepare',return_value=None):
                self.assertTrue(bridge.consume(cfg))
                app.ingest_message.assert_not_called()
                channel.return_value.observe_receipt.assert_called_once()
            with sqlite3.connect(inbox) as db:
                self.assertEqual(db.execute('SELECT callback_id FROM bridge_events').fetchall(),[(3,)])
            result={'kind':'image','description':'红球','visible_text':'','uncertainty':''}
            with patch.object(media,'prepare',return_value=result):
                self.assertFalse(bridge.consume(cfg))
            self.assertEqual(app.ingest_message.call_count,2)
            self.assertIn('红球',app.ingest_message.call_args_list[0].args[1]['body'])
            self.assertEqual(app.ingest_message.call_args_list[1].args[1]['body'],'看这张图')


if __name__=='__main__':unittest.main()
