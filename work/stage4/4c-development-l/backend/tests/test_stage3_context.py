"""Memory provenance and responsibility-context regression tests, fake families only."""
import json
import unittest

import test_stage1 as baseline
from digital_tutor.context import build_context, read_context
from digital_tutor.domain import BusinessError
from digital_tutor.family_memory import delete_memory, read_memory, save_memory


class ContextTests(baseline.CoreTests):
    def memory_args(self, source, **overrides):
        return {'request_id': self.key(), 'expected_family_version': self.version(),
                'source_message_id': source['id'], 'source_version': source['version'],
                'source_excerpt': '先别催', 'body': '此轮希望暂停催课',
                'kind': 'stated_preference', **overrides}

    def test_memory_cross_family_and_source_versions(self):
        source = self.incoming(False)
        other = self.app.open_session('other', 'service')
        with self.assertRaises(BusinessError) as caught:
            save_memory(self.app, other, self.memory_args(source, expected_family_version=1))
        self.assertEqual(caught.exception.code, 'RECORD_NOT_FOUND')
        with self.assertRaises(BusinessError) as caught:
            save_memory(self.app, self.service, self.memory_args(source, source_excerpt='虚构原话'))
        self.assertEqual(caught.exception.code, 'EVIDENCE_MISSING')
        saved = save_memory(self.app, self.service, self.memory_args(source))['data']
        self.assertNotIn('body', saved)
        self.assertEqual(read_memory(self.app, other, {})['memories'], [])
        self.call(self.service, 'message.interpret', {
            'request_id': self.key(), 'message_id': source['id'], 'expected_version': source['version'],
            'intents': [{'intent_id': 'i', 'text': '暂停'}]})
        with self.assertRaises(BusinessError) as caught:
            save_memory(self.app, self.service, self.memory_args(source))
        self.assertEqual(caught.exception.code, 'EVIDENCE_SUPERSEDED')

    def test_correction_uses_new_source_and_delete_redacts_history(self):
        source = self.incoming(False)
        original = self.memory_args(source, body='PRIVATE_MEMORY_SENTINEL')
        first = save_memory(self.app, self.service, original)['data']
        revised_source = self.incoming(False)
        revised = save_memory(self.app, self.service, self.memory_args(
            revised_source, memory_id=first['memory_id'], expected_version=first['version'],
            body='更正后的偏好'))['data']
        memories = read_memory(self.app, self.service, {})['memories']
        self.assertEqual([m['body'] for m in memories], ['更正后的偏好'])
        request = self.incoming(False)
        result = delete_memory(self.app, self.service, {
            'request_id': self.key(), 'expected_family_version': self.version(),
            'memory_id': revised['memory_id'], 'expected_version': revised['version'],
            'source_message_id': request['id'], 'source_version': request['version']})
        self.assertIn('database_backups', result['data']['retained'])
        self.assertEqual(read_memory(self.app, self.service, {})['memories'], [])
        rows = [r[0] for r in self.app.store.connection.execute("SELECT data FROM history WHERE kind='memory'")]
        self.assertNotIn('PRIVATE_MEMORY_SENTINEL', json.dumps(rows))
        self.assertNotIn('更正后的偏好', json.dumps(rows, ensure_ascii=False))
        self.assertNotIn('PRIVATE_MEMORY_SENTINEL', json.dumps(save_memory(self.app, self.service, original)))

    def test_expired_memory_omitted_and_controls_authoritative(self):
        source = self.incoming(False)
        pause = self.pause(source)
        save_memory(self.app, self.service, self.memory_args(
            source, body='旧解释不应该覆盖暂停', valid_until='2026-10-02T23:00:00+08:00'))
        context = build_context(self.app, self.service, {'kind': 'service', 'message_id': source['id']})
        self.assertEqual(context['memory']['memories'], [])
        self.assertEqual(context['controls'][0]['id'], pause['id'])
        self.assertEqual(context['learning']['course_binding'], 'unknown')

    def test_daily_covers_all_open_responsibilities_not_only_plan(self):
        ids = self.create([self.goal('a'), self.goal('b')])
        with self.app.store.transaction():
            event = self.app.event('f', ids['b'])
            event.update(status='waiting', waiting={'reason': '等待家庭', 'resume_condition': '家庭回复', 'review_at': None})
            self.app.store.put('event', event['id'], 'f', event, event['version'])
            plan = self.app.store.list('plan', 'f')[0]
            plan['items'] = [plan['items'][0]]
            self.app.store.put('plan', plan['id'], 'f', plan, plan['version'])
        daily = self.app.open_session('f', 'planner_daily')
        context = build_context(self.app, daily, {'kind': 'daily'})
        self.assertEqual({e['id'] for e in context['events']}, set(ids.values()))
        self.assertEqual(context['coverage']['responsibilities'], 'complete')
        self.assertEqual(context['plans'][0]['items'][0]['event_id'], ids['a'])

    def test_progress_is_bound_to_report_and_original_items(self):
        ids = self.create([self.goal('a'), self.goal('b')])
        message = self.incoming()
        report = self.report([ids['a']], [self.ref('message', message)])
        self.report([ids['b']], [self.ref('message', message)])
        token = self.app.open_session('f', 'planner_progress', report, 1)
        context = build_context(self.app, token, {'kind': 'progress'})
        self.assertEqual([e['id'] for e in context['events']], [ids['a']])
        self.assertEqual([r['report_id'] for r in context['reports']], [report])
        self.assertEqual(context['plans'][0]['items'], [{'event_id': ids['a'], 'rank': 1}])
        with self.assertRaises(BusinessError):
            read_context(self.app, token, {'event_id': ids['b']})

    def test_context_never_includes_other_family_or_silently_claims_full_history(self):
        foreign = self.app.open_session('other', 'channel')
        other_message = self.call(foreign, 'wechat.receive_message', {
            'external_id': 'other', 'body': 'OTHER_FAMILY_SENTINEL', 'occurred_at': self.app.clock.iso()})['data']
        messages = [self.incoming(False) for _ in range(8)]
        result = build_context(self.app, self.service, {'message_id': messages[0]['id']})
        self.assertNotIn('OTHER_FAMILY_SENTINEL', json.dumps(result))
        self.assertEqual(len(result['messages']), 7)
        self.assertTrue(result['coverage']['older_messages_omitted'])
        self.assertEqual(len(result['coverage']['unanswered_message_refs']), 8)
        with self.assertRaises(BusinessError):
            read_context(self.app, self.service, {'message_id': other_message['id']})
        with self.assertRaises(BusinessError):
            build_context(self.app, self.service, material_loader=lambda envelope: {'family_id': 'other'})


# Reuse fixture helpers without discovering inherited stage-one tests twice.
for _name in tuple(dir(baseline.CoreTests)):
    if _name.startswith('test_') and _name not in ContextTests.__dict__:
        setattr(ContextTests, _name, None)


if __name__ == '__main__':
    unittest.main()
