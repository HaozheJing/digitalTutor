import copy,json,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
import test_fixture_integration as fixtures,test_host_receipt as hostbase
import sql_tools as s,test_mode as tm,agent_plan as ap,wechat_test as w
import s01_reading_guides as guides,experiment_runtime as er,business as b

class GuideTests(unittest.TestCase):
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context
    begin_workflow=hostbase.HostReceiptTests.begin_workflow
    plan=hostbase.HostReceiptTests.plan
    stop_plan=hostbase.HostReceiptTests.stop_plan
    new_task=hostbase.HostReceiptTests.new_task
    start_action=hostbase.HostReceiptTests.start_action
    begin=hostbase.HostReceiptTests.begin
    deliver=hostbase.HostReceiptTests.deliver
    finish=hostbase.HostReceiptTests.finish

    def setUp(self):
        fixtures.FixtureIntegrationTests.setUp(self)
        # Keep the real required-image contract; only replace the isolated experiment identity.
        self.manifest=patch.object(er,'manifest',return_value={'experiment_run_id':'exp-'+'a'*32})
        self.manifest.start();self.addCleanup(self.manifest.stop)
        self.assertTrue(guides.required())

    def accept(self):
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'text'},{'errcode':0,'requestId':'image'}]) as send:
            result=self.deliver()
        self.assertEqual(result['status'],'accepted',result)
        self.assertEqual([x.args[4] if len(x.args)>4 else x.kwargs.get('message_type',7) for x in send.call_args_list],[7,6])
        return result

    def receipt(self,kind):
        row=s.read('SELECT id FROM test_outbox WHERE message_type=?',(kind,))[0]
        with closing(sqlite3.connect(s.DB)) as c,c:
            w.observe(c,{'externalRequestId':row['id'],'sendCode':0,'messageId':'sent-'+str(kind)})

    def test_all_twenty_originals_exact_lesson_and_pending_answers(self):
        for n in range(1,21):
            value=guides.select({'status':'scheduled','class_type':'L2','lesson_no':n})
            self.assertEqual(value['status'],'ready');asset=value['asset']
            self.assertEqual(asset['id'],'s01-L2-M1-day%02d'%n)
            self.assertEqual(len(asset['questions']),2)
            self.assertTrue(all(q['answer_status']=='pending' and q['answer_ref'] is None for q in asset['questions']))
        for c,n in [('L2',21),('L3',1),('L2',None)]:
            self.assertEqual(guides.select({'status':'scheduled','class_type':c,'lesson_no':n})['status'],'unavailable')
        self.assertEqual(guides.select({'status':'no_lesson'})['status'],'unavailable')

    def test_registered_answer_projection_never_claims_a_read_or_mutates_cards(self):
        import query_contract
        raw=guides.REGISTRY.read_bytes()
        with patch.object(query_contract,'live_call',side_effect=AssertionError('projection must not read cloud answers')):
            for n in range(1,21):
                asset=guides.select({'status':'scheduled','class_type':'L2','lesson_no':n})['asset']
                original=copy.deepcopy(asset)
                result=guides.public(asset);reference=result['answer_reference']
                self.assertEqual(reference['status'],'registered_not_read')
                self.assertEqual(reference['question_match_status'],'requires_comparison')
                self.assertEqual(reference['curriculum_ref']['week'],(n-1)//5+1)
                self.assertEqual(reference['curriculum_ref']['day'],(n-1)%5+1)
                self.assertNotIn('body',reference)
                self.assertEqual(asset,original)
        self.assertEqual(guides.REGISTRY.read_bytes(),raw)

    def test_missing_or_ambiguous_answer_mapping_stays_unavailable(self):
        from pathlib import Path
        catalog=json.loads(guides.CATALOG.read_text())
        local=self.root/'answer-catalog.json'
        asset=json.loads(guides.REGISTRY.read_text())['assets'][0]
        with patch.object(guides,'CATALOG',local):
            self.assertEqual(guides.answer_reference(asset)['status'],'not_available')
            catalog['lessons'].append(copy.deepcopy(catalog['lessons'][0]))
            local.write_text(json.dumps(catalog))
            self.assertEqual(guides.answer_reference(asset)['status'],'not_available')
        for fields in [{'class_type':'L3'},{'lesson_no':None},{'lesson_no':True},{'lesson_no':999}]:
            self.assertIsNone(guides.answer_reference({**asset,**fields})['read_args'])

    def test_wording_difference_requires_comparison_not_automatic_grading(self):
        asset=guides.select({'status':'scheduled','class_type':'L2','lesson_no':12})['asset']
        reference=guides.answer_reference(asset)
        self.assertTrue(reference['known_wording_difference'])
        self.assertEqual(reference['status'],'registered_not_read')
        self.assertEqual(reference['question_match_status'],'requires_comparison')

    def test_tampered_hash_size_or_path_is_rejected(self):
        asset=json.loads(guides.REGISTRY.read_text())['assets'][0]
        for fields in [{'sha256':'0'*64},{'size':1},{'path':'config/workbench.json'},{'path':'assets/reading-guides/missing.png'}]:
            with self.assertRaises(ValueError):guides.verify({**asset,**fields})

    def test_text_then_original_image_waits_both_receipts_and_dedupes(self):
        self.begin();result=self.accept();self.assertEqual(self.finish(result)['status'],'awaiting_delivery')
        self.receipt(7);self.assertEqual(self.finish(result)['status'],'awaiting_delivery')
        self.assertEqual(guides.history('2025-10-13')['sent_guides'],[])
        self.receipt(6);self.assertEqual(self.finish(result)['status'],'done')
        history=guides.history('2025-10-13');self.assertEqual(history['sent_guides'][0]['questions'][0]['text'],'三个饲养场主分别叫什么？')
        self.assertEqual(history['sent_guides'][0]['image_status'],'sent')
        self.assertEqual(history['answer_key_status'],'registered_not_read')
        reference=history['sent_guides'][0]['answer_reference']
        self.assertEqual(reference['read_args'],{'target':{'document_id':'weeks-W01-Day1-reading-qa'}})
        self.assertEqual(reference['question_match_status'],'requires_comparison')
        self.assertEqual(history['sent_guides'][0]['questions'][0]['answer_status'],'registered_not_read')
        self.assertIn('reading_guides',b.context('2025-10-13'))
        with patch.object(w,'http_send') as send:
            w.flush();w.flush();send.assert_not_called()
        self.assertEqual(len(s.read('SELECT * FROM s01_guide_deliveries')),1)

    def test_accepted_does_not_claim_image_sent_or_expose_questions_as_sent(self):
        self.begin();self.accept();value=guides.history('2025-10-13')
        self.assertFalse(value['sent_guides']);self.assertNotIn('questions',value['pending_guides'][0])
        self.assertEqual(value['pending_guides'][0]['image_status'],'accepted')

    def test_image_failure_does_not_complete_or_retry_text(self):
        self.begin()
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'text'},TimeoutError]) as send:
            result=self.deliver();self.assertEqual(result['status'],'partial')
            self.assertEqual(self.finish(result)['status'],'awaiting_delivery');w.flush();self.assertEqual(send.call_count,2)
        self.receipt(7);self.assertEqual(self.finish(result)['status'],'blocked');self.assertFalse(guides.history('2025-10-13')['sent_guides'])

    def test_text_timeout_never_sends_image(self):
        self.begin()
        with patch.object(w,'http_send',side_effect=TimeoutError) as send:
            result=self.deliver();w.flush();self.assertEqual(send.call_count,1)
        self.assertEqual(result['status'],'unknown');self.assertFalse(guides.history('2025-10-13')['sent_guides'])

    def test_missing_card_blocks_before_any_text(self):
        self.begin()
        with patch.object(guides,'select',return_value={'status':'unavailable','reason':'missing'}),patch.object(w,'http_send') as send:
            self.assertEqual(self.finish(self.deliver())['status'],'blocked');send.assert_not_called()
        self.assertFalse(s.read('SELECT * FROM deliveries'))

    def test_real_channel_not_configured_blocks_before_any_text(self):
        self.begin()
        with patch.object(er,'manifest',return_value=None),patch.object(w,'http_send') as send:
            self.assertEqual(self.finish(self.deliver())['status'],'blocked');send.assert_not_called()

    def test_verified_manual_channel_sends_pinned_original_and_requires_both_receipts(self):
        import manual_media
        self.begin()
        with patch.object(er,'manifest',return_value=None),patch('manual_workbench.verify',return_value={'enabled':True}),patch.object(manual_media,'publish',side_effect=lambda asset,raw:manual_media.url(asset)) as publish:
            result=self.accept();publish.assert_called_once()
            self.assertEqual(self.finish(result)['status'],'awaiting_delivery')
            self.receipt(7);self.receipt(6)
            self.assertEqual(self.finish(result)['status'],'done')
            self.assertTrue(guides.history('2025-10-13')['sent_guides'])
            with patch.object(w,'http_send') as send:w.flush();send.assert_not_called()

    def test_pause_and_completion_still_stop_both_parts(self):
        self.begin()
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute('INSERT INTO contact_constraints VALUES(?,?,?,?)',('2025-10-13','m','暂停',s.now()))
        with patch.object(w,'http_send') as send:
            self.assertEqual(self.deliver()['status'],'skipped');send.assert_not_called()

    def test_wrong_sender_key_or_run_cannot_reuse_pinned_card(self):
        self.begin();self.accept();row=s.read('SELECT * FROM s01_guide_deliveries')[0]
        with closing(sqlite3.connect(s.DB)) as c:
            with self.assertRaises(ValueError):guides.authorize(c,'forged-key',row['public_url'])
            key=tm.state()['run_id']+':'+row['action_key']+':image:0'
            self.assertTrue(guides.authorize(c,key,row['public_url']))
            with patch.object(er,'manifest',return_value={'experiment_run_id':'exp-'+'b'*32}):
                with self.assertRaises(ValueError):guides.authorize(c,key,row['public_url'])
        state=tm.state()
        with patch.object(tm,'state',return_value={**state,'uid':'other'}):
            self.assertFalse(guides.registered(row['public_url']));self.assertIsNone(guides.history('2025-10-13'))

    def test_planned_context_contains_exact_question_but_not_sent_claim(self):
        self.begin();import learning_context,course_service
        with patch.object(course_service,'course_knowledge',return_value={'status':'unavailable'}):value=learning_context.read()
        self.assertEqual(value['reading_guide']['card']['lesson_no'],1)
        self.assertEqual(value['reading_guide']['delivery_status'],'not_sent')
        self.assertIn('reading_guide_context',value['fact_tools'])
        self.assertEqual(value['family']['child_nickname'],'侨侨')
        with patch.object(ap,'active',return_value=None),patch.object(course_service,'course_knowledge',return_value={'status':'unavailable'}):
            self.assertNotIn('reading_guide',learning_context.read())

if __name__=='__main__':unittest.main()
