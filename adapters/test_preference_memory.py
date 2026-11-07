import datetime as dt
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import sql_tools as s
import base_mcp  # Capture its read-only source root before per-test fixture patching.
import preference_memory as m
import live_runtime as live


class PreferenceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'config').mkdir()
        self.addCleanup(patch.stopall)
        patch.object(s,'R',self.root).start();patch.object(s,'DB',self.root/'live.sqlite').start()
        self.now=dt.datetime.fromisoformat('2026-10-26T21:59:00+08:00')
        patch.object(m,'clock',side_effect=lambda:self.now).start()
        patch.object(s,'family_profile',return_value={'child_nickname':'小禾','guardian_role':'mother'}).start()
        self.config={'enabled':True,'slow_enabled':True,'family_uid':'family-a','contact_id':'contact-a'}
        self.write_config()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.executescript((Path(__file__).parent/'schema.sql').read_text())
        self.count=0

    def write_config(self):
        (self.root/'config/preference-memory.json').write_text(json.dumps(self.config))

    def message(self,body='以后回复简短一点',ident=None,role='user',at=None):
        self.count+=1;ident=ident or 'source-'+str(self.count)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,1)',(ident,role,body,at or (self.now+dt.timedelta(seconds=self.count)).isoformat(),'test_explicit',hashlib.sha256(body.encode()).hexdigest()))
        return ident

    def change(self,ident,key='reply.length',value='brief',action='set',**extra):
        with closing(sqlite3.connect(s.DB)) as c:
            quote=c.execute('SELECT body FROM messages WHERE source_id=?',(ident,)).fetchone()[0]
        return {'key':key,'action':action,'subject':'parent','basis':'explicit','reason':'当前用户明确表达，按对应语境识别主体及期限',
                'applicability':'temporary' if '今天回复' in quote else 'persistent','source_message_id':ident,'quote':quote,**({'value':value} if action=='set' else {}),**extra}

    def commit(self,*changes):
        return m.commit('request-'+str(self.count)+'-'+str(m.recall()['revision']),m.context()['context_token'],list(changes))

    def start(self):
        self.now=self.now.replace(hour=22,minute=0)
        result=m.tick();self.assertEqual(result['status'],'queued')
        event=live.claim();self.assertEqual(event['id'],result['event_id'])
        return event['id']

    def finish(self,ident,entries=None,candidates=None):
        page=m.consolidation_context(ident)
        while page['has_more']:page=m.consolidation_context(ident,page['next_sequence'])
        updates=[{'action':'add','subject':'parent','basis':'inferred','applicability':'uncertain',
                  'scope_note':'当前原话提供的线索，尚需后续反馈确认','reason':'根据本次新证据补充家庭记忆',**x} for x in (candidates or [])]
        notes=[{'subject':'parent',**x} for x in (entries or [])]
        prepared=m.consolidation_prepare(event_id=ident,expected_revision=page['revision'],reviewed_through_sequence=page['through_sequence'],
            entries=notes,changes=updates,no_change_reason='本窗口没有足以新增或修订的持续服务信息' if not notes and not updates else '')
        self.last_prepared=prepared['prepared_token']
        return m.consolidation_commit(ident,self.last_prepared)

    def test_disabled_no_database_side_effects(self):
        self.config['enabled']=False;self.write_config()
        before=s.DB.read_bytes()
        self.assertEqual(m.tick()['status'],'disabled');self.assertFalse(m.recall()['enabled'])
        self.assertEqual(before,s.DB.read_bytes())

    def test_parent_child_same_key_and_correction_are_independent(self):
        parent=self.message('我喜欢历史故事');child=self.message('孩子喜欢恐龙故事')
        self.commit(self.change(parent,key='reading.interest',value='历史故事'),
                    self.change(child,key='reading.interest',value='恐龙故事',subject='child'))
        result=m.recall();self.assertEqual(json.loads(result['scope']),['live','family-a'])
        self.assertEqual(result['by_subject']['parent'][0]['value'],'历史故事')
        self.assertEqual(result['by_subject']['child'][0]['evidence_kind'],'parent_report_about_child')
        newer=self.message('孩子现在喜欢太空故事')
        self.commit(self.change(newer,key='reading.interest',value='太空故事',subject='child'))
        self.assertEqual(m.recall()['by_subject']['parent'][0]['value'],'历史故事')
        self.assertEqual(m.recall()['by_subject']['child'][0]['value'],'太空故事')

    def test_only_parent_or_child_subjects(self):
        src=self.message()
        for subject in ['father','mother','unknown','']:
            with self.assertRaisesRegex(ValueError,r'changes\[0\].subject'):
                self.commit(self.change(src,subject=subject))
        self.assertEqual(m.recall()['revision'],0)

    def test_semantic_subject_attribution_is_not_a_nickname_regex(self):
        src=self.message('我问他还想看什么，他又指了指月球那一页。')
        self.commit(self.change(src,key='service.context',value='家长转述孩子再次选择月球页面',subject='child'))
        self.assertEqual(m.recall()['by_subject']['child'][0]['value'],'家长转述孩子再次选择月球页面')

    def test_child_media_does_not_restrict_parent_transport(self):
        src=self.message('孩子不喜欢图片')
        self.commit(self.change(src,key='media.images',value='disabled',subject='child'))
        with closing(sqlite3.connect(s.DB)) as c,c:m.guard_media(c,6)
        src=self.message('我不喜欢图片')
        self.commit(self.change(src,key='media.images',value='disabled'))
        with closing(sqlite3.connect(s.DB)) as c,c:
            with self.assertRaisesRegex(ValueError,'media_blocked'):m.guard_media(c,6)

    def test_forgetting_child_keeps_parent_value(self):
        parent=self.message('我喜欢历史故事');child=self.message('孩子喜欢恐龙故事')
        self.commit(self.change(parent,key='reading.interest',value='历史故事'),
                    self.change(child,key='reading.interest',value='恐龙故事',subject='child'))
        src=self.message('请忘记孩子的阅读兴趣')
        self.commit(self.change(src,key='reading.interest',action='forget',subject='child'))
        result=m.recall();self.assertEqual(result['by_subject']['parent'][0]['value'],'历史故事')
        self.assertEqual(result['by_subject']['child'],[])
        self.assertEqual(result['forgotten_by_subject'],{'parent':[],'child':['reading.interest']})

    def test_one_family_nightly_run_with_two_subjects(self):
        parent=self.message('我喜欢历史故事');child=self.message('孩子喜欢恐龙故事')
        self.commit(self.change(parent,key='reading.interest',value='历史故事'),
                    self.change(child,key='reading.interest',value='恐龙故事',subject='child'))
        ident=self.start();page=m.consolidation_context(ident)
        seq=next(x['seq'] for x in page['items'] if x['source_id']==child)
        self.finish(ident,candidates=[{'key':'reading.routine','subject':'child','note':'孩子可能喜欢相关主题阅读','evidence':[seq]}])
        self.assertEqual(len(m.status()['runs']),1)
        self.assertEqual(m.status()['candidates'][0]['subject'],'child')
        self.assertEqual(len(m.recall()['items']),2)
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_v1_scope_migration_preserves_retry_clock_and_pending_event(self):
        src=self.message();ctx=m.context();change=self.change(src)
        m.commit('stable',ctx['context_token'],[change]);ident=self.start()
        with m.database() as (c,sc):
            due=c.execute('SELECT next_due FROM memory_clock').fetchone()[0]
            old=s.encoded(['live','family-a','contact-a'])
            for table in ['preference_scopes','preference_versions','preference_tokens','preference_requests','preference_forgotten',
                          'memory_journal','memory_seen','memory_checkpoints','memory_clock','memory_runs','memory_candidates','memory_summaries']:
                c.execute('UPDATE '+table+' SET scope=? WHERE scope=?',(old,sc))
            raw=json.loads(c.execute('SELECT payload FROM events WHERE id=?',(ident,)).fetchone()[0]);raw['memory_scope']=old
            c.execute('UPDATE events SET payload=? WHERE id=?',(s.encoded(raw),ident))
            raw=json.loads(c.execute('SELECT result FROM preference_requests').fetchone()[0]);raw['scope']=old
            c.execute('UPDATE preference_requests SET result=?',(s.encoded(raw),))
            for table in ['preference_versions','memory_candidates']:c.execute('ALTER TABLE '+table+' DROP COLUMN subject')
        result=m.recall();self.assertEqual(result['revision'],1)
        self.assertEqual(result['items'][0]['subject'],'parent')
        retry=m.commit('stable',ctx['context_token'],[change])
        self.assertTrue(retry['duplicate']);self.assertEqual(retry['scope'],result['scope'])
        self.assertEqual(s.read('SELECT next_due FROM memory_clock')[0]['next_due'],due)
        self.assertEqual(json.loads(s.read('SELECT payload FROM events')[0]['payload'])['memory_scope'],result['scope'])
        self.finish(ident);self.assertEqual(len(m.status()['runs']),1)

    def test_contact_binding_is_ingress_guard_not_subject(self):
        self.commit(self.change(self.message()))
        self.config['contact_id']='another-contact';self.write_config()
        with self.assertRaisesRegex(ValueError,'binding_changed'):m.recall()

    def test_explicit_recall_correction_and_immutable_retry(self):
        source=self.message();ctx=m.context();change=self.change(source)
        first=m.commit('stable',ctx['context_token'],[change])
        self.assertEqual(m.commit('stable',ctx['context_token'],[change])['duplicate'],True)
        with self.assertRaisesRegex(ValueError,'immutable'):m.commit('stable',ctx['context_token'],[{**change,'value':'detailed'}])
        self.assertEqual(m.recall()['items'][0]['value'],'brief')
        newer=self.message('以后请详细解释');self.commit(self.change(newer,value='detailed'))
        self.assertEqual(m.recall()['items'][0]['value'],'detailed')
        self.assertEqual(len(s.read('SELECT * FROM preference_versions')),2)
        self.assertEqual(first['revision'],1)

    def test_atomic_change_rollback_on_invalid_evidence(self):
        src=self.message();ctx=m.context()
        with self.assertRaisesRegex(ValueError,'exact_user_quote'):
            m.commit('bad',ctx['context_token'],[self.change(src),{**self.change(src,key='media.emoji',value='disabled'),'quote':'不存在的原话'}])
        self.assertEqual(m.recall()['revision'],0);self.assertEqual(m.recall()['items'],[])

    def test_no_assistant_or_outside_evidence(self):
        src=self.message(role='assistant')
        with self.assertRaisesRegex(ValueError,'outside_context'):self.commit(self.change(src))
        src=self.message('你好')
        with self.assertRaisesRegex(ValueError,'exact_user_quote'):self.commit({**self.change(src,key='address.name',value='小陈'),'quote':'不存在的证据'})

    def test_context_conflict_new_message_and_concurrent_commit(self):
        src=self.message();ctx=m.context();self.message('还有一件事')
        with self.assertRaisesRegex(ValueError,'stale'):m.commit('old',ctx['context_token'],[self.change(src)])
        one=m.context();two=m.context();m.commit('one',one['context_token'],[self.change(src)])
        with self.assertRaisesRegex(ValueError,'stale'):m.commit('two',two['context_token'],[self.change(src)])

    def test_old_evidence_does_not_replace_new(self):
        old=self.message(at='2026-10-24T20:00:00+08:00');new=self.message('以后详细回复')
        self.commit(self.change(new,value='detailed'))
        with self.assertRaisesRegex(ValueError,'older_or_same'):self.commit(self.change(old))

    def test_temporary_preference_expiry(self):
        src=self.message('今天回复简短一点')
        self.commit(self.change(src,expires_at='2026-10-27T00:00:00+08:00'))
        self.assertEqual(len(m.recall()['items']),1)
        self.now=dt.datetime.fromisoformat('2026-10-27T00:00:00+08:00')
        self.assertEqual(m.recall()['items'],[])

    def test_unknown_expiry_is_contextual_not_an_unlimited_media_ban(self):
        src=self.message('这阵子出门不方便看图，回家后就还好')
        self.commit(self.change(src,key='media.images',value='disabled',applicability='temporary'))
        self.assertEqual(m.recall()['items'],[])
        self.assertEqual(m.recall()['memories'][0]['applicability'],'temporary')
        with closing(sqlite3.connect(s.DB)) as c,c:m.guard_media(c,6)
        self.assertEqual(s.read('SELECT status FROM preference_versions')[0]['status'],'contextual')

    def test_binding_cannot_change_same_live_database(self):
        self.commit(self.change(self.message()))
        self.config['family_uid']='family-b';self.write_config()
        with self.assertRaisesRegex(ValueError,'binding_changed'):m.recall()

    def test_test_runs_do_not_inherit_live_or_other_run_memories(self):
        self.commit(self.change(self.message()))
        for run in ['r1','r2']:
            with closing(sqlite3.connect(s.DB)) as c,c:
                c.execute("INSERT OR REPLACE INTO metadata VALUES('test_mode',?)",(json.dumps({'uid':'family-a','run_id':run}),))
            self.assertEqual(m.recall()['items'],[])
            self.commit(self.change(self.message()))
            self.assertTrue(m.recall()['scope'].startswith('["test:'+run))

    def test_forget_scrubs_derived_data_and_rejects_old_evidence(self):
        src=self.message();self.commit(self.change(src));ident=self.start();page=m.consolidation_context(ident)
        seq=page['items'][0]['seq']
        self.finish(ident,[{'note':'家长表达希望回复更简短','evidence':[seq]}],[{'key':'reply.length','note':'家长可能倾向简短回复','evidence':[seq]}])
        forget=self.message('忘记我之前关于回复长短的偏好');self.commit(self.change(forget,action='forget'))
        self.assertEqual(m.recall()['items'],[])
        self.assertEqual(m.status()['candidates'],[]);self.assertEqual(m.status()['summaries'],[])
        self.assertTrue(all(r['value'] is None and r['quote'] is None for r in s.read('SELECT * FROM preference_versions')))
        self.assertTrue(json.loads(s.read('SELECT body FROM memory_journal')[0]['body'])['redacted'])
        with self.assertRaisesRegex(ValueError,'forgotten_evidence'):self.commit(self.change(src))
        self.commit(self.change(self.message('现在重新记住，回复简短一点')))
        self.assertEqual(m.recall()['items'][0]['value'],'brief')

    def test_media_guard_blocks_before_transport_but_allows_text(self):
        self.commit(self.change(self.message('以后不要发图片'),key='media.images',value='disabled'))
        with closing(sqlite3.connect(s.DB)) as c,c:
            m.guard_media(c,7);m.guard_media(c,5)
            with self.assertRaisesRegex(ValueError,'media_blocked'):m.guard_media(c,6)

    def test_22_boundary_single_enqueue_empty_commit(self):
        self.assertEqual(m.tick()['status'],'not_due')
        ident=self.start();self.assertEqual(m.tick()['status'],'pending')
        self.finish(ident);self.assertEqual(m.tick()['status'],'already_scheduled')
        self.assertEqual(s.read('SELECT status FROM events')[0]['status'],'done')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_incremental_late_receipt_and_post_cutoff_message(self):
        self.message()
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute("INSERT INTO deliveries VALUES('d','content','accepted','at','test')")
        ident=self.start();page=m.consolidation_context(ident)
        self.assertEqual(len(page['items']),2)
        self.assertEqual(page['window_statistics']['delivery_status_counts'],{'accepted':1})
        self.message('22点后新消息');self.finish(ident)
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET status='sent'")
        self.now+=dt.timedelta(days=1);ident=self.start();page=m.consolidation_context(ident)
        self.assertEqual(len(page['items']),2)
        self.assertEqual([x['body']['status'] for x in page['items'] if x['kind']=='delivery'],['sent'])
        self.assertEqual(page['window_statistics']['delivery_status_counts'],{'sent':1})

    def test_pagination_and_frozen_window(self):
        for i in range(105):self.message('第'+str(i)+'条')
        ident=self.start();page=m.consolidation_context(ident);self.assertTrue(page['has_more'])
        with self.assertRaisesRegex(ValueError,'not_reviewed'):m.consolidation_prepare(event_id=ident,expected_revision=0,reviewed_through_sequence=page['through_sequence'],entries=[],changes=[],no_change_reason='本次尚未读取全部材料不应提交')
        with self.assertRaisesRegex(ValueError,'page_gap'):m.consolidation_context(ident,101)
        self.message('late');page=m.consolidation_context(ident,page['next_sequence']);self.assertEqual(len(page['items']),5)
        self.assertFalse(page['has_more']);self.finish(ident)

    def test_restart_reuses_run_and_commit_is_idempotent(self):
        self.message();ident=self.start();m.consolidation_context(ident)
        live.recover();self.assertEqual(live.claim()['id'],ident)
        result=self.finish(ident)
        self.assertTrue(m.consolidation_commit(ident,self.last_prepared)['duplicate'])
        with self.assertRaisesRegex(ValueError,'immutable'):m.consolidation_commit(ident,'another-token')
        self.assertEqual(len(s.read('SELECT * FROM memory_runs')),1)

    def test_candidate_is_never_an_active_preference(self):
        self.message('这样挺好');ident=self.start();page=m.consolidation_context(ident)
        candidate={'key':'reply.length','note':'可能接受简短回复，尚未确认','evidence':[page['items'][0]['seq']]}
        self.finish(ident,candidates=[candidate]);self.assertEqual(m.recall()['items'],[])
        self.assertEqual(m.status()['candidates'][0]['status'],'candidate')

    def test_candidate_cannot_learn_from_system_output(self):
        self.message('你喜欢简短回复',role='assistant');ident=self.start();page=m.consolidation_context(ident)
        with self.assertRaisesRegex(ValueError,'user_evidence'):
            self.finish(ident,candidates=[{'key':'reply.length','note':'家长可能喜欢简短回复','evidence':[page['items'][0]['seq']]}])

    def test_slow_does_not_advance_cursor_on_conflict(self):
        src=self.message();ident=self.start();page=m.consolidation_context(ident)
        self.commit(self.change(src))
        with self.assertRaisesRegex(ValueError,'stale'):m.consolidation_prepare(event_id=ident,expected_revision=page['revision'],reviewed_through_sequence=page['through_sequence'],entries=[],changes=[],no_change_reason='当前材料没有需要补充的记忆内容')
        self.assertEqual(s.read('SELECT seq FROM memory_checkpoints')[0]['seq'],0)
        self.finish(ident)

    def test_passive_preempts_slow_and_busy_claim_cannot_start_slow(self):
        self.now=self.now.replace(hour=22);event=m.tick()
        self.assertIsNone(live.claim(allow_internal=False))
        ident=live.claim()['id'];self.assertTrue(m.internal())
        source=self.message('现在有问题')
        with closing(sqlite3.connect(s.DB)) as c,c:live.enqueue(c,'inbound', 'passive',{'source_message_id':source})
        with self.assertRaisesRegex(ValueError,'priority'):m.consolidation_context(ident)
        # The serialized host pauses the owned nightly run before claiming chat.
        self.assertIsNone(live.claim())
        m.pause();self.assertFalse(m.internal())
        with patch('service_hours.now',return_value=self.now.replace(hour=7)):
            self.assertEqual(live.claim()['kind'],'passive')
        with self.assertRaisesRegex(ValueError,'not_owned'):m.consolidation_context(ident)

    def test_overnight_catchup_is_incremental_no_past_sends(self):
        ident=self.start();self.finish(ident)
        self.now+=dt.timedelta(days=2,hours=-15)
        result=m.tick();self.assertEqual(result['day'],'2026-10-27')
        self.assertEqual(result['cutoff_at'],'2026-10-28T07:00:00+08:00')
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_first_night_outage_recovers_persisted_due_time(self):
        self.assertEqual(m.tick()['status'],'not_due')
        self.now=dt.datetime.fromisoformat('2026-10-27T07:00:00+08:00')
        self.assertEqual(m.tick()['day'],'2026-10-26')

    def test_inference_cannot_claim_explicit_commit_contract(self):
        src=self.message('今天孩子随手翻了一本书')
        with self.assertRaisesRegex(ValueError,'basis'):
            self.commit({**self.change(src),'basis':'inferred'})
        self.assertEqual(m.recall()['items'],[])

    def test_mcp_internal_permission_blocks_all_business_tools(self):
        import mcp_server,business
        business.initialize()
        self.message();ident=self.start()
        for tool in ['reply_to_message','workflow_commit','service_schedule','memory_remember','preference_commit','generate_image']:
            with self.subTest(tool=tool),self.assertRaisesRegex(ValueError,'no_business_or_send_permission'):
                mcp_server.business_call(tool,{})
        self.assertEqual(mcp_server.business_call('memory_consolidation_context',{'event_id':ident})['event_id'],ident)

    def test_mcp_explicit_write_requires_current_passive_event(self):
        import mcp_server,business
        business.initialize()
        source=self.message('忘记回复长短的偏好');ctx=mcp_server.business_call('preference_context',{})
        args={'request_id':'native','context_token':ctx['context_token'],'changes':[self.change(source,action='forget')]}
        with self.assertRaisesRegex(ValueError,'current_dialogue'):mcp_server.business_call('preference_commit',args)
        with closing(sqlite3.connect(s.DB)) as c,c:live.enqueue(c,'inbound','passive',{'source_message_id':source})
        live.claim();self.assertEqual(mcp_server.business_call('preference_commit',args)['status'],'committed')

    def test_source_tampering_invalidates_context(self):
        src=self.message();ctx=m.context()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE messages SET body='以后请详细回复' WHERE source_id=?",(src,))
        with self.assertRaisesRegex(ValueError,'source_changed'):
            m.commit('tampered',ctx['context_token'],[self.change(src,value='detailed')])

    def test_actual_sender_blocks_disabled_image_before_outbox_and_http(self):
        import wechat_test as w
        import test_mode as tm
        w.initialize()
        self.commit(self.change(self.message('以后不要发图片'),key='media.images',value='disabled'))
        with patch.object(w,'config',return_value={'test':True}),patch.object(tm,'state',return_value={'run_id':'r'}),patch.object(w,'provider_config',return_value=({},{})),patch.object(w,'http_send') as http:
            with self.assertRaisesRegex(ValueError,'media_blocked'):w.send('r:image','https://coolcdn.igetcool.com/test.png',6)
            http.assert_not_called()
        self.assertEqual(s.read('SELECT * FROM test_outbox'),[])

    def test_operator_retries_attention_without_new_run(self):
        ident=self.start()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE events SET status='needs_attention',attempts=3 WHERE id=?",(ident,))
        self.assertEqual(m.retry(ident)['event_id'],ident)
        self.assertEqual(live.claim()['id'],ident);self.finish(ident)
        with self.assertRaisesRegex(ValueError,'not_retryable'):m.retry(ident)

    def test_forgotten_key_cannot_return_as_inferred_candidate(self):
        self.commit(self.change(self.message()))
        self.commit(self.change(self.message('忘记回复长短的偏好'),action='forget'))
        self.message('最近这样回复挺好');ident=self.start();page=m.consolidation_context(ident)
        seq=page['items'][-1]['seq']
        with self.assertRaisesRegex(ValueError,'cannot_be_inferred'):
            self.finish(ident,candidates=[{'key':'reply.length','note':'家长可能喜欢简短回复','evidence':[seq]}])

    def test_mixed_positive_negative_clauses_and_business_reference(self):
        import mcp_server,business
        business.initialize()
        src=self.message('以后回复请简短一点，也不要给我发图片和表情。请记住这三个偏好。')
        with closing(sqlite3.connect(s.DB)) as c,c:live.enqueue(c,'mixed','passive',{'source_message_id':src})
        live.claim();ctx=m.context()
        changes=[self.change(src),self.change(src,key='media.images',value='disabled'),self.change(src,key='media.emoji',value='disabled')]
        for item in changes:item['source_message_id']=business.token(src)
        with self.assertRaisesRegex(ValueError,'nightly_only'):
            mcp_server.business_call('preference_commit',{'request_id':'mixed','context_token':ctx['context_token'],'changes':changes})
        self.assertEqual(len(m.recall()['items']),0)

    def test_current_dialogue_cannot_replay_unrelated_old_preference(self):
        import mcp_server,business
        business.initialize()
        old=self.message('忘记回复长短的偏好');new=self.message('我今天有别的问题')
        with closing(sqlite3.connect(s.DB)) as c,c:live.enqueue(c,'current','passive',{'source_message_id':new})
        live.claim();ctx=m.context()
        with self.assertRaisesRegex(ValueError,'current_batch_evidence'):
            mcp_server.business_call('preference_commit',{'request_id':'replay','context_token':ctx['context_token'],'changes':[self.change(old,action='forget')]})

    def test_disabling_slow_loop_does_not_dispatch_queued_work(self):
        self.now=self.now.replace(hour=22);event=m.tick()
        self.config['slow_enabled']=False;self.write_config()
        self.assertIsNone(live.claim());self.assertEqual(m.tick()['status'],'disabled')
        self.config['slow_enabled']=True;self.write_config()
        self.assertEqual(live.claim()['id'],event['event_id'])


    def packet(self,ident,changes=None,entries=None):
        page=m.consolidation_context(ident)
        while page['has_more']:page=m.consolidation_context(ident,page['next_sequence'])
        return dict(event_id=ident,expected_revision=page['revision'],reviewed_through_sequence=page['through_sequence'],
                    entries=entries or [],changes=changes or [],no_change_reason='本次没有值得新增或修订的家庭服务信息')

    def record(self,seq,**updates):
        return {'action':'add','subject':'child','key':'reading.interest','note':'孩子近期在学校主题活动中谈到太空',
                'evidence':[seq],'basis':'observed','applicability':'temporary','scope_note':'学校本期活动情境，持续兴趣未确认',
                'reason':'本次家长转述孩子的实际活动情况',**updates}

    def test_natural_expression_not_rejected_by_unrelated_time_clause(self):
        body='这周孩子在学校做火箭。你那个先给我一句示范再讲原因的方法，一直很管用。'
        src=self.message(body)
        change=self.change(src,key='reply.explanation',value='examples',quote='你那个先给我一句示范再讲原因的方法，一直很管用。')
        self.commit(change)
        self.assertIsNone(m.recall()['items'][0]['expires_at'])

    def test_prepare_is_read_only_until_atomic_commit(self):
        self.message();ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        packet=self.packet(ident,[self.record(seq)])
        prepared=m.consolidation_prepare(**packet)
        self.assertEqual(m.status()['candidates'],[])
        self.assertEqual(s.read('SELECT seq FROM memory_checkpoints')[0]['seq'],0)
        self.assertEqual(s.read('SELECT status FROM events')[0]['status'],'dispatched')
        m.consolidation_commit(ident,prepared['prepared_token'])
        self.assertEqual(len(m.recall()['memories']),1)
        self.assertEqual(m.recall()['items'],[])

    def test_placeholder_and_wrong_nested_fields_do_not_consume_window(self):
        self.message();ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        for item in [{'subject':'parent','note':'test','evidence':[seq]},
                     {'subject':'parent','note':'家长反馈可保留的内容','evidence':[seq],'text':'wrong'}]:
            with self.assertRaisesRegex(ValueError,r'entries\[0\]'):
                m.consolidation_prepare(**self.packet(ident,entries=[item]))
        self.assertEqual(s.read('SELECT seq FROM memory_checkpoints')[0]['seq'],0)
        self.finish(ident)

    def test_cross_night_update_replaces_head_and_retains_history(self):
        self.message('学校这周讲火箭，孩子回来也讲这个');ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        prepared=m.consolidation_prepare(**self.packet(ident,[self.record(seq)]));m.consolidation_commit(ident,prepared['prepared_token'])
        old=m.recall()['memories'][0];self.now+=dt.timedelta(days=1)
        self.message('学校活动已经结束，先别把这个当作长期兴趣')
        ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        update=self.record(seq,action='update',memory_id=old['id'],basis='reported',applicability='uncertain',note='家长明确提醒不要把本期学校话题当作长期兴趣')
        prepared=m.consolidation_prepare(**self.packet(ident,[update]));m.consolidation_commit(ident,prepared['prepared_token'])
        head=m.recall()['memories'];self.assertEqual(len(head),1);self.assertEqual(head[0]['id'],old['id'])
        self.assertEqual(head[0]['note'],update['note']);self.assertEqual(len(s.read('SELECT * FROM memory_history')),2)
        self.assertEqual(m.recall()['recent_service_summaries'],[])

    def test_new_message_between_prepare_commit_invalidates_packet(self):
        self.message();ident=self.start();prepared=m.consolidation_prepare(**self.packet(ident));self.message('还有新信息')
        with self.assertRaisesRegex(ValueError,'stale'):m.consolidation_commit(ident,prepared['prepared_token'])
        self.assertEqual(s.read('SELECT seq FROM memory_checkpoints')[0]['seq'],0)
        self.finish(ident)

    def test_source_edit_after_prepare_rejected(self):
        src=self.message();ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        prepared=m.consolidation_prepare(**self.packet(ident,[self.record(seq)]))
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE messages SET body='修改后的不同证据' WHERE source_id=?",(src,))
        with self.assertRaisesRegex(ValueError,'source_changed'):m.consolidation_commit(ident,prepared['prepared_token'])
        self.assertEqual(m.status()['candidates'],[])

    def test_forgotten_slow_only_evidence_and_prepared_text_are_scrubbed(self):
        self.message('孩子最近在看太空主题');ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        prepared=m.consolidation_prepare(**self.packet(ident,[self.record(seq)]));m.consolidation_commit(ident,prepared['prepared_token'])
        forget=self.message('之前聊孩子阅读兴趣的那些，先忘掉吧')
        self.commit(self.change(forget,key='reading.interest',subject='child',action='forget'))
        self.assertEqual(s.read('SELECT * FROM memory_history'),[])
        self.assertEqual(s.read('SELECT * FROM memory_prepared'),[])
        self.assertTrue(json.loads(s.read('SELECT body FROM memory_journal WHERE seq=?',(seq,))[0]['body'])['redacted'])
        self.assertEqual(m.recall()['memories'],[])

    def test_fast_correction_retracts_old_slow_memory(self):
        src=self.message('孩子最近在看太空主题');ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        prepared=m.consolidation_prepare(**self.packet(ident,[self.record(seq)]));m.consolidation_commit(ident,prepared['prepared_token'])
        mid=m.recall()['memories'][0]['id']
        newer=self.message('学校活动不等于他的长期爱好，目前没有固定偏好的主题')
        self.commit(self.change(newer,key='reading.interest',subject='child',value='家长表示孩子目前没有固定偏好的主题',replaces_memory_ids=[mid]))
        self.assertEqual(m.recall()['memories'],[])
        self.assertEqual(m.status()['candidates'][0]['status'],'retracted')

    def test_fast_temporary_correction_updates_same_memory_without_inventing_end(self):
        self.message('孩子最近在看太空主题');ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        prepared=m.consolidation_prepare(**self.packet(ident,[self.record(seq)]));m.consolidation_commit(ident,prepared['prepared_token'])
        mid=m.recall()['memories'][0]['id']
        src=self.message('是幼儿园这周做火箭带起来的，先别按长期兴趣定')
        self.commit(self.change(src,key='reading.interest',subject='child',applicability='temporary',
            value='当前太空话题与学校活动有关，不作为长期兴趣定位',replaces_memory_ids=[mid]))
        memories=m.recall()['memories'];self.assertEqual(len(memories),1);self.assertEqual(memories[0]['id'],mid)
        self.assertEqual(memories[0]['status'],'reported');self.assertIsNone(memories[0]['expires_at'])
        self.assertEqual(m.recall()['items'],[])

    def test_repair_keeps_prior_checkpoint_and_audits_original(self):
        import memory_maintenance as maint
        self.message();old=self.start();self.finish(old)
        checkpoint=s.read('SELECT seq FROM memory_checkpoints')[0]['seq']
        repair=maint.repair(old,'原始摘要质量不足，需要重新审查全部窗口')
        self.assertEqual(live.claim()['id'],repair['event_id'])
        self.finish(repair['event_id'])
        self.assertEqual(s.read('SELECT seq FROM memory_checkpoints')[0]['seq'],checkpoint)
        self.assertEqual(len(m.status()['runs']),2)
        self.assertTrue(s.read('SELECT result_hash FROM memory_runs WHERE event_id=?',(old,))[0]['result_hash'])

    def test_prepare_returns_original_evidence_for_semantic_review_without_applying(self):
        src=self.message('今天太空那本没拿，不能据此说以后都不看')
        ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        record=self.record(seq,note='孩子今天没有拿太空书，不能推定长期变化')
        prepared=m.consolidation_prepare(**self.packet(ident,[record]))
        self.assertEqual(prepared['review']['proposed_changes'],[record])
        evidence=prepared['review']['evidence'];self.assertEqual(evidence[0]['source_id'],src)
        self.assertEqual(evidence[0]['body'],'今天太空那本没拿，不能据此说以后都不看')
        self.assertEqual(m.recall()['memories'],[])

    def test_operator_repair_reason_is_visible_in_context(self):
        import memory_maintenance as maint
        self.message('今天没看某一本不代表以后都不看');old=self.start();self.finish(old)
        reason='原记录把今天没看扩大成不再看，请对照原话修复时间范围'
        repair=maint.repair(old,reason);live.claim()
        page=m.consolidation_context(repair['event_id'])
        self.assertEqual(page['repair_reason'],reason)
        self.assertEqual(page['repair_of'],old)

    def test_empty_result_requires_explanation(self):
        self.message();ident=self.start();packet=self.packet(ident);packet['no_change_reason']=''
        with self.assertRaisesRegex(ValueError,'substantive'):m.consolidation_prepare(**packet)
        self.assertEqual(s.read('SELECT seq FROM memory_checkpoints')[0]['seq'],0)

    def test_correction_is_not_authorization_to_forget(self):
        src=self.message('先别按长期兴趣给他定下来')
        with self.assertRaisesRegex(ValueError,'explicit_removal_request'):
            self.commit(self.change(src,key='reading.interest',subject='child',action='forget'))
        self.assertEqual(m.recall()['revision'],0)

    def test_forget_child_preserves_unrelated_parent_slow_memory(self):
        self.message('孩子近期总找太空书');self.message('对话示范更容易跟着做')
        ident=self.start();rows=m.consolidation_context(ident)['items']
        packet=self.packet(ident,[self.record(rows[0]['seq']),self.record(rows[1]['seq'],subject='parent',key='reply.explanation',
            note='家长认可可照着操作的对话示范',basis='reported',applicability='persistent')])
        prepared=m.consolidation_prepare(**packet);m.consolidation_commit(ident,prepared['prepared_token'])
        src=self.message('孩子的阅读兴趣先别留着了')
        self.commit(self.change(src,key='reading.interest',subject='child',action='forget'))
        left=m.recall()['memories'];self.assertEqual(len(left),1);self.assertEqual(left[0]['subject'],'parent')
        self.assertEqual(len(s.read('SELECT * FROM memory_history')),1)

    def test_failed_second_change_leaves_entire_batch_unwritten(self):
        self.message();ident=self.start();seq=m.consolidation_context(ident)['items'][0]['seq']
        with self.assertRaisesRegex(ValueError,'not_in_scope'):
            m.consolidation_prepare(**self.packet(ident,[self.record(seq),self.record(seq,action='update',memory_id='foreign')]))
        self.assertEqual(m.status()['candidates'],[])
        self.assertEqual(s.read('SELECT * FROM memory_prepared'),[])

    def test_zero_starts_current_window_without_replaying_prior_window(self):
        self.message('首日已有的自然反馈');old=self.start();self.finish(old)
        prior=s.read('SELECT seq FROM memory_checkpoints')[0]['seq']
        self.now+=dt.timedelta(days=1);src=self.message('第二天新增的自然反馈');ident=self.start()
        page=m.consolidation_context(ident,0)
        self.assertEqual(page['from_sequence'],prior)
        self.assertEqual([x['source_id'] for x in page['items']],[src])
        self.assertTrue(all(x['seq']>prior for x in page['items']))
        with self.assertRaisesRegex(ValueError,'page_gap'):
            m.consolidation_context(ident,page['through_sequence']+1)

    def test_old_evidence_only_cannot_create_new_nightly_memory(self):
        self.message();old=self.start();seq=m.consolidation_context(old)['items'][0]['seq'];self.finish(old)
        self.now+=dt.timedelta(days=1);self.message('另一个问题');ident=self.start()
        with self.assertRaisesRegex(ValueError,'new_window_evidence'):
            m.consolidation_prepare(**self.packet(ident,[self.record(seq)]))
        self.assertEqual(m.evidence_read(ident,[seq])['items'][0]['seq'],seq)


if __name__=='__main__':unittest.main()
