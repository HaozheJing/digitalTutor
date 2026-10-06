"""Scoped knowledge, provenance, native Skill adapter, and mandatory guide tests."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from digital_tutor.domain import BusinessError
from digital_tutor.knowledge import KnowledgeBinding, KnowledgeCatalog, decode_cli_json
from digital_tutor.skill_loader import SkillCatalog, FAMILY_SKILLS, ALIASES


ROOT = Path(__file__).resolve().parents[2]


class FakeKnowledge:
    def __init__(self):
        self.calls = []
        self.bodies = {}

    def read(self, uri):
        self.calls.append(uri)
        return self.bodies[uri]


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeKnowledge()
        self.manifest = {'revision': 'r1', 'target_root': 'viking://resources/test/r1', 'units': []}
        for day in (1, 2):
            uri, body = f'viking://resources/test/r1/day-{day}.md', f'Only day {day}\n'
            self.client.bodies[uri] = body
            self.manifest['units'].append({
                'unit_id': f'day-{day}', 'record_type': 'lesson', 'source_day': day,
                'course_content_version': None, 'business_review_status': 'unverified',
                'actual_read_uris': [uri], 'file_sha256': hashlib.sha256(body.encode()).hexdigest(),
            })
        self.catalog = KnowledgeCatalog([self.manifest], self.client)
        self.binding = KnowledgeBinding('r1', ('day-1',), simulation=True)

    def assert_code(self, code, fn):
        with self.assertRaises(BusinessError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_precise_day_body_and_unknown_version_provenance(self):
        result = self.catalog.read('service', self.binding, 'day-1')
        self.assertEqual(result['content'], 'Only day 1\n')
        self.assertEqual(result['provenance']['source_day'], 1)
        self.assertIsNone(result['provenance']['course_content_version'])
        self.assertTrue(result['provenance']['simulation_binding'])
        self.assertNotIn('actual_read_uris', json.dumps(result))
        self.assertEqual(len(self.client.calls), 1)

    def test_unknown_real_binding_and_other_day_fail_before_client(self):
        self.assert_code('KNOWLEDGE_BINDING_UNKNOWN', lambda: self.catalog.list('service', None))
        self.assert_code('KNOWLEDGE_BINDING_UNKNOWN', lambda: self.catalog.read(
            'service', KnowledgeBinding('r1', ('day-1',)), 'day-1'))
        self.assert_code('KNOWLEDGE_SCOPE_FORBIDDEN', lambda: self.catalog.read('service', self.binding, 'day-2'))
        self.assertEqual(self.client.calls, [])

    def test_missing_day_does_not_substitute_and_revision_is_exact(self):
        self.assert_code('KNOWLEDGE_GAP', lambda: self.catalog.list(
            'service', KnowledgeBinding('r2', ('day-1',), simulation=True)))
        self.assert_code('KNOWLEDGE_GAP', lambda: self.catalog.list(
            'service', KnowledgeBinding('r1', ('day-3',), simulation=True)))
        self.assertEqual(self.client.calls, [])

    def test_body_drift_and_backend_error_do_not_fallback(self):
        uri = self.manifest['units'][0]['actual_read_uris'][0]
        self.client.bodies[uri] = 'Only day 2\n'
        self.assert_code('KNOWLEDGE_REVISION_CHANGED', lambda: self.catalog.read('service', self.binding, 'day-1'))
        del self.client.bodies[uri]
        self.assert_code('KNOWLEDGE_UNAVAILABLE', lambda: self.catalog.read('service', self.binding, 'day-1'))
        self.assertEqual(self.client.calls, [uri, uri])

    def test_manifest_rejects_escape_uri_and_duplicate_identity(self):
        for uri in ('viking://resources/other/day.md', 'viking://resources/test/r1/../r2/a.md',
                    'viking://resources/test/r1/%2e%2e/a.md'):
            malformed = copy.deepcopy(self.manifest)
            malformed['units'][0]['actual_read_uris'] = [uri]
            self.assert_code('INVALID_MANIFEST', lambda: KnowledgeCatalog([malformed], self.client))
        self.assert_code('INVALID_MANIFEST', lambda: KnowledgeCatalog([self.manifest, self.manifest], self.client))

    def test_product_reference_requires_host_authorization(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['units'][0].update(record_type='product', retrieval_scope='reference')
        catalog = KnowledgeCatalog([manifest], self.client)
        self.assertEqual(catalog.list('service', self.binding)['documents'], [])
        approved = KnowledgeBinding('r1', ('day-1',), allow_reference=True)
        self.assertEqual(catalog.read('service', approved, 'day-1')['content'], 'Only day 1\n')

    def test_cli_parser_ignores_echoed_query_json(self):
        self.assertEqual(decode_cli_json('ov read {"not":"result"}\n{"result":{"content":"body"}}'),
                         {'result': {'content': 'body'}})


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'agent'
        shutil.copytree(ROOT / 'agent', self.root)
        self.calls = []

        def reader(name):
            self.calls.append(name)
            path = self.root / 'skills' / name / 'SKILL.md'
            return {'success': True, '_source_path': str(path), 'content': path.read_text(encoding='utf-8')}

        self.reader = reader
        self.catalog = SkillCatalog(self.root, reader)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_code(self, code, fn):
        with self.assertRaises(BusinessError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_approved_inventory_and_alias_no_duplicate_entry(self):
        self.assertEqual(len(FAMILY_SKILLS), 34)
        self.assertEqual(len(self.catalog.entries), 41)
        self.assertEqual(len(self.catalog.list('service')['skills']), 36)
        listed = {item['skill_id'] for item in self.catalog.list('admin')['skills']}
        self.assertEqual(len(listed), 37)
        self.assertFalse(listed & set(ALIASES))
        alias = self.catalog.read('service', 'recommend-catch-up-reading')
        self.assertEqual(alias['canonical_skill_id'], 'prompt-backlog-makeup')
        self.assertEqual(self.calls, ['prompt-backlog-makeup'])

    def test_native_read_full_skill_and_all_required_guides(self):
        result = self.catalog.read('service', 'explain-lesson')
        self.assertEqual({g['guide_id'] for g in result['required_guides']}, {'service-basics', 'communication', 'qa'})
        self.assertTrue(all(g['content'] and len(g['sha256']) == 64 for g in result['required_guides']))
        self.assertIn('daily', result['runtime_constraints'])
        self.assertFalse(result['automatic_triggers_enabled'])

    def test_role_and_path_escape_rejected_before_native_read(self):
        self.assert_code('SKILL_SCOPE_FORBIDDEN', lambda: self.catalog.read('service', 'plan-proactive-service'))
        self.assert_code('SKILL_SCOPE_FORBIDDEN', lambda: self.catalog.read('planner_progress', 'explain-lesson'))
        for name in ('../explain-lesson', '/etc/passwd', 'x:y', 'x\\y'):
            self.assert_code('SKILL_PATH_FORBIDDEN', lambda: self.catalog.read('service', name))
        self.assertEqual(self.calls, [])

    def test_native_shadowing_or_content_change_rejected(self):
        self.catalog.native_reader = lambda name: {**self.reader(name), '_source_path': '/another/SKILL.md'}
        self.assert_code('SKILL_SOURCE_MISMATCH', lambda: self.catalog.read('service', 'explain-lesson'))
        self.catalog.native_reader = lambda name: {**self.reader(name), 'content': 'wrong body'}
        self.assert_code('SKILL_SOURCE_MISMATCH', lambda: self.catalog.read('service', 'explain-lesson'))

    def test_changed_required_guide_cannot_silently_load(self):
        (self.root / 'guides' / 'qa.md').write_text('changed', encoding='utf-8')
        self.assert_code('SKILL_REVISION_CHANGED', lambda: self.catalog.read('service', 'explain-lesson'))
