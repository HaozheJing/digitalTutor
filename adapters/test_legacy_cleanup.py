"""Regression checks for archived materials and compatibility imports."""
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import service_content as content
import service_catalog as catalog
import legacy_limits
from legacy.delivery_limits import with_current_limits

class LegacyCleanupTests(unittest.TestCase):
    def test_archived_reference_stays_disabled_without_active_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'references').mkdir();skill=root/'SKILL.md';skill.write_text('current')
            with patch.object(content,'skill_file',return_value=skill):
                for path in ['references/supervision.md','references/../references/supervision.md']:
                    result=content.reference('service-policy',path)
                    self.assertEqual(result['status'],'disabled')
                    self.assertNotIn('archive',result.get('body','').lower())
                with self.assertRaises(ValueError):content.reference('service-policy','references/missing.md')
                with self.assertRaises(ValueError):content.reference('service-policy','../supervision.md')

    def test_catalog_omits_retired_material_but_keeps_all_actions(self):
        result=catalog.catalog()
        self.assertEqual(len(result['actions']),36)
        self.assertEqual(len({a['skill'] for a in result['actions']}),32)
        self.assertTrue(all(a['mounted'] for a in result['actions']))
        for action in result['actions']:
            self.assertNotIn({'name':'service-policy','path':'references/supervision.md'},action['dependencies']['references'])
        self.assertEqual({a['skill'] for a in result['actions'] if a['id'] in ('I01','I02','I03','I04')},{'invite-reading-interaction'})

    def test_historical_names_and_migration_import_still_work(self):
        self.assertIs(legacy_limits.with_current_limits,with_current_limits)
        self.assertEqual(catalog.canonical_skill('practice-reading-method'),'invite-reading-interaction')
        self.assertEqual(catalog.KNOWLEDGE_SKILL_ROUTES['explain-lesson'],['explain-reading-content'])
        self.assertEqual(with_current_limits('每条最多30字'),'每条最多50字')

    def test_retired_text_is_preserved_but_excluded_from_bundle(self):
        root=Path(content.__file__).resolve().parents[1]
        archive=root/'legacy/skills/service-policy/references/supervision.md'
        self.assertTrue(archive.is_file())
        files=content.reference_files('service-policy')
        self.assertNotIn(root/'skills/service-policy/references/supervision.md',files)
        self.assertTrue(all('legacy' not in p.relative_to(root).parts for p in files))

if __name__=='__main__':unittest.main()
