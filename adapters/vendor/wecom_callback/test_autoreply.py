"""Transport is mocked at the HTTP boundary: these tests cannot send real messages."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'candidates/service-wecom-v1/backend/src'))
from digital_tutor.wecom_transport import WecomChannel, family_id
from bridge import classify


class AutoReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.route={'orgId':'org','imBotId':'bot','chatId':'chat','imContactId':'contact'}
        self.cfg={'auto_reply_enabled':True,'expected_token':'unit-test-not-real',
                  'expected_org_id':'org','api_base_url':'https://example.invalid',
                  'auto_reply_not_before':'2026-10-19T00:00:00+00:00','allowed_routes':[self.route]}
        self.path=Path(self.tmp.name)/'config.json';self.path.write_text(json.dumps(self.cfg))
        self.env=patch.dict(os.environ,{'DIGITAL_TUTOR_WECOM_CONFIG':str(self.path)})
        self.env.start()
        self.channel=WecomChannel(Path(self.tmp.name)/'channel.sqlite')
        self.operation={'id':'operation-unit-test','recipient':'wecom:'+family_id(self.route),
                        'body':'你好','action_kind':'reply','reply_to':[{'message_id':'m1'}]}

    def tearDown(self):
        self.channel.close();self.env.stop();self.tmp.cleanup()

    def receipt(self,**values):
        return {**self.route,'externalRequestId':self.operation['id'],'requestId':'provider-request',
                'errorCode':0,'sendCode':0,'messageId':'sent-message',**values}

    def test_accepted_is_not_sent_duplicate_never_resends(self):
        with patch('digital_tutor.wecom_transport.http_send',return_value={'errcode':0,'requestId':'provider-request'}) as http:
            self.assertEqual(self.channel.send(self.operation),'accepted')
            self.assertEqual(self.channel.send(self.operation),'accepted')
            self.assertEqual(http.call_count,1)
        self.assertTrue(self.channel.observe_receipt(self.receipt()))
        self.assertEqual(self.channel.query(self.operation['id']),'sent')
        self.channel.observe_receipt(self.receipt(errorCode=1002,sendCode=1002))
        self.assertEqual(self.channel.query(self.operation['id']),'sent')

    def test_timeout_does_not_resend_and_wrong_recipient_cannot_confirm(self):
        with patch('digital_tutor.wecom_transport.http_send',side_effect=TimeoutError) as http:
            self.assertEqual(self.channel.send(self.operation),'unknown')
            self.assertEqual(self.channel.send(self.operation),'unknown')
            self.assertEqual(http.call_count,1)
        self.assertFalse(self.channel.observe_receipt(self.receipt(imContactId='other')))
        self.assertEqual(self.channel.query(self.operation['id']),'unknown')
        self.assertTrue(self.channel.observe_receipt(self.receipt()))
        self.assertEqual(self.channel.query(self.operation['id']),'sent')

    def test_callback_before_http_response_is_not_downgraded(self):
        def response(*args):
            self.channel.observe_receipt(self.receipt())
            return {'errcode':0,'requestId':'provider-request'}
        with patch('digital_tutor.wecom_transport.http_send',side_effect=response):
            self.assertEqual(self.channel.send(self.operation),'sent')

    def test_observed_success_receipt_without_error_code_requires_message_id(self):
        with patch('digital_tutor.wecom_transport.http_send',return_value={'errcode':0,'requestId':'provider-request'}):
            self.channel.send(self.operation)
        receipt=self.receipt();receipt.pop('errorCode')
        incomplete={k:v for k,v in receipt.items() if k!='messageId'}
        self.assertFalse(self.channel.observe_receipt(incomplete))
        self.assertEqual(self.channel.query(self.operation['id']),'accepted')
        self.assertTrue(self.channel.observe_receipt(receipt))
        self.assertEqual(self.channel.query(self.operation['id']),'sent')

    def test_disabled_or_unallowlisted_never_calls_http(self):
        with patch('digital_tutor.wecom_transport.http_send') as http:
            with self.assertRaises(ValueError):self.channel.send({**self.operation,'recipient':'wecom:unknown'})
            self.cfg['auto_reply_enabled']=False;self.path.write_text(json.dumps(self.cfg))
            with self.assertRaises(ValueError):self.channel.send(self.operation)
            http.assert_not_called()

    def test_only_verified_allowlisted_customer_text_is_ingested(self):
        incoming={**self.route,'messageId':'m1','isSelf':False,'source':0,'messageType':7,'payload':{'text':'你好'}}
        at='2026-10-19T01:00:00+00:00'
        self.assertEqual(classify(self.cfg,incoming,at,'received_token_verified')[0],'eligible')
        self.assertEqual(classify(self.cfg,incoming,at,'received_unverified')[0],'unverified')
        for change in [{'isSelf':True},{'isSelf':None},{'source':6},{'imRoomId':'room'},
                       {'chatId':'other'},{'messageType':3}]:
            self.assertNotEqual(classify(self.cfg,{**incoming,**change},at,'received_token_verified')[0],'eligible')
        self.assertEqual(classify(self.cfg,incoming,'2025-01-01','received_token_verified')[0],'before_activation')
        personal={k:v for k,v in incoming.items() if k!='isSelf'}
        personal.update(contactType=1,externalUserId='external-test',customerExternalUserId='external-test')
        self.assertEqual(classify(self.cfg,personal,at,'received_token_verified')[0],'eligible')
        for change in [{'isSelf':True},{'isSelf':None},{'contactType':3},
                       {'imContactId':'bot'},{'customerExternalUserId':'other'},{'source':6}]:
            self.assertNotEqual(classify(self.cfg,{**personal,**change},at,'received_token_verified')[0],'eligible')

    def assert_native_reply_path(self, action_kind, resolves_intent):
        """Exercise the real family gate, queue capability and outbox, not just transport."""
        from digital_tutor.application import Application, Clock
        from digital_tutor.jobs import JobQueue
        from digital_tutor.mock_channel import channel_for
        from digital_tutor.outbox import reconcile
        from digital_tutor.wechat import read_message_stream, send_message

        family = family_id(self.route)
        app = Application(Path(self.tmp.name)/'business.sqlite',
                          Clock('2026-10-19T10:00:00+08:00'), mode='passive',
                          strict_runtime=True, service_start=0, service_end=24)
        app.context_mode = 'daily_native'
        app.steering_enabled = True
        app.workflow_enabled = True
        service_token = None
        try:
            binding = app.create_family(family, family, 'wecom:'+family, mock=False)
            self.assertEqual(binding['mock'], 0)
            channel_token = app.open_session(family, 'channel')
            try:
                message = app.ingest_message(channel_token, {
                    'external_id':'synthetic-native-reply-test', 'body':'我想和你聊聊。',
                    'occurred_at':app.clock.iso(),
                    'source_metadata':{'source_id':'synthetic-native-reply-test',
                        'source_alias':'synthetic-unit-test', 'source_row':1,
                        'source_sha256':'0'*64, 'speaker_role':'family_member_unknown',
                        'media_status':'text'}})['data']
            finally:
                app.revoke_session(channel_token)

            queue = JobQueue(app)
            claim = queue.claim('native-wecom-unit-test', lease_seconds=60)
            self.assertIsNotNone(claim)
            service_token = queue.session(claim)
            read = read_message_stream(app, service_token, {
                'scope':'all_history', 'direction':'inbound', 'after_seq':0, 'limit':100})
            self.assertIn(message['id'], [m['id'] for m in read['messages']])
            prefix = claim['job']['run_id']
            app.interpret(service_token, {
                'request_id':prefix+'-interpret', 'message_id':message['id'],
                'expected_version':message['version'],
                'intents':[{'intent_id':'chat', 'text':'想聊聊', 'requires_reply':True}]})
            body = '好呀，我在听。' if resolves_intent else '好呀，你想先聊哪件事？'
            args = {'request_id':prefix+'-send',
                    'expected_family_version':app.store.family(family)['version'],
                    'body':body, 'action_kind':action_kind,
                    'reply_to':[{'message_id':message['id'], 'intent_id':'chat',
                                 'resolves_intent':resolves_intent}]}
            with patch('digital_tutor.wecom_transport.http_send',
                       return_value={'errcode':0,'requestId':'native-provider-request'}) as http:
                operation = send_message(app, service_token, args)['data']
                self.assertEqual(operation['stage'], 'accepted')
                self.assertFalse(operation['simulated'])
                self.assertEqual(operation['channel'], 'wecom_live')
                self.assertEqual(operation['dispatch_count'], 1)
                http.assert_called_once()
                self.assertEqual(http.call_args.args[1], {
                    'externalRequestId':operation['id'], 'imBotId':self.route['imBotId'],
                    'imContactId':self.route['imContactId'], 'messageType':7,
                    'payload':{'text':body}})
                self.assertFalse([m for m in app.store.list('message', family)
                                  if m['direction']=='outbound'])

                transport = channel_for(app)
                self.assertIsInstance(transport, WecomChannel)
                self.assertTrue(transport.observe_receipt({**self.route,
                    'externalRequestId':operation['id'], 'requestId':'native-provider-request',
                    'errorCode':0, 'sendCode':0, 'messageId':'synthetic-provider-message'}))
                confirmed = reconcile(app, family, operation['id'])
                self.assertEqual(confirmed['stage'], 'sent')
                sent = [m for m in app.store.list('message', family) if m['direction']=='outbound']
                self.assertEqual([m['body'] for m in sent], [body])
                self.assertEqual(sent[0]['source_kind'], 'wecom_live')
                intent = app.store.get('message', message['id'], family)['intents'][0]
                if resolves_intent:
                    self.assertEqual(intent['reply_operation_refs'], [operation['id']])
                else:
                    self.assertEqual(intent['reply_operation_refs'], [])
                    self.assertEqual(intent['partial_reply_operation_refs'], [operation['id']])
                self.assertEqual(send_message(app, service_token, args)['outcome'], 'duplicate')
                http.assert_called_once()
        finally:
            if service_token:
                app.revoke_session(service_token)
            app.close()

    def test_native_real_family_reply_reaches_http_and_receipt(self):
        self.assert_native_reply_path('reply', True)

    def test_native_real_family_clarification_reaches_http_and_keeps_question(self):
        self.assert_native_reply_path('clarify_existing', False)


if __name__=='__main__':unittest.main()
