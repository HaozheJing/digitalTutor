"""Independent third-stage boundary checks against synthetic data only."""
import hashlib
import unittest

import test_stage1 as baseline
from digital_tutor.domain import BusinessError
from digital_tutor.family_memory import read_memory, save_memory
from digital_tutor.knowledge import KnowledgeCatalog, KnowledgeBinding
from digital_tutor.tools import ToolRegistry


class Stage3ReviewTests(baseline.CoreTests):
    def message_at(self, body, at):
        return self.call(self.channel, 'wechat.receive_message', {
            'external_id': self.key(), 'body': body, 'occurred_at': at})['data']

    def memory_args(self, message, **fields):
        return {'request_id': self.key(), 'expected_family_version': self.version(),
                'source_message_id': message['id'], 'source_version': message['version'],
                'source_excerpt': message['body'], 'body': message['body'],
                'kind': 'stated_preference', **fields}

    def test_delayed_old_message_cannot_overwrite_newer_memory(self):
        recent = self.message_at('现在改为周末联系', '2026-10-03T09:00:00+08:00')
        memory = save_memory(self.app, self.service, self.memory_args(recent))['data']
        delayed = self.message_at('以前工作日联系', '2026-10-02T09:00:00+08:00')
        with self.assertRaises(BusinessError):
            save_memory(self.app, self.service, self.memory_args(delayed,
                memory_id=memory['memory_id'], expected_version=memory['version']))
        self.assertEqual(read_memory(self.app, self.service, {})['memories'][0]['body'], recent['body'])

    def test_material_binding_is_selected_by_family_not_model_argument(self):
        bodies = {'viking://resources/mock/r1/day-1.md': 'one',
                  'viking://resources/mock/r1/day-2.md': 'two'}

        class Reader:
            def __init__(self):
                self.calls = []

            def read(self, uri):
                self.calls.append(uri)
                return bodies[uri]

        client = Reader()
        units = [{'unit_id': f'day-{day}', 'record_type': 'lesson',
                  'actual_read_uris': [uri], 'file_sha256': hashlib.sha256(body.encode()).hexdigest()}
                 for day, (uri, body) in enumerate(bodies.items(), 1)]
        self.app.knowledge = KnowledgeCatalog([{
            'revision': 'r1', 'target_root': 'viking://resources/mock/r1', 'units': units}], client)
        self.app.knowledge_bindings = {
            'f': KnowledgeBinding('r1', ('day-1',), simulation=True),
            'other': KnowledgeBinding('r1', ('day-2',), simulation=True)}
        tools = ToolRegistry(self.app, self.service)
        self.assertEqual(tools.call('knowledge.read', {'document_id': 'day-2'})['error']['code'],
                         'KNOWLEDGE_SCOPE_FORBIDDEN')
        self.assertEqual(tools.call('knowledge.read', {'document_id': 'day-2', 'family_id': 'other'})['error']['code'],
                         'INVALID_SCHEMA')
        self.assertEqual(client.calls, [])
        self.assertEqual(tools.call('knowledge.read', {'document_id': 'day-1'})['result']['content'], 'one')

    def test_revoked_session_cannot_read_new_context_or_materials(self):
        self.incoming(False)
        self.app.revoke_session(self.service)
        tools = ToolRegistry(self.app, self.service)
        for name, args in [('context.read', {}), ('memory.read', {}), ('knowledge.list', {}),
                           ('skills.list', {}), ('learning.schema', {}), ('wechat.read_messages', {'mode': 'all'})]:
            with self.subTest(tool=name):
                self.assertEqual(tools.call(name, args)['error']['code'], 'RUN_REVOKED')


for _name in baseline.CoreTests.__dict__:
    if _name.startswith('test_'):
        setattr(Stage3ReviewTests, _name, None)
