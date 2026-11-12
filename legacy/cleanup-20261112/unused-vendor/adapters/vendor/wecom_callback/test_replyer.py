import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
import replyer
import workbench


class ReplyerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.session={'id':'test','path':str(self.root),'cutoff_id':0}
        self.route={'orgId':'org','imBotId':'bot','chatId':'chat','imContactId':'contact'}
        self.payload={**self.route,'messageId':'one','timestamp':1792395867554,'payload':{'text':'你好'}}
        cfg={ 'auto_reply_enabled':True,'expected_token':'synthetic-test-only',
              'expected_org_id':'org','allowed_routes':[self.route],'api_base_url':'https://example.invalid'}
        p=self.root/'config.json';p.write_text(json.dumps(cfg))
        self.chosen={'engine':'replyer','version':'unit-v1','parts':{'common':'COMMON','replyer':'REPLYER','tone':'TONE'},'fixture_id':'free_chat'}
        self.patches=[patch('test_context.current',side_effect=lambda:self.session),
            patch.object(workbench,'active',return_value=self.chosen),patch.object(workbench,'STATE',self.root),
            patch.dict(os.environ,{'DIGITAL_TUTOR_WECOM_CONFIG':str(p)})]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_free_chat_has_three_prompt_parts_no_service_and_dedup(self):
        replyer.receive(self.payload,self.route,1);replyer.receive(self.payload,self.route,2)
        with patch.object(replyer,'generate',return_value='你好，我在呢。') as model, \
             patch('digital_tutor.wecom_transport.http_send',return_value={'errcode':0,'requestId':'r1'}) as http:
            replyer.run_once();replyer.run_once()
            self.assertEqual(model.call_count,1);self.assertEqual(http.call_count,1)
            request=model.call_args.args[0]
            self.assertEqual(request[0],{'role':'system','content':'COMMON\n\nREPLYER\n\nTONE'})
            self.assertEqual([m['role'] for m in request],['system','user'])
            self.assertEqual(request[-1]['content'],'你好')
            self.assertFalse(replyer.has_work())
        with closing(replyer.connect()) as db:
            row=db.execute('SELECT * FROM turns').fetchone()
            self.assertEqual(row['status'],'accepted');op=row['id']
        self.assertTrue(replyer.observe_receipt({**self.route,'externalRequestId':op,'requestId':'r1','sendCode':0,'messageId':'sent'}))
        self.assertEqual(replyer.messages()[-1]['status'],'sent')

    def test_timeout_never_resends_or_exposes_unsent_as_history(self):
        replyer.receive(self.payload,self.route,1)
        with patch.object(replyer,'generate',return_value='first'),patch('digital_tutor.wecom_transport.http_send',side_effect=TimeoutError) as http:
            replyer.run_once();replyer.run_once();self.assertEqual(http.call_count,1)
        replyer.receive({**self.payload,'messageId':'two','payload':{'text':'第二句'}},self.route,2)
        with patch.object(replyer,'generate',return_value='second') as model,patch('digital_tutor.wecom_transport.http_send',return_value={'errcode':0,'requestId':'r2'}):
            replyer.run_once()
            self.assertFalse(any(m['role']=='assistant' for m in model.call_args.args[0]))

    def test_new_context_does_not_import_old_history(self):
        replyer.receive(self.payload,self.route,1)
        new=self.root/'fresh';new.mkdir();self.session={**self.session,'id':'fresh','path':str(new)}
        self.assertFalse(replyer.has_work());self.assertEqual(replyer.messages(),[])

    def test_failure_is_visible_and_does_not_send(self):
        replyer.receive(self.payload,self.route,1)
        with patch.object(replyer,'generate',side_effect=TimeoutError),patch('digital_tutor.wecom_transport.http_send') as http:
            replyer.run_once();http.assert_not_called()
        self.assertFalse(replyer.has_work());self.assertEqual(replyer.messages()[0]['status'],'failed')

    def test_workbench_assembles_three_editable_parts(self):
        text,extra=workbench.replyer_input({'common':' A ','replyer':' B ','tone':' C ','fixture_id':'child_fox'})
        self.assertEqual(text,'A\n\nB\n\nC\n');self.assertEqual(extra['fixture_id'],'child_fox')
        with self.assertRaises(ValueError):workbench.replyer_input({'common':'','replyer':'B','tone':'C','fixture_id':'free_chat'})
        with self.assertRaises(ValueError):replyer.selected_fixture('not-real')


    def test_only_nonfree_scene_adds_synthetic_service_and_preserves_history(self):
        history=[{'role':'user','content':'earlier'},{'role':'assistant','content':'prior reply'}]
        free=replyer.compose_request(self.chosen['parts'],replyer.selected_fixture('free_chat'),history,'now')
        self.assertEqual(free[1:-1],history)
        packet=replyer.compose_request(self.chosen['parts'],replyer.selected_fixture('child_fox'),history,'now')
        self.assertIn('模拟 Service',packet[1]['content'])
        self.assertEqual(packet[2:-1],history)
        self.assertEqual(packet[-1],{'role':'user','content':'now'})

    def test_stale_editor_cannot_silently_drop_tone_and_legacy_version_readable(self):
        with self.assertRaisesRegex(ValueError,'编辑器已升级'):
            workbench.replyer_input({'common':'A','replyer':'B','fixture_id':'free_chat'})
        self.assertEqual(replyer.system_text({'common':'A','replyer':'B'}),'A\n\nB')


if __name__=='__main__':unittest.main()
