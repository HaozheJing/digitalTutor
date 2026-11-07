import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
import test_preference_memory as fixtures
import preference_memory as m
import memory_session as session
import sql_tools as s
import live_runtime as live

class MemorySessionTests(unittest.TestCase):
    setUp=fixtures.PreferenceMemoryTests.setUp
    write_config=fixtures.PreferenceMemoryTests.write_config
    message=fixtures.PreferenceMemoryTests.message
    change=fixtures.PreferenceMemoryTests.change
    commit=fixtures.PreferenceMemoryTests.commit
    start=fixtures.PreferenceMemoryTests.start
    finish=fixtures.PreferenceMemoryTests.finish

    def test_preload_preserves_sources_and_semantic_memory_fields(self):
        source=self.message('晚上先聊封面像什么，孩子愿意接着说，但今天这个效果别当成一直如此。')
        event=self.start();before=m.consolidation_context(event)
        packet=session.launch(event)
        self.assertFalse(packet['completed']);self.assertIn(s.conversation_read(source)[0]['body'],packet['prompt'])
        self.assertEqual(packet['prompt'].count(m.INSTRUCTION),1)
        self.assertIn('不要重复取首页',packet['prompt'])
        compact=session.compact_page(before)
        self.assertNotIn('rules',compact)
        for original,presented in zip(before['items'],compact['items']):
            self.assertEqual(original['body'],presented['body']);self.assertEqual(original['seq'],presented['seq'])
        self.assertEqual(compact['forgotten_by_subject'],before['forgotten_by_subject'])
        self.assertEqual(m.recall()['revision'],0)

    def test_prepare_review_includes_exact_prior_memory_for_update(self):
        import memory_maintenance as maintenance
        source=self.message("今天回复简短一点，等这周项目结束再展开。");self.commit(self.change(source))
        event=self.start();page=m.consolidation_context(event)
        with m.database() as (c,sc):old=maintenance.memories(c,sc)[0]
        seq=next(x['seq'] for x in page['items'] if x['kind']=='message')
        packet={'action':'update','memory_id':old['id'],'key':old['key'],'subject':old['subject'],
                'note':old['note'],'evidence':[seq],'basis':old['basis'],'applicability':old['applicability'],
                'scope_note':old['scope_note'],'reason':'复核更新必须完整保留仍有效旧限定'}
        prepared=m.consolidation_prepare(event_id=event,expected_revision=page['revision'],
            reviewed_through_sequence=page['through_sequence'],entries=[],changes=[packet],no_change_reason='')
        self.assertEqual(prepared['review']['previous_memories'],[old])
        self.assertIn('update整体替换',prepared['review']['instruction'])
        self.assertIn('update 会整体替换',m.INSTRUCTION)
        with m.database() as (c,sc):self.assertEqual(maintenance.memories(c,sc),[old])
        self.finish(event)

    def test_preload_cannot_skip_remaining_pages(self):
        for i in range(125):self.message('不同原话'+str(i))
        event=self.start();packet=session.launch(event);self.assertFalse(packet['completed'])
        with m.database() as (c,sc):row=m.run_row(c,sc,event);self.assertLess(row['reviewed_seq'],row['to_seq'])
        with self.assertRaisesRegex(ValueError,'pages_not_reviewed'):
            m.consolidation_prepare(event_id=event,expected_revision=0,reviewed_through_sequence=row['to_seq'],entries=[],changes=[],no_change_reason='无变化，但尚未读完不能提交')
        self.finish(event)

    def test_empty_window_commits_without_model_and_does_not_erase_memory(self):
        source=self.message();self.commit(self.change(source))
        with m.database() as (c,sc):
            m.collect(c,sc);c.execute('UPDATE memory_checkpoints SET seq=(SELECT MAX(seq) FROM memory_journal WHERE scope=?) WHERE scope=?',(sc,sc))
        before=m.recall()['items'];event=self.start();packet=session.launch(event)
        self.assertTrue(packet['completed']);self.assertFalse(packet['model_required']);self.assertNotIn('prompt',packet)
        self.assertEqual(m.recall()['items'],before)
        self.assertEqual(s.read('SELECT status FROM events WHERE id=?',(event,))[0]['status'],'done')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_empty_repair_requires_model_review(self):
        event=self.start();session.launch(event)
        repaired=__import__('memory_maintenance').repair(event,'重新检查这个历史任务，不自动省略修复')
        live.claim();self.assertFalse(session.launch(repaired['event_id'])['completed'])

    def test_tools_are_memory_only_even_when_called_directly(self):
        for name in ['reply_to_message','memory_remember','workflow_commit','preference_commit','generate_image']:
            with self.subTest(tool=name),self.assertRaisesRegex(ValueError,'memory_only_tool_not_allowed'):session.call(name,{})

    def test_completion_requires_persisted_matching_token(self):
        self.message();event=self.start();session.launch(event)
        self.assertFalse(session.completion(event,'missing')['completed'])
        self.finish(event)
        self.assertTrue(session.completion(event,self.last_prepared)['completed'])
        self.assertFalse(session.completion(event,'other-token')['completed'])
        self.assertFalse(session.completion('other-event',self.last_prepared)['completed'])

    def test_new_dialogue_blocks_preload_and_commit(self):
        self.message();event=self.start()
        with closing(sqlite3.connect(s.DB)) as c,c:live.enqueue(c,'incoming','passive',{'source_message_id':'new'})
        with self.assertRaisesRegex(ValueError,'dialogue_has_priority'):session.launch(event)
        self.assertEqual(s.read('SELECT seq FROM memory_checkpoints')[0]['seq'],0)

    def test_only_unexecuted_planning_placeholders_can_skip_model(self):
        page={'has_more':False,'repair_of':None,'items':[]}
        self.assertTrue(session.no_memory_evidence(page))
        item={'kind':'task','body':{'kind':'plan','status':'planned','result':None}}
        page['items']=[item];self.assertTrue(session.no_memory_evidence(page))
        for key,value in [('kind','interaction'),('status','done'),('result','实际服务结果')]:
            changed={**page,'items':[{'kind':'task','body':{**item['body'],key:value}}]}
            self.assertFalse(session.no_memory_evidence(changed))
        for kind in ['message','delivery','receipt']:
            self.assertFalse(session.no_memory_evidence({**page,'items':[{**item,'kind':kind}]}))
        self.assertFalse(session.no_memory_evidence({**page,'has_more':True}))
        self.assertFalse(session.no_memory_evidence({**page,'repair_of':'previous-event'}))

    def test_recall_semantics_are_not_lost_by_compaction(self):
        source=self.message('孩子今天想聊火箭，这周幼儿园正好在做')
        self.commit(self.change(source,key='reading.interest',value='本周火箭话题',subject='child',applicability='temporary'))
        event=self.start();page=m.consolidation_context(event);compact=session.compact_page(page)
        for old,new in zip(page['existing_memories'],compact['existing_memories']):
            for key in ['id','subject','key','note','basis','evidence','applicability','scope_note','expires_at','revision']:
                self.assertEqual(old[key],new[key])

if __name__=='__main__':unittest.main()
