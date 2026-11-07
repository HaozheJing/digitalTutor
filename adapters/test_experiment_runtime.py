import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
import test_message_hooks as base
import sql_tools as s
import test_mode as tm
import wechat_test as w
import message_hooks as h
import experiment_runtime as ex
import emoji_assets as emoji


class ExperimentTests(base.MessageHookTests):
    # Inherit the baseline Hook suite too: the same engine must keep its behavior.
    def enable_experiment(self, outcome='sent'):
        self.addCleanup(patch.stopall)
        self.root.joinpath('config').mkdir(exist_ok=True)
        oldroot=s.R; s.R=self.root;self.addCleanup(setattr,s,'R',oldroot)
        olddb=s.DB
        self.root.joinpath('runtime').mkdir(exist_ok=True)
        with closing(sqlite3.connect(olddb)) as source,closing(sqlite3.connect(self.root/'runtime/live.sqlite')) as dest:source.backup(dest)
        s.DB=self.root/'runtime/live.sqlite'
        self.addCleanup(setattr,s,'DB',olddb)
        state=tm.state()
        self.value={'schema_version':1,'simulation':True,'workspace':str(self.root),'experiment_run_id':'exp-'+'a'*32,'family_uid':state['uid'],'business_run_id':state['run_id'],'frozen_files':{},'transport_outcome':outcome}
        (self.root/'config/experiment.json').write_text(json.dumps(self.value))
        self.configpatch.stop()
        self.addCleanup(self.configpatch.start)

    def assets(self):
        self.root.joinpath('config').mkdir(exist_ok=True)
        oldroot=s.R;s.R=self.root;self.addCleanup(setattr,s,'R',oldroot)
        self.registry={'version':'v1','allowed_hosts':['assets.example.test'],'assets':[{'asset_id':'thanks','status':'approved','reviewed_by':'test-reviewer','sha256':'a'*64,'url':'https://assets.example.test/immutable-a.png'}]}
        self.asset_file=self.root/'config/emoji-assets.json';self.asset_file.write_text(json.dumps(self.registry))
        return [{'asset_id':'thanks','version':'v1'}]

    def test_simulation_uses_outbox_then_explicit_final_receipt(self):
        self.enable_experiment();self.queue()
        with patch.object(w,'build_opener',side_effect=AssertionError('no external HTTP')):
            self.assertEqual(w.flush()[0]['status'],'accepted')
            self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'accepted')
            self.assertEqual(w.receive()['applied'],1)
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'sent')
        self.assertTrue(json.loads(s.read('SELECT body FROM experiment_receipts')[0]['body'])['simulation'])
        self.assertEqual(w.flush(),[])
        self.assertEqual(w.receive()['applied'],0)

    def test_unknown_preserved_across_recovery_no_resend(self):
        self.enable_experiment('unknown');self.queue()
        self.assertEqual(w.flush()[0]['status'],'unknown')
        __import__('live_runtime').recover()
        self.assertEqual(w.flush(),[]);self.assertEqual(w.receive()['applied'],0)
        self.assertEqual(len(s.read('SELECT * FROM test_outbox')),1)

    def test_frozen_configuration_and_production_channel_fail_closed(self):
        self.enable_experiment();file=self.root/'config/component.md';file.write_text('first')
        self.value['frozen_files']={'config/component.md':hashlib.sha256(file.read_bytes()).hexdigest()}
        (self.root/'config/experiment.json').write_text(json.dumps(self.value));self.assertIsNotNone(ex.verify())
        file.write_text('changed')
        with self.assertRaisesRegex(ValueError,'configuration_changed'):ex.verify()
        (self.root/'config/test-wechat.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'external_channel'):ex.manifest()

    def test_inbound_bound_idempotent_and_real_sixty_second_gate(self):
        self.enable_experiment()
        data={'experiment_run_id':self.value['experiment_run_id'],'run_id':self.value['business_run_id'],'family_uid':self.value['family_uid'],'request_id':'one','body':'你好'}
        self.assertEqual(ex.inbound(data)['status'],'recorded');self.clock+=59
        self.assertEqual(ex.inbound(data)['status'],'duplicate')
        self.assertIsNone(__import__('live_runtime').claim());self.clock+=1
        self.assertIsNotNone(__import__('live_runtime').claim())
        with self.assertRaisesRegex(ValueError,'scope_mismatch'):ex.inbound({**data,'family_uid':'another'})
        with self.assertRaisesRegex(ValueError,'immutable_request'):ex.inbound({**data,'body':'changed'})

    def test_emoji_requires_approved_asset_and_preserves_all_parts(self):
        refs=self.assets()
        with emoji.selection(refs):self.queue()
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'text'},{'errcode':0,'requestId':'emoji'}]) as send:
            self.assertEqual(w.flush()[0]['status'],'accepted');self.assertEqual(send.call_args_list[1].args[-1],5)
        rows=s.read('SELECT * FROM test_outbox ORDER BY rowid');self.assertEqual([r['message_type'] for r in rows],[7,5])
        with closing(sqlite3.connect(s.DB)) as c,c:w.observe(c,{'externalRequestId':rows[0]['id'],'sendCode':0,'messageId':'text-final'})
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'accepted')
        with closing(sqlite3.connect(s.DB)) as c,c:w.observe(c,{'externalRequestId':rows[1]['id'],'sendCode':0,'messageId':'emoji-final'})
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'sent')

    def test_emoji_new_inbound_stops_after_text_without_replaying(self):
        with emoji.selection(self.assets()):self.queue()
        def transport(*args):self.inbound('between');return {'errcode':0,'requestId':'text'}
        with patch.object(w,'http_send',side_effect=transport) as send:
            self.assertEqual(w.flush()[0]['status'],'replan_required');self.assertEqual(send.call_count,1)
        self.assertEqual(len(s.read('SELECT * FROM test_outbox')),1)
        self.assertEqual(w.flush(),[])

    def test_emoji_url_injection_and_changed_registry_rejected(self):
        refs=self.assets()
        with self.assertRaises(ValueError):emoji.resolve([{'asset_id':'thanks','version':'v1','url':'https://evil.test'}])
        with emoji.selection(refs):self.queue()
        self.registry['assets'][0]['status']='disabled';self.asset_file.write_text(json.dumps(self.registry))
        with closing(sqlite3.connect(s.DB)) as c:
            with self.assertRaises(ValueError):emoji.validate_part(c,tm.state()['run_id']+':draft:emoji:0','https://assets.example.test/immutable-a.png')

    def test_emoji_wire_payload(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'{"errcode":0,"requestId":"r"}'
        with patch.object(w,'build_opener') as opener:
            opener.return_value.open.return_value=Response()
            w.http_send({'api_base_url':'https://provider.example.test','expected_token':'test'}, {'imBotId':'b','imContactId':'c'}, 'stable', 'https://assets.example.test/a.gif',5)
            body=json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(body['messageType'],5);self.assertEqual(body['payload'],{'imageUrl':'https://assets.example.test/a.gif'})

    def test_emoji_retry_cannot_remove_required_part(self):
        refs=self.assets()
        with emoji.selection(refs):self.queue()
        with closing(sqlite3.connect(s.DB)) as c:
            with self.assertRaisesRegex(ValueError,'immutable_delivery_emoji'):emoji.check_existing(c,'draft')
            with emoji.selection(refs):emoji.check_existing(c,'draft')
        self.assertEqual(len(s.read('SELECT * FROM test_delivery_emojis')),1)


if __name__=='__main__':unittest.main()
