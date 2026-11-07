import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from reply_lab import Fault
from reply_lab_emojis import package, bind_rule, selected_parts, RULE


class EmojiPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.raw = b'fixture-image-bytes'
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.storage = self.root/'runtime/teacher-skill-workbench/reply-lab/assets'
        self.storage.mkdir(parents=True); (self.storage/self.sha).write_bytes(self.raw)
        self.publications = self.root/'runtime/quality-source-snapshot/emoji-publications.json'
        self.publications.parent.mkdir(parents=True)
        self.url = 'https://assets.example.test/'+self.sha+'.gif'
        self.publications.write_text(json.dumps({'allowed_hosts':['assets.example.test'], 'assets':{
            self.sha:{'sha256':self.sha,'url':self.url}}}))
        self.config = {'asset_rule': RULE, 'skills': {'reply-style': {'SKILL.md':'Style'}}, 'assets':[
            {'version':'asset-v1','card':{'sha256':self.sha,'review':'approved','reviewer':'test-reviewer',
            'caption':'收到','visible_text':'收到','intents':['acknowledge'],'suitable':[],'avoid':[]}}]}
        bind_rule(self.config['skills'],RULE)

    def test_frozen_bytes_and_exact_native_reference(self):
        pack = package(self.root,self.config,self.root/'run-assets')
        self.assertEqual((self.root/'run-assets'/self.sha).read_bytes(),self.raw)
        parts = selected_parts({'parts':[{'id':'p','message_type':5,'body':self.url,'status':'sent'}]},pack)
        self.assertEqual(parts[0]['asset_version'],'asset-v1')
        self.assertEqual(parts[0]['sha256'],self.sha)
        self.assertTrue(parts[0]['simulation'])

    def test_corrupt_original_fails_before_native_launch(self):
        (self.storage/self.sha).write_bytes(b'changed')
        with self.assertRaises(Fault):package(self.root,self.config,self.root/'run-assets')

    def test_unreviewed_or_unpublished_asset_rejected(self):
        self.config['assets'][0]['card']['review']='candidate'
        with self.assertRaises(Fault):package(self.root,self.config,self.root/'run-assets')
        self.config['assets'][0]['card']['review']='approved'
        self.publications.write_text('{"assets":{},"allowed_hosts":[]}')
        with self.assertRaises(Fault):package(self.root,self.config,self.root/'run-assets')

    def test_untrusted_url_and_unbound_rule_rejected(self):
        data=json.loads(self.publications.read_text());data['assets'][self.sha]['url']='https://evil.test/a.gif'
        self.publications.write_text(json.dumps(data))
        with self.assertRaises(Fault):package(self.root,self.config,self.root/'run-assets')
        data['assets'][self.sha]['url']=self.url;self.publications.write_text(json.dumps(data))
        self.config['asset_rule']='changed after freeze'
        with self.assertRaises(Fault):package(self.root,self.config,self.root/'run-assets')

    def test_unknown_native_output_not_projected_as_approved(self):
        pack=package(self.root,self.config,self.root/'run-assets')
        with self.assertRaises(Fault):selected_parts({'parts':[{'id':'p','message_type':5,'body':'https://evil.test/a.gif','status':'sent'}]},pack)

    def test_binding_round_trip_does_not_duplicate_prompt(self):
        old=self.config['skills']['reply-style']['SKILL.md']
        bind_rule(self.config['skills'],'new rule')
        self.assertEqual(self.config['skills']['reply-style']['SKILL.md'],old)
        self.assertEqual(self.config['skills']['reply-style']['references/emoji-selection.md'],'new rule')

    @unittest.skipIf(__import__('os').name == 'nt', 'Linux native worker uses flock')
    def test_real_channel_lease_blocks_lab_before_launch(self):
        import fcntl
        from reply_lab_native import NativeRunner
        with (self.root/'runtime/emoji-real-window.lock').open('a+') as lease:
            fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            runner=NativeRunner(self.root,self.root/'runtime/worker.json')
            with self.assertRaisesRegex(Fault,'真实渠道联调'):runner.run({})


if __name__=='__main__':unittest.main()
