import datetime as dt
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
import sql_tools as s
import preference_memory as m
import dialogue_context as d
import test_preference_memory as fixtures


class DialogueContextTests(unittest.TestCase):
    setUp=fixtures.PreferenceMemoryTests.setUp
    write_config=fixtures.PreferenceMemoryTests.write_config
    message=fixtures.PreferenceMemoryTests.message
    change=fixtures.PreferenceMemoryTests.change
    commit=fixtures.PreferenceMemoryTests.commit

    def checkpoint(self):
        with m.database() as (c,sc):
            m.collect(c,sc)
            c.execute('UPDATE memory_checkpoints SET seq=(SELECT MAX(seq) FROM memory_journal WHERE scope=?) WHERE scope=?',(sc,sc))

    def test_cross_midnight_and_more_than_sixty_pending_originals(self):
        for i in range(125):self.message('昨晚第%d条完整原话'%i,at='2026-10-26T23:55:00+08:00')
        self.now+=dt.timedelta(days=1)
        result=d.window('2026-10-27')
        self.assertEqual(len(result['recent_conversation']),125)
        self.assertFalse(result['conversation_window']['has_more'])
        self.assertEqual(result['recent_conversation'][0]['body'][:2],'昨晚')

    def test_today_processed_still_all_loaded(self):
        for i in range(85):self.message('今天第%d条'%i)
        self.checkpoint()
        self.assertEqual(len(d.window('2026-10-26')['recent_conversation']),85)
        self.assertEqual(len(d.window('2026-10-27')['recent_conversation']),60)

    def test_checkpoint_keeps_late_and_changed_sources_and_old_unprocessed(self):
        first=self.message('22点前');self.checkpoint()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE messages SET body='纠正后的完整原话' WHERE source_id=?",(first,))
        late=self.message('夜间之后迟到原话',at='2026-10-25T23:50:00+08:00')
        for i in range(70):self.message('当日%d'%i)
        rows=d.window('2026-10-28')['recent_conversation']
        self.assertIn(late,[r['source_id'] for r in rows]);self.assertIn('纠正后的完整原话',[r['body'] for r in rows])

    def test_pending_run_does_not_advance_window(self):
        for i in range(80):self.message('待整理%d'%i)
        self.now=self.now.replace(hour=22,minute=0);m.tick()
        self.assertEqual(len(d.window('2026-10-27')['recent_conversation']),80)

    def test_delivery_history_has_status_and_exceeds_ten(self):
        with closing(sqlite3.connect(s.DB)) as c,c:
            for i in range(25):c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(str(i),'老师的原回复%d'%i,'sent' if i<24 else 'accepted','2026-10-26T23:58:00+08:00','test'))
        rows=d.window('2026-10-27')['previous_deliveries'];self.assertEqual(len(rows),25)
        self.assertEqual(sum(r['status']=='accepted' for r in rows),1)

    def test_pages_reconstruct_every_original_and_detect_changes(self):
        for i in range(8):self.message(str(i)+'完整内容'*200)
        with patch.object(d,'PAGE_CHARS',2000):
            page=d.window('2026-10-27');rows=[]
            while True:
                rows+=page['recent_conversation'];w=page['conversation_window']
                if not w['has_more']:break
                page=d.window('2026-10-27',w['next_offset'],w['snapshot'])
            self.assertEqual(len(rows),8);self.assertEqual(len({r['source_id'] for r in rows}),8)
            self.assertTrue(all(len(r['body'])==801 for r in rows))
            self.message('后来消息')
            with self.assertRaisesRegex(ValueError,'snapshot_changed'):d.window('2026-10-27',1,w['snapshot'])

    def test_large_single_message_is_never_truncated(self):
        self.message('正文'*5000)
        with patch.object(d,'PAGE_CHARS',100):self.assertEqual(len(d.window('2026-10-27')['recent_conversation'][0]['body']),10000)

    def test_forgotten_and_hidden_sources_not_reloaded(self):
        old=self.message();self.commit(self.change(old));self.commit(self.change(self.message('忘记回复长短的偏好'),action='forget'))
        hidden=self.message('隐藏记录')
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('UPDATE messages SET visible=0 WHERE source_id=?',(hidden,))
        self.assertNotIn(old,[r['source_id'] for r in d.window('2026-10-27')['recent_conversation']])
        self.assertNotIn(hidden,[r['source_id'] for r in d.window('2026-10-27')['recent_conversation']])

    def test_wrong_family_binding_rejected(self):
        d.window('2026-10-27');self.config['family_uid']='other-family';self.write_config()
        with self.assertRaisesRegex(ValueError,'binding_changed'):d.window('2026-10-27')

    def test_disabled_overlay_is_inert(self):
        self.config['enabled']=False;self.write_config();value={'recent_conversation':['old']};before=s.DB.read_bytes()
        self.assertEqual(d.apply(value,'2026-10-27'),value);self.assertEqual(s.DB.read_bytes(),before)

    def test_mcp_set_and_legacy_writes_rejected_without_changes(self):
        import mcp_server,business
        business.initialize();source=self.message();ctx=m.context()
        for name,args in [('preference_commit',{'request_id':'x','context_token':ctx['context_token'],'changes':[self.change(source)]}),('memory_remember',{})]:
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'nightly_only'):mcp_server.business_call(name,args)
        self.assertEqual(m.recall()['revision'],0)
        schema=next(sc for n,_,sc in mcp_server.base_mcp.TOOLS if n=='preference_commit')
        self.assertEqual(schema['properties']['changes']['items']['properties']['action']['enum'],['set','forget'])

    def test_explicit_media_control_uses_current_dialogue_not_nightly_inference(self):
        import mcp_server,business
        business.initialize()
        source=self.message('现在允许给我发图片。')
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO events(id,kind,payload,status,created_at,attempts) VALUES('media-control','passive',?,'dispatched',?,1)",(json.dumps({'source_message_id':source}),s.now()))
        ctx=m.context()
        result=mcp_server.business_call('preference_commit',{'request_id':'allow-images','context_token':ctx['context_token'],'changes':[self.change(source,key='media.images',value='allowed')]})
        self.assertEqual(result['status'],'committed')
        with closing(sqlite3.connect(s.DB)) as c,c:m.guard_media(c,6)

    def test_legacy_review_receipt_is_not_blocked_as_memory_write(self):
        import mcp_server,business
        business.initialize()
        with patch.object(mcp_server,'_generation_call',return_value={'status':'reviewed'}) as call:
            self.assertEqual(mcp_server.business_call('memory_review',{'event_id':'history','note':'已接收原文，普通偏好夜间处理'})['status'],'reviewed')
            call.assert_called_once()
        self.assertEqual(m.recall()['revision'],0)

    def test_instant_prompt_has_no_nightly_extraction_policy(self):
        self.assertNotIn(m.INSTRUCTION,m.FAST_INSTRUCTION)
        self.assertIn('22点',m.FAST_INSTRUCTION)
        self.assertIn('暂停',m.FAST_INSTRUCTION)
        self.assertIn('forget',m.context()['memory_instruction'])


if __name__=='__main__':unittest.main()
