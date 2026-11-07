import base64
import json
from pathlib import Path
from unittest.mock import patch
import unittest
import test_reply_lab as reply_tests
from test_voice_understanding import audio
from reply_lab import Fault

class ReplyVoiceTests(unittest.TestCase):
    setUp=reply_tests.ReplyLabTests.setUp
    config=reply_tests.ReplyLabTests.config
    scenario=reply_tests.ReplyLabTests.scenario
    native_result=reply_tests.ReplyLabTests.native_result
    def test_audio_snapshot_is_bound_to_owner_and_hash(self):
        from types import SimpleNamespace
        self.lab.runner=SimpleNamespace(run=self.native_result)
        raw=audio();config=self.config();scene=self.scenario()
        job=self.lab.start(self.actor,{'config':config['id'],'scenario':scene['id'],
            'messages':['语音'],'audio':{'format':'wav','file_base64':base64.b64encode(raw).decode()}})
        self.lab.pool.submit(lambda:None).result(timeout=10)
        run=self.lab.run(self.actor,job['id'])
        self.assertEqual(run['status'],'done',run['result'])
        self.assertNotIn('file_base64',json.dumps(run['envelope']))
        preview=self.lab.dispatch(self.actor,'audio-preview',{'id':job['id']})
        self.assertEqual(base64.b64decode(preview['file_base64']),raw)
        with self.assertRaises(Fault):self.lab.dispatch(self.other,'audio-preview',{'id':job['id']})
        (Path(run['envelope']['runtime_root'])/run['envelope']['audio']['file']).write_bytes(b'changed')
        with self.assertRaises(Fault):self.lab.dispatch(self.actor,'audio-preview',{'id':job['id']})

    def test_fixture_never_claims_audio_understanding(self):
        with self.assertRaises(Fault):self.lab.start(self.actor,{'config':self.config()['id'],'scenario':self.scenario()['id'],'engine':'fixture','audio':{'format':'wav','file_base64':base64.b64encode(audio()).decode()}})

if __name__=='__main__':unittest.main()
