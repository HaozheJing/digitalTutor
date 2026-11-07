"""Regression checks for conversational formatting and legacy Skill migration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from message_text import parts
from reply_style_migration import adapt


class MessageBoundaryTests(unittest.TestCase):
    def test_author_boundaries_are_preserved(self):
        self.assertEqual(parts('对，就在APP里上课\n课解锁了，打开就能读'), ['对，就在APP里上课','课解锁了，打开就能读'])
        self.assertEqual(parts('第一条\r\n\r\n第二条'), ['第一条','第二条'])
        self.assertEqual(parts('🙂'*50), ['🙂'*50])

    def test_reject_whole_submission_before_delivery(self):
        for body in ('第一条\n'+'甲'*51, '甲'*49+'，再读', '话还没说完，\n下一条', '…\n回答'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parts(body)
        for empty in ('', None, ' \n'):
            with self.assertRaisesRegex(ValueError,'非空'):
                parts(empty)


class LegacyExpressionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='reply-style-migration-')
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_legacy_reference_naming_is_removed_without_touching_business(self):
        business = '不能通过拆调用绕过联系频控；unknown不盲目重发。\n退款须真实人工回执。'
        path = self.write('remind-reading/references/examples.md',
                          '两条分别带正确称呼，不通过拆调用绕过联系频控。\n' + business)
        self.assertEqual(adapt(self.root), ['remind-reading/references/examples.md'])
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('两条分别带正确称呼', text)
        self.assertIn('后续分片不重复称呼', text)
        self.assertTrue(text.endswith(business))

    def test_family_route_migration_preserves_other_business_instructions(self):
        old = ('1. 识别家长本人、孩子借手机、家长代问；分别成人语态、儿童语态、双层讲法。'
               '称呼遵循 reply-style，每条都带孩子昵称或已确认的“昵称＋爸爸／妈妈”；'
               '身份未明时可用孩子昵称承接给孩子的话，需要家长处理时先确认关系，不用泛称、不猜身份。')
        unrelated = 'Q04退款投诉须真人受理；金额、时限不能猜。'
        path = self.write('answer-family-question/SKILL.md',
                          '# 答疑路由\n\n## 五步判断\n\n' + old + '\n' + unrelated)
        self.assertEqual(adapt(self.root), ['answer-family-question/SKILL.md'])
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('每条都带', text)
        self.assertIn('连续交流直接接话，不重复称呼', text)
        self.assertIn('普通问候、同意、致谢', text)
        self.assertTrue(text.endswith(unrelated))

    def test_migration_is_idempotent_and_unrelated_files_are_unchanged(self):
        path = self.write('answer-family-question/SKILL.md', '## 五步判断\n保留业务步骤。')
        untouched = self.write('refund/SKILL.md', '# 退款\n需真实授权和回执。\n')
        before = untouched.read_bytes()
        self.assertTrue(adapt(self.root))
        migrated = path.read_bytes()
        self.assertEqual(adapt(self.root), [])
        self.assertEqual(path.read_bytes(), migrated)
        self.assertEqual(untouched.read_bytes(), before)


class ReplySubmissionTests(unittest.TestCase):
    # Reuse the existing isolated SQL/inbound fixtures, never a real family DB.
    import test_live as fixtures
    insert = fixtures.LiveTests.insert
    create_source = fixtures.LiveTests.create_source
    set_context = fixtures.LiveTests.set_context
    tearDown = fixtures.LiveTests.tearDown

    def setUp(self):
        self.fixtures.LiveTests.setUp(self)
        import business
        business.initialize()

    def test_rejected_short_reply_has_no_delivery_and_corrected_retry_is_once(self):
        import live_runtime
        import sql_tools as s
        self.insert()
        live_runtime.ingest(self.settings)
        event = live_runtime.claim()['id']
        with patch('service_hours.is_open', return_value=True), \
             patch('test_mode.state', return_value=None), \
             patch('wechat_test.queue_text') as queue, \
             patch('wechat_test.flush') as flush:
            result = s.mock_reply(event, '可以呀\n'+'甲'*51)
            self.assertEqual(result['status'], 'needs_revision')
            self.assertIn('第2条有51字', result['reason'])
            self.assertEqual(s.read('SELECT * FROM deliveries'), [])
            queue.assert_not_called()
            flush.assert_not_called()
            good = '可以呀，拍照发我看看'
            first = s.mock_reply(event, good)
            second = s.mock_reply(event, '另一条合规回复')
            self.assertEqual(first['body'], good)
            self.assertEqual(second['body'], good)
            rows = s.read('SELECT action_key,body FROM deliveries')
            self.assertEqual(rows, [{'action_key': 'reply:' + event, 'body': good}])
            queue.assert_not_called()
            flush.assert_not_called()


if __name__ == '__main__':
    unittest.main()
