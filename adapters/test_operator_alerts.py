"""Human handoff safety, tested with an isolated DB and mocked transport only."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import agent_plan as ap
import operator_alerts as alerts
import sql_tools as s
import test_mode as tm
import wechat_test as w


class OperatorAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='operator-alert-test-', dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'live.sqlite'
        for target, name, value in [(s, 'DB', self.db), (w, 'config', lambda: {'enabled': True}),
                                    (w, 'provider_config', lambda: ({}, {'imContactId': 'bound-only'}))]:
            stub = patch.object(target, name, value)
            stub.start()
            self.addCleanup(stub.stop)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.executescript('''
              CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE events(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,status TEXT,created_at TEXT,
                attempts INTEGER,dispatched_at TEXT,completed_at TEXT,retry_after TEXT);
              CREATE TABLE messages(source_id TEXT PRIMARY KEY,speaker TEXT,body TEXT,created_at TEXT,
                source TEXT,sha256 TEXT,visible INTEGER);
              CREATE TABLE deliveries(action_key TEXT PRIMARY KEY,body TEXT,status TEXT,created_at TEXT,source TEXT);
              CREATE TABLE contact_constraints(day TEXT PRIMARY KEY,source_message_id TEXT,quote TEXT,created_at TEXT);
            ''')
            c.execute('INSERT INTO metadata VALUES(?,?)', ('test_mode', s.encoded({
                'run_id': 'run-current', 'uid': '00001', 'as_of': '2025-11-05T19:00:00+08:00'})))
        ap.initialize()
        w.initialize()
        self.event()

    def event(self, ident='event-current', source='message-current', status='dispatched'):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',
                      (source, 'user', '这件事需要老师确认后再回复我。', s.now(), 'bound_wechat_test', 'hash', 1))
            c.execute('INSERT INTO events(id,kind,payload,status,created_at,attempts) VALUES(?,?,?,?,?,?)',
                      (ident, 'passive', s.encoded({'source_message_id': source}), status, s.now(), 1))

    def request(self):
        return alerts.request_alert('需要人工核对资料', '模型拟回复，等待老师检查。', event_id='event-current')['alert']

    def send(self, alert, **updates):
        args = {'expected_run_id': 'run-current', 'alert_id': alert['id'], 'body': '已人工核对，稍后把准确资料回复给你。',
                'revision': alert['revision'], 'request_id': 'request-original'}
        args.update(updates)
        return alerts.send_reply(**args)

    def status(self, table, ident, key='id'):
        return s.read('SELECT status FROM ' + table + ' WHERE ' + key + '=?', (ident,))[0]['status']

    def receipt(self, ident, success=True):
        payload = {'externalRequestId': ident, 'requestId': 'provider-request',
                   'sendCode': 0 if success else 1, 'errorCode': 0 if success else 42}
        if success:
            payload['messageId'] = 'confirmed-message'
        with closing(sqlite3.connect(self.db)) as c, c:
            w.observe(c, payload)

    def task(self, kind='s01'):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("UPDATE events SET status='done'")
            c.execute('INSERT INTO service_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('service-current', 'run-current', '2025-11-05', kind, '当前服务任务', 'remind-reading',
                       '2025-11-05T07:00:00+08:00', '2025-11-05T23:59:00+08:00', None, '业务原因', '',
                       'running', 1, 0, s.now(), s.now(), None, None, None))
            for key in ('active_service_task', 'native_service_content_task'):
                c.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (key, s.encoded('service-current')))

    def test_projection_before_migration_is_read_only(self):
        before = self.db.read_bytes()
        self.assertEqual(alerts.list_alerts()['items'], [])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse(s.read("SELECT 1 FROM sqlite_master WHERE name='operator_alerts'"))

    def test_request_is_durable_idempotent_and_stops_source_event(self):
        alert = self.request()
        self.assertEqual(alert['source_message']['body'], '这件事需要老师确认后再回复我。')
        self.assertEqual(self.status('events', 'event-current'), 'needs_attention')
        self.assertEqual(self.request()['id'], alert['id'])
        self.assertEqual(len(alerts.list_alerts()['items']), 1)
        self.assertTrue(alerts.list_alerts()['model_outbound_paused'])
        self.assertFalse(s.read('SELECT * FROM deliveries'))
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            alerts.request_alert('改写原因', event_id='event-current')

    def test_only_current_dispatched_passive_sources_can_be_transferred(self):
        with self.assertRaises(ValueError):
            alerts.request_alert('原因', event_id='unknown')
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("UPDATE events SET status='done'")
        with self.assertRaises(ValueError):
            self.request()
        with self.assertRaises(ValueError):
            alerts.request_alert('原因', event_id='event-current', task_id='service-current')
        with self.assertRaises(ValueError):
            alerts.request_alert('原因')

    def test_task_handoff_blocks_native_tail_without_active_metadata(self):
        self.task()
        result = alerts.request_alert('交给人工确认', '拟回复', task_id='service-current')
        self.assertEqual(self.status('service_tasks', 'service-current'), 'blocked')
        self.assertIsNone(ap.active())
        for name in alerts.OUTBOUND_TOOLS:
            with self.subTest(tool=name), self.assertRaisesRegex(ValueError, '人工'):
                alerts.guard_model_delivery(name, {})
        alerts.guard_model_delivery('learning_query', {})
        alerts.dismiss('run-current', result['alert']['id'], 1, '此事项无须发送')
        # The same native turn still owns its tail, even after a dismissal.
        with self.assertRaises(ValueError):
            alerts.guard_model_delivery('deliver_message', {})

    def test_mcp_exposes_only_request_and_guards_every_outgoing_entry_first(self):
        import mcp_server as mcp
        import business
        # Full discovery may have imported MCP against a previous temporary DB.
        # Initialize this test's DB explicitly instead of relying on import time.
        business.initialize()
        ap.initialize()
        definitions = {name: schema for name, _, schema in mcp.base_mcp.TOOLS}
        definition = definitions['request_human_confirmation']
        self.assertFalse(definition['additionalProperties'])
        self.assertNotIn('recipient', definition['properties'])
        self.assertFalse(set(definitions) & {'operator_reply', 'send_reply', 'dismiss_alert'})
        event_ref = business.token('event-current')
        result = mcp.business_call('request_human_confirmation', {
            'event_id': event_ref, 'reason': '需要业务老师判断', 'proposed_reply': '待人工检查'})
        self.assertEqual(result['status'], 'human_confirmation_required')
        for name in alerts.OUTBOUND_TOOLS:
            with self.subTest(tool=name), self.assertRaisesRegex(ValueError, '人工'):
                # Includes festival's early dispatch branch and legacy aliases.
                mcp.business_call(name, {'event_id': event_ref})
        self.assertFalse(s.read('SELECT * FROM deliveries'))

    def test_edited_reply_uses_canonical_delivery_and_final_callback(self):
        alert = self.request()
        with patch.object(w, 'http_send', return_value={'errcode': 0, 'requestId': 'provider-request'}) as http:
            result = self.send(alert)
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(self.status('events', 'event-current'), 'needs_attention')
            self.assertFalse(result['alert']['resolved'])
            self.assertEqual(http.call_count, 1)
            self.assertEqual(http.call_args.args[1], {'imContactId': 'bound-only'})
            self.assertEqual(http.call_args.args[3], '已人工核对，稍后把准确资料回复给你。')
            duplicate = self.send(alert)
            self.assertTrue(duplicate['duplicate'])
            self.assertEqual(http.call_count, 1)
        delivery = s.read('SELECT * FROM deliveries')[0]
        self.assertEqual(delivery['action_key'], 'reply:event-current')
        operation = s.read('SELECT id FROM test_outbox')[0]['id']
        self.receipt(operation)
        # GET reflects actual receipts but remains read-only.
        self.assertEqual(alerts.list_alerts()['items'][0]['status'], 'sent')
        alerts.reconcile()
        self.assertEqual(self.status('events', 'event-current'), 'done')
        self.assertEqual(alerts.list_alerts()['pending_count'], 0)
        # Transferred work must never be reclaimed by its original model turn.
        with self.assertRaises(ValueError):
            alerts.guard_model_delivery('reply_to_message', {'event_id': 'event-current'})

    def test_unknown_transport_never_retries_or_accepts_changed_payload(self):
        alert = self.request()
        with patch.object(w, 'http_send', side_effect=TimeoutError()) as http:
            self.assertEqual(self.send(alert)['status'], 'unknown')
            self.assertEqual(self.send(alert)['status'], 'unknown')
            self.assertEqual(http.call_count, 1)
            for changes in ({'body': '修改后的正文'}, {'request_id': 'different-request'}):
                with self.assertRaisesRegex(ValueError, '正文已冻结'):
                    self.send(alert, **changes)
            with self.assertRaisesRegex(ValueError, '已提交'):
                alerts.dismiss('run-current', alert['id'], 2, '不能假装没发过')
        operation = s.read('SELECT id FROM test_outbox')[0]['id']
        self.receipt(operation)
        alerts.reconcile()
        self.assertEqual(self.status('events', 'event-current'), 'done')

    def test_failed_receipt_keeps_handoff_open_and_never_resends(self):
        alert = self.request()
        with patch.object(w, 'http_send', return_value={'errcode': 0, 'requestId': 'provider-request'}) as http:
            self.send(alert)
            self.receipt(s.read('SELECT id FROM test_outbox')[0]['id'], False)
            alerts.reconcile()
            self.assertEqual(alerts.list_alerts()['items'][0]['status'], 'failed')
            self.assertEqual(self.status('events', 'event-current'), 'needs_attention')
            self.assertEqual(self.send(alert)['status'], 'failed')
            self.assertEqual(http.call_count, 1)

    def test_cross_run_stale_version_and_history_cannot_send(self):
        alert = self.request()
        with patch.object(w, 'http_send') as http:
            for changes in ({'expected_run_id': 'old-run'}, {'revision': 99}, {'alert_id': 'alert-unknown'}):
                with self.assertRaises(ValueError):
                    self.send(alert, **changes)
            with closing(sqlite3.connect(self.db)) as c, c:
                state = tm.state()
                state['run_id'] = 'new-run'
                c.execute("UPDATE metadata SET value=? WHERE key='test_mode'", (s.encoded(state),))
            self.assertFalse(alerts.list_alerts()['items'])
            with self.assertRaises(ValueError):
                self.send(alert, expected_run_id='new-run')
            http.assert_not_called()

    def test_archive_database_cannot_mutate_even_with_its_matching_run_id(self):
        alert = self.request()
        archive = Path(self.temp.name) / 'test-archives' / 'old.sqlite'
        archive.parent.mkdir()
        with closing(sqlite3.connect(self.db)) as source, closing(sqlite3.connect(archive)) as target:
            source.backup(target)
        with patch.object(s, 'DB', archive), patch.object(w, 'http_send') as http:
            self.assertEqual(len(alerts.list_alerts()['items']), 1)
            before = archive.read_bytes()
            with self.assertRaisesRegex(ValueError, '历史轮次'):
                self.send(alert)
            with self.assertRaisesRegex(ValueError, '历史轮次'):
                alerts.dismiss('run-current', alert['id'], 1, '拒绝历史写入')
            with self.assertRaisesRegex(ValueError, '历史轮次'):
                self.request()
            self.assertEqual(before, archive.read_bytes())
            http.assert_not_called()

    def test_length_control_characters_and_recipient_parameters_are_rejected(self):
        alert = self.request()
        with patch.object(w, 'http_send') as http:
            for body in ('', 'a' * 151, 'a' * 301, 'text\x00unsafe'):
                with self.subTest(body=repr(body)), self.assertRaises(ValueError):
                    self.send(alert, body=body)
            with self.assertRaises(TypeError):
                self.send(alert, recipient='arbitrary-contact')
            http.assert_not_called()
        with patch.object(w, 'http_send', return_value={'errcode': 0, 'requestId': 'provider-request'}) as http:
            self.assertEqual(self.send(alert, body='甲' * 150 + '\n\n' + '乙' * 150)['status'], 'accepted')
            self.assertEqual(http.call_count, 2)

    def test_other_delivery_or_hidden_original_cannot_be_overwritten(self):
        alert = self.request()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)', ('reply:event-current', '已提交', 'unknown', s.now(), 'test_wechat'))
        with patch.object(w, 'http_send') as http:
            with self.assertRaisesRegex(ValueError, '其他交付申请'):
                self.send(alert)
            http.assert_not_called()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('DELETE FROM deliveries')
            c.execute('UPDATE messages SET visible=0')
        with self.assertRaisesRegex(ValueError, '上下文已变化'):
            self.send(alert)

    def test_submission_crash_is_durable_without_second_submission(self):
        alert = self.request()
        with patch.object(w, 'flush', side_effect=RuntimeError('process interrupted')):
            with self.assertRaises(RuntimeError):
                self.send(alert)
        with patch.object(w, 'flush') as flush:
            duplicate = self.send(alert)
            self.assertTrue(duplicate['duplicate'])
            self.assertEqual(duplicate['status'], 'queued')
            flush.assert_not_called()
        self.assertEqual(len(s.read('SELECT * FROM deliveries')), 1)

    def test_dismiss_is_versioned_never_sends_and_never_retries_event(self):
        alert = self.request()
        with self.assertRaises(ValueError):
            alerts.dismiss('run-current', alert['id'], 2, '版本不对')
        result = alerts.dismiss('run-current', alert['id'], 1, '资料核对后决定暂不回复')
        self.assertEqual(result['status'], 'dismissed')
        self.assertFalse(s.read('SELECT * FROM deliveries'))
        self.assertEqual(self.status('events', 'event-current'), 'done')
        with self.assertRaises(ValueError):
            self.send(alert)

    def test_service_reply_resolves_only_after_final_receipt_and_respects_pause(self):
        self.task()
        alert = alerts.request_alert('交给人工确认', '拟回复', task_id='service-current')['alert']
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)', ('2025-11-05', 'source', '别联系', s.now()))
        with self.assertRaisesRegex(ValueError, '暂停'):
            self.send(alert)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('DELETE FROM contact_constraints')
        with patch.object(w, 'http_send', return_value={'errcode': 0, 'requestId': 'provider-request'}):
            self.assertEqual(self.send(alert)['status'], 'accepted')
        alerts.reconcile()
        self.assertEqual(self.status('service_tasks', 'service-current'), 'blocked')
        self.receipt(s.read('SELECT id FROM test_outbox')[0]['id'])
        alerts.reconcile()
        self.assertEqual(self.status('service_tasks', 'service-current'), 'done')
        self.assertEqual(len(s.read('SELECT * FROM service_links')), 1)

    def test_internal_task_alert_is_visible_but_cannot_create_outreach(self):
        self.task('completion_plan')
        alert = alerts.request_alert('规划资料有冲突', task_id='service-current')['alert']
        self.assertFalse(alert['can_send'])
        self.assertTrue(alert['can_dismiss'])
        with self.assertRaisesRegex(ValueError, '不能转为对客消息'):
            self.send(alert)
        alerts.dismiss('run-current', alert['id'], 1, '已核对资料，保留任务阻断供后续修复')
        self.assertEqual(self.status('service_tasks', 'service-current'), 'blocked')

    def test_fresh_family_message_requires_review_before_manual_send(self):
        alert = self.request()
        self.event('event-new', 'message-new', 'pending')
        with self.assertRaisesRegex(ValueError, '新的家庭消息'):
            self.send(alert)


if __name__ == '__main__':
    unittest.main()
