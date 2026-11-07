import datetime as dt
import hashlib
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import test_live as fixtures
import service_content as content
import sql_tools as s


class ServiceContentTests(unittest.TestCase):
    create_source = fixtures.LiveTests.create_source
    set_context = fixtures.LiveTests.set_context

    def setUp(self):
        fixtures.LiveTests.setUp(self)
        self.old_root = s.R
        s.R = self.root
        self.clock = dt.datetime.fromisoformat('2025-10-25T19:00:00+08:00')
        self.addCleanup(patch.stopall)
        patch.object(content.tm, 'state', return_value={
            'uid': '00001', 'as_of': self.clock.isoformat()
        }).start()
        patch.object(content.tm, 'clock', return_value=self.clock).start()
        self.skill = self.root / 'skills' / 'answer-family-question'
        (self.skill / 'references').mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('test skill', encoding='utf-8')
        tier_skill = self.root / 'skills' / 'student-service-tier'
        tier_skill.mkdir()
        (tier_skill / 'SKILL.md').write_text('test tier skill', encoding='utf-8')
        self.reference_text = '\n'.join(f'第{n}行' for n in range(1, 132))
        (self.skill / 'references' / 'sample.md').write_text(
            self.reference_text, encoding='utf-8')

    def tearDown(self):
        s.R = self.old_root
        fixtures.LiveTests.tearDown(self)

    def configure(self, **updates):
        config = {
            'endpoint': 'https://tier.example.invalid/student?camp=test',
            'uid': 'configured-other',
            'headers': {'Authorization': 'Bearer private-test-token'}
        }
        config.update(updates)
        (self.root / 'config').mkdir(exist_ok=True)
        (self.root / 'config' / 'student-tier.json').write_text(
            json.dumps(config), encoding='utf-8')

    def tier_value(self, **updates):
        value = {
            'uid': '00001', 'level': 'A', 'source': 'official-tier-api',
            'observed_at': '2025-10-25T18:00:00+08:00',
            'valid_until': '2025-10-26T18:00:00+08:00'
        }
        value.update(updates)
        return value

    def read_tier(self, value=None, raw=None, error=None):
        opener = Mock()
        if error:
            opener.open.side_effect = error
        else:
            payload = raw if raw is not None else json.dumps(value).encode()
            opener.open.return_value = io.BytesIO(payload)
        with patch.object(content, 'build_opener', return_value=opener) as build:
            result = content.student_tier()
            build.assert_called_once()
            self.assertIsInstance(build.call_args.args[0], content.NoRedirect)
        return result, opener

    def test_reference_pagination_keeps_version_and_stops_at_end(self):
        with patch.object(content, 'skill_file', return_value=self.skill / 'SKILL.md'):
            first = content.reference('answer-family-question', 'references/sample.md', 1, 120)
            last = content.reference('answer-family-question', 'references/sample.md', 121, 120)
        self.assertEqual(len(first['body'].splitlines()), 120)
        self.assertEqual(first['next_line'], 121)
        self.assertEqual(last['body'].splitlines()[0], '第121行')
        self.assertEqual(len(last['body'].splitlines()), 11)
        self.assertIsNone(last['next_line'])
        self.assertEqual(first['total_lines'], 131)
        digest = hashlib.sha256(self.reference_text.encode()).hexdigest()
        self.assertEqual(first['sha256'], digest)
        self.assertEqual(last['sha256'], digest)

    def test_disabled_supervision_reference_never_returns_its_old_body(self):
        file=self.skill/'references/supervision.md'
        file.write_text('old mandatory supervision wording',encoding='utf-8')
        with patch.object(content,'skill_file',return_value=self.skill/'SKILL.md'):
            for path in ['references/supervision.md','references/../references/supervision.md']:
                value=content.reference('service-policy',path)
                self.assertEqual(value['status'],'disabled')
                self.assertNotIn('old mandatory supervision wording',value['body'])
                self.assertIn('reply-style',value['body'])
            self.assertNotIn(file,content.reference_files('service-policy'))
            self.assertIn(self.skill/'references/sample.md',content.reference_files('service-policy'))

    def test_disabled_reference_does_not_participate_in_active_bundle(self):
        file=self.skill/'references/supervision.md';file.write_text('old rule',encoding='utf-8')
        with patch.object(content,'skill_file',return_value=self.skill/'SKILL.md'):
            first=content.bundle_digest('service-policy')
            file.write_text('changed inactive rule',encoding='utf-8')
            self.assertEqual(content.bundle_digest('service-policy'),first)
            (self.skill/'references/sample.md').write_text('changed active rule',encoding='utf-8')
            self.assertNotEqual(content.bundle_digest('service-policy'),first)

    def test_reference_rejects_escape_non_markdown_and_missing_paths(self):
        outside = self.root / 'private.md'
        outside.write_text('private material', encoding='utf-8')
        (self.skill / 'references' / 'sample.txt').write_text('not markdown')
        paths = [
            '../../private.md', str(outside), 'SKILL.md',
            'references/../SKILL.md', 'references/sample.txt', 'references/missing.md'
        ]
        with patch.object(content, 'skill_file', return_value=self.skill / 'SKILL.md'):
            for path in paths:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    content.reference('answer-family-question', path)

    def test_reference_rejects_symlink_escape(self):
        outside = self.root / 'private.md'
        outside.write_text('private material', encoding='utf-8')
        linked = self.skill / 'references' / 'linked.md'
        try:
            linked.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('This host does not permit creating symbolic links')
        with patch.object(content, 'skill_file', return_value=self.skill / 'SKILL.md'):
            with self.assertRaises(ValueError):
                content.reference('answer-family-question', 'references/linked.md')

    def test_reference_requires_bounded_integer_pagination(self):
        with patch.object(content, 'skill_file', return_value=self.skill / 'SKILL.md'):
            for start, count in [(0, 60), (-1, 60), (True, 60), ('1', 60),
                                 (1, 0), (1, 121), (1, True), (1, 2.5)]:
                with self.subTest(start=start, count=count), self.assertRaises(ValueError):
                    content.reference('answer-family-question', 'references/sample.md', start, count)

    def test_skill_names_are_allowlisted(self):
        for name in ['../../config', 'invented-skill', 'answer-family-question/../tutor-main']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                content.skill_file(name)
        self.assertTrue(content.skill_file('answer-family-question').is_file())
        self.assertTrue(content.skill_file('student-service-tier').is_file())

    def test_unconfigured_tier_is_unknown_without_network(self):
        with patch.object(content, 'build_opener') as build:
            result = content.student_tier()
        build.assert_not_called()
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['level'])
        self.assertEqual(result['uid'], '00001')
        self.assertEqual(result['reason'], 'tier_interface_not_configured')

    def test_invalid_endpoint_does_not_open_network(self):
        for endpoint in ['http://tier.example.invalid', 'https://user:secret@tier.example.invalid',
                         'https://tier.example.invalid/#fragment', '/relative']:
            with self.subTest(endpoint=endpoint):
                self.configure(endpoint=endpoint)
                with patch.object(content, 'build_opener') as build:
                    result = content.student_tier()
                build.assert_not_called()
                self.assertEqual(result['status'], 'unknown')

    def test_known_tiers_use_bound_uid_and_preserve_leading_zeroes(self):
        self.configure()
        for level in 'SABCD':
            with self.subTest(level=level):
                result, opener = self.read_tier(self.tier_value(level=level))
                self.assertEqual(result['status'], 'known')
                self.assertEqual(result['level'], level)
                self.assertEqual(result['uid'], '00001')
                self.assertIsNone(result['policy'])
                req = opener.open.call_args.args[0]
                self.assertEqual(parse_qs(urlsplit(req.full_url).query)['uid'], ['00001'])
                self.assertEqual(opener.open.call_args.kwargs['timeout'], 5)

    def test_wrong_identity_level_or_source_is_never_known(self):
        self.configure()
        cases = [
            {'uid': 'another-child'}, {'level': 'L2'}, {'level': 'a'},
            {'source': ''}, {'source': None}, {'source': {'nested': 'not a source'}},
            {'source': 'x' * 201}
        ]
        for updates in cases:
            with self.subTest(updates=updates):
                result, _ = self.read_tier(self.tier_value(**updates))
                self.assertEqual(result['status'], 'unknown')
                self.assertIsNone(result['level'])

    def test_future_expired_or_naive_observations_are_unknown(self):
        self.configure()
        cases = [
            {'observed_at': '2025-10-25T20:00:00+08:00'},
            {'observed_at': '2026-10-20T19:00:00+08:00'},
            {'valid_until': '2025-10-25T18:59:59+08:00'},
            {'observed_at': '2025-10-25T18:00:00'},
            {'valid_until': '2025-10-26T18:00:00'},
            {'observed_at': 'invalid'},
        ]
        for updates in cases:
            with self.subTest(updates=updates):
                result, _ = self.read_tier(self.tier_value(**updates))
                self.assertEqual(result['status'], 'unknown')
                self.assertIsNone(result['level'])

    def test_time_window_includes_exact_boundaries(self):
        self.configure()
        for field in ['observed_at', 'valid_until']:
            with self.subTest(field=field):
                result, _ = self.read_tier(self.tier_value(**{field: self.clock.isoformat()}))
                self.assertEqual(result['status'], 'known')

    def test_errors_and_oversized_bodies_do_not_disclose_private_data(self):
        self.configure()
        cases = [
            {'raw': b'not json private-test-token'},
            {'raw': b'x' * 65537},
            {'raw': b'{"unexpected":"private-test-token"}'},
            {'error': RuntimeError('private-test-token https://private.example.invalid')}
        ]
        for args in cases:
            with self.subTest(args=list(args)):
                result, _ = self.read_tier(**args)
                self.assertEqual(result['status'], 'unknown')
                self.assertNotIn('private-test-token', json.dumps(result))
                self.assertNotIn('private.example.invalid', json.dumps(result))

    def test_redirect_handler_refuses_cross_origin_and_http_downgrade(self):
        request = Request('https://tier.example.invalid/student',
                          headers={'Authorization': 'Bearer private-test-token'})
        handler = content.NoRedirect()
        for target in ['https://other.example.invalid/steal', 'http://tier.example.invalid/plain',
                       'https://tier.example.invalid/new']:
            with self.subTest(target=target):
                self.assertIsNone(handler.redirect_request(request, None, 302, 'Found', {}, target))


if __name__ == '__main__':
    unittest.main()
