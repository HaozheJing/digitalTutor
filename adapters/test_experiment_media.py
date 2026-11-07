import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import experiment_media as media


class MediaCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'workspace';(self.root/'config').mkdir(parents=True)
        (self.root/'config/experiment.json').write_text('{}')
        self.private=Path(self.temp.name)/'operator.json'
        self.private.write_text(json.dumps({'api_key':'synthetic-only','base_url':media.ORIGIN,'unrelated_production_setting':'not_copied'}))
        self.operator={'media':{'credential_file':str(self.private),'image_model':'image-test','inspection_model':'vision-test'}}

    def test_binding_is_private_scoped_immutable_and_no_config_copy(self):
        binding=media.bind(self.root,self.operator)
        self.assertEqual(media.bind(self.root,self.operator),binding)
        with patch.dict(os.environ,{'TUTOR_MEDIA_BINDING_SHA256':binding['sha256']}):
            path=media.configuration(self.root,'service');cfg=json.loads(path.read_text())
            self.assertEqual(cfg['asset_directory'],str(self.root/'artifacts/media'))
            private=media.configuration(self.root,'provider')
            self.assertEqual(set(json.loads(private.read_text())),{'api_key','base_url','vlm_model','inspection_parameters'})
            self.assertEqual(json.loads(private.read_text())['inspection_parameters'],{'max_tokens':8192})
            self.assertEqual(private.stat().st_mode & 0o777,0o600)
            private.write_text('{}')
            with self.assertRaisesRegex(ValueError,'file_changed'):media.configuration(self.root,'service')
        with self.assertRaisesRegex(ValueError,'changed_on_restart'):media.bind(self.root,self.operator)
        self.assertEqual(list((self.root/'config').iterdir()),[self.root/'config/experiment.json'])

    def test_experiment_never_falls_back_to_production_config(self):
        (self.root/'config/service-lab-media.json').write_text('{"asset_directory":"/production"}')
        with patch.dict(os.environ,{},clear=True):self.assertIsNone(media.configuration(self.root,'service'))

    def test_symlinked_capability_and_wrong_provider_are_rejected(self):
        self.private.write_text(json.dumps({'api_key':'fake','base_url':'https://untrusted.invalid'}))
        with self.assertRaisesRegex(ValueError,'plan_route'):media.bind(self.root,self.operator)
        self.private.write_text(json.dumps({'api_key':'fake','base_url':media.ORIGIN}))
        (self.root/'runtime').mkdir();(self.root/'runtime/media-capability').symlink_to(self.private.parent,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'path_invalid'):media.bind(self.root,self.operator)


if __name__=='__main__':unittest.main()
