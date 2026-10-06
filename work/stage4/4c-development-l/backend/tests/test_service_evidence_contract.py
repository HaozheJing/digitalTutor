"""Service source/scope/read contracts on real host paths; no model or network.

Raw rows below are isolated synthetic business inputs, not model answer scripts.
Run after the context/read/send integration is installed, on the validation host.
"""
from pathlib import Path
from copy import deepcopy
import tempfile
from types import SimpleNamespace
import unittest

from digital_tutor.application import Application, Clock
from digital_tutor.context import build_context
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue
from digital_tutor.learning import learning_source_contract, query_learning
from digital_tutor.mock_channel import channel_for
from digital_tutor.materials import dispatch_material
from digital_tutor.outbox import dispatch
from digital_tutor.runtime_outcome import record_outcome
from digital_tutor.service_context import (continuation_context, control_scope_snapshot,
    message_read_state, message_source_digest, service_evidence_contract)
from digital_tutor.storage import encode
from digital_tutor.wechat import read_messages, prepare_message, send_message


class ServiceEvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.tmp.name) / 'service.sqlite',
            Clock('2026-10-04T10:00:00+08:00'), mode='passive', strict_runtime=True)
        self.app.create_family('f', 'synthetic-f', 'mock:f')
        self.app.create_family('other', 'synthetic-other', 'mock:other')
        self.admin = self.app.open_session('f', 'admin')
        self.queue = JobQueue(self.app)
        self.sequence = 0

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def key(self):
        self.sequence += 1
        return 'service-scope-' + str(self.sequence)

    def incoming(self, body='这次记录里的学习状态是什么？'):
        return self.app.ingest_message(self.admin, {'external_id': self.key(),
            'body': body, 'occurred_at': self.app.clock.iso()})['data']

    def claim(self):
        claim = self.queue.claim(self.key(), lease_seconds=60)
        self.assertIsNotNone(claim)
        token = self.queue.session(claim)
        return claim, token

    def interpret(self, token, message):
        current = self.app.store.get('message', message['id'], 'f')
        return self.app.interpret(token, {'request_id': self.key(), 'message_id': current['id'],
            'expected_version': current['version'],
            'intents': [{'intent_id': 'question', 'text': current['body'], 'requires_reply': True}]})['data']

    def send_args(self, *messages):
        return {'request_id': self.key(), 'expected_family_version': self.app.store.family('f')['version'],
                'action_kind': 'reply', 'body': '一条模拟回复。',
                'reply_to': [{'message_id': message['id'], 'intent_id': 'question'} for message in messages]}

    def assert_unread(self, token, args):
        with self.assertRaises(BusinessError) as caught:
            send_message(self.app, token, args)
        self.assertEqual(caught.exception.code, 'MESSAGE_READ_REQUIRED')
        self.assertEqual(channel_for(self.app).count(), 0)

    def add_event(self, eid, *, delivery=False):
        completion = ({'kind': 'delivery', 'required_part_ids': ['text', 'audio'], 'required_stage': 'delivered', 'lesson_refs': []}
                      if delivery else {'kind': 'goal', 'lesson_refs': ['synthetic-lesson'], 'required_part_ids': [], 'required_stage': 'sent'})
        with self.app.store.transaction():
            return self.app.store.put('event', eid, 'f', {'goal_text': '独立原事项 ' + eid,
                'status': 'pending', 'completion': completion,
                'remaining_work': list(completion['required_part_ids'] if delivery else completion['lesson_refs']),
                'requirement_revision': 1, 'due_at': None, 'next_action': None})

    def test_assembly_and_state_never_authorize_first_send(self):
        message = self.incoming()
        _, token = self.claim()
        self.interpret(token, message)
        context = build_context(self.app, token)
        self.app.state(token)
        self.assertFalse(context['service_evidence_contract']['message_reads']['required_reply_inputs'][0]['read_this_run'])
        self.assertEqual(self.app.store.list('runtime_message_read', 'f'), [])
        self.assert_unread(token, self.send_args(message))

    def test_actual_read_survives_interpretation_and_reply_receipt_versions(self):
        message = self.incoming()
        _, token = self.claim()
        read_messages(self.app, token, {'mode': 'all'})
        interpreted = self.interpret(token, message)
        self.assertGreater(interpreted['version'], message['version'])
        self.assertEqual(message_source_digest(interpreted), message_source_digest(message))
        args = self.send_args(message)
        result = send_message(self.app, token, args)
        self.assertEqual(result['data']['stage'], 'sent')
        # Receipt replay remains before new-send checks, even without read history.
        with self.app.store.transaction() as db:
            db.execute("DELETE FROM records WHERE kind='runtime_message_read' AND family_id=?", ('f',))
        self.assertEqual(send_message(self.app, token, args)['outcome'], 'duplicate')
        self.assertEqual(channel_for(self.app).count(), 1)

    def test_current_source_correction_invalidates_prior_read(self):
        message = self.incoming()
        _, token = self.claim()
        read_messages(self.app, token, {'mode': 'all'})
        message = self.interpret(token, message)
        with self.app.store.transaction():
            self.app.store.put('message', message['id'], 'f', {**message, 'body': '原来源内容更正。'}, message['version'])
        self.assert_unread(token, self.send_args(message))

    def test_latest_one_cannot_authorize_merged_reply_to_older_input(self):
        first = self.incoming('第一个问题。')
        second = self.incoming('另一个问题。')
        _, token = self.claim()
        read_messages(self.app, token, {'mode': 'latest', 'direction': 'inbound', 'limit': 1})
        self.interpret(token, first)
        self.interpret(token, second)
        self.assert_unread(token, self.send_args(first, second))
        read_messages(self.app, token, {'mode': 'all', 'direction': 'inbound'})
        self.assertEqual(send_message(self.app, token, self.send_args(first, second))['data']['stage'], 'sent')

    def test_old_run_or_admin_read_is_not_this_run_evidence(self):
        message = self.incoming()
        read_messages(self.app, self.admin, {'mode': 'all'})
        first, first_token = self.claim()
        self.interpret(first_token, message)
        self.assert_unread(first_token, self.send_args(message))
        read_messages(self.app, first_token, {'mode': 'all'})
        self.queue.finish(first, 'waiting')
        self.app.revoke_session(first_token)
        self.incoming('补充一条相关说明。')
        second, second_token = self.claim()
        self.assertNotEqual(first['job']['run_id'], second['job']['run_id'])
        self.assert_unread(second_token, self.send_args(message))
        _, binding = self.app.auth(second_token, {'service'})
        cursors = message_read_state(self.app, 'f', binding)['trusted_contiguous_cursors']
        self.assertTrue(any(message['id'] in c['required_before_or_at_cursor'] for c in cursors))

    def test_read_audit_does_not_change_business_versions_or_self_wake(self):
        message = self.incoming()
        claim, token = self.claim()
        before = self.app.store.family('f')['version']
        read_messages(self.app, token, {'mode': 'all'})
        observed = self.app.store.list('runtime_message_read', 'f')
        history = self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0]
        read_messages(self.app, token, {'mode': 'all'})
        self.assertEqual(self.app.store.list('runtime_message_read', 'f'), observed)
        self.assertEqual(self.app.store.family('f')['version'], before)
        self.assertEqual(self.app.store.get('message', message['id'], 'f'), message)
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0], history)
        self.queue.finish(claim, 'waiting')
        for _ in range(20):
            self.assertIsNone(self.queue.claim('no-change'))

    def test_queued_unread_operation_cannot_begin_dispatch(self):
        message = self.incoming()
        _, token = self.claim()
        read_messages(self.app, token, {'mode': 'all'})
        self.interpret(token, message)
        operation = prepare_message(self.app, token, self.send_args(message))['data']
        with self.app.store.transaction() as db:
            db.execute("DELETE FROM records WHERE kind='runtime_message_read' AND family_id=?", ('f',))
        result = dispatch(self.app, 'f', operation['id'])
        self.assertEqual(result['stage'], 'cancelled')
        self.assertEqual(result['cancel_reason'], 'MESSAGE_READ_REQUIRED')
        self.assertEqual(result['dispatch_count'], 0)
        self.assertEqual(channel_for(self.app).count(), 0)

    def test_unconfirmed_learning_snapshot_does_not_inherit_old_event_status(self):
        event = self.add_event('old-day-one')
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO imports VALUES(?,?)', ('batch', encode({'course_binding': None})))
            db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('batch', 'synthetic-f', 1, 'attended', 'completed', 'submitted', 'unknown', 'unknown', None, None, 'synthetic-source'))
        contract = service_evidence_contract(self.app, 'f', {})
        self.assertEqual(contract['learning_snapshot_contract']['confirmed_course_bindings'], [])
        self.assertEqual(contract['responsibility_contracts'][0]['status'], 'pending')
        self.assertFalse(contract['responsibility_contracts'][0]['status_is_learning_snapshot'])
        result = query_learning(self.app, self.admin, {'sql': 'SELECT batch_id,day,completion_status,homework_status FROM learning_days WHERE day=:day', 'parameters': {'day': 1}})
        self.assertEqual(result['rows'][0]['completion_status'], 'completed')
        self.assertEqual(result['rows'][0]['homework_status'], 'submitted')
        self.assertEqual(self.app.event('f', event['id'])['status'], 'pending')

    def test_partial_host_binding_retains_unknown_fields_without_inferred_event_link(self):
        self.add_event('same-day-label')
        with self.app.store.transaction():
            self.app.store.put('course_binding', 'binding', 'f', {'batch_id': 'batch',
                'source_kind': 'trusted_host_confirmation', 'coverage_through': self.app.clock.iso()})
        contract = learning_source_contract(self.app, 'f')
        self.assertIn('course_id', contract['confirmed_course_bindings'][0]['unknown_fields'])
        self.assertIn('lesson_map', contract['confirmed_course_bindings'][0]['unknown_fields'])
        self.assertEqual(contract['event_correspondence'], 'not_established_by_day_number_or_event_status')

    def test_original_goal_and_delivery_scope_not_replaced_by_current_activity(self):
        goal = self.add_event('goal')
        delivery = self.add_event('delivery', delivery=True)
        snapshot = service_evidence_contract(self.app, 'f', {})
        indexed = {row['event_id']: row for row in snapshot['responsibility_contracts']}
        for event in (goal, delivery):
            self.assertEqual(indexed[event['id']]['completion'], event['completion'])
            self.assertEqual(indexed[event['id']]['remaining_work'], event['remaining_work'])
        self.assertFalse(snapshot['current_request']['activity_completion_is_event_completion'])
        indexed['delivery']['completion']['required_part_ids'].clear()
        self.assertEqual(self.app.event('f', 'delivery')['completion']['required_part_ids'], ['text', 'audio'])

    def test_control_scope_comes_from_record_not_topic_or_resume_prose(self):
        self.add_event('topic-a')
        self.add_event('topic-b')
        message = self.incoming('先不要催课。')
        _, token = self.claim()
        control = self.app.apply_control(token, {'request_id': self.key(), 'event_id': None,
            'action_kinds': ['remind_makeup'], 'reason': '本轮暂停',
            'source_message_id': message['id'], 'source_version': message['version']})['data']
        outcome = record_outcome(self.app, token, {'request_id': self.key(), 'outcome': 'waiting',
            'refs': [{'kind': 'message', 'id': message['id'], 'version': message['version']},
                     {'kind': 'control', 'id': control['id'], 'version': control['version']}],
            'reason': '待新输入', 'remaining_work': ['原咨询'],
            'resume_condition': '模型旧错误说明：topic-b不受暂停影响'})
        scope = outcome['effective_control_scopes']['controls'][0]
        self.assertEqual(scope['scope_kind'], 'family')
        self.assertEqual(scope['applies_to_current_event_ids'], ['topic-a', 'topic-b'])
        self.assertEqual(scope['action_kinds'], ['remind_makeup'])
        self.assertTrue(scope['applies_to_future_events'])
        self.assertIn('not_control_or_send_permission', outcome['resume_condition_authority'])
        self.add_event('topic-c')
        projected = continuation_context(self.app, 'f', [outcome])[0]
        self.assertEqual(projected['resume_condition'], outcome['resume_condition'])  # Do not rewrite history.
        self.assertIn('topic-c', projected['current_control_scopes']['controls'][0]['applies_to_current_event_ids'])
        self.assertNotIn('topic-c', outcome['effective_control_scopes']['controls'][0]['applies_to_current_event_ids'])

    def test_event_scoped_or_released_controls_do_not_become_family_active(self):
        self.add_event('topic-a')
        self.add_event('topic-b')
        with self.app.store.transaction():
            self.app.store.put('control', 'narrow', 'f', {'status': 'active', 'event_id': 'topic-a', 'action_kinds': ['remind_makeup']})
            self.app.store.put('control', 'old', 'f', {'status': 'released', 'event_id': None, 'action_kinds': ['reply']})
        controls = {row['control_id']: row for row in control_scope_snapshot(self.app, 'f')['controls']}
        self.assertEqual(controls['narrow']['applies_to_current_event_ids'], ['topic-a'])
        self.assertFalse(controls['narrow']['applies_to_future_events'])
        self.assertFalse(controls['old']['effective'])

    def test_method_scope_guidance_keeps_source_body_hashes_and_guides_unchanged(self):
        source = {'content': 'Synthetic source with explicit required conditions.',
                  'content_sha256': 'fixed-source-hash', 'complete_body': True,
                  'required_guides': [{'guide_id': 'synthetic', 'content': 'Exact guide bytes.', 'sha256': 'fixed-guide-hash'}]}
        before = deepcopy(source)
        self.app.knowledge = SimpleNamespace(dispatch=lambda *args: source)
        self.app.knowledge_bindings = {}
        returned = dispatch_material(self.app, self.admin, 'knowledge.read', {'document_id': 'synthetic'})
        self.assertEqual(source, before)
        for key in before:
            self.assertEqual(returned[key], before[key])
        self.assertFalse(returned['method_scope_contract']['family_preference_changes_requirements'])
        self.assertFalse(returned['method_scope_contract']['local_activity_proves_method_or_course_completion'])


if __name__ == '__main__':
    unittest.main()
