import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
import message_text
import sql_tools as s
import wechat_test as w
import test_mode as tm
import test_fixture_integration as fixtures


class SplitOutboxTests(unittest.TestCase):
    setUp=fixtures.FixtureIntegrationTests.setUp
    tearDown=fixtures.FixtureIntegrationTests.tearDown
    create_source=fixtures.FixtureIntegrationTests.create_source
    set_context=fixtures.FixtureIntegrationTests.set_context

    def queue(self,images=None,legacy=False):
        self.messages=['先把本周的进度说清楚。','再把周末的安排说完整。']
        body='\n\n'.join(self.messages)
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('split-case',body,'queued',s.now(),'test_wechat_pending'))
            if not legacy:w.queue_text(c,'split-case',body)
            if images:w.queue_images(c,'split-case',images)

    def receipt(self,index):
        row=s.read('SELECT id,request_id FROM test_outbox ORDER BY rowid')[index]
        with closing(sqlite3.connect(s.DB)) as c,c:w.observe(c,{'externalRequestId':row['id'],'requestId':row['request_id'],'sendCode':0,'messageId':'confirmed-'+str(index)})

    def test_auto_three_bubbles_share_one_receipt_group(self):
        body='一'*31+'\n'+'二'*50+'\n三'
        with closing(sqlite3.connect(s.DB)) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',('split-case',body,'queued',s.now(),'test_wechat_pending'))
            w.queue_text(c,'split-case',body)
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'p'+str(i)} for i in range(3)]) as send:
            w.flush()
            self.assertEqual([x.args[3] for x in send.call_args_list],['一'*31,'二'*50,'三'])
        for index in range(3):
            self.receipt(index)
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'sent')

    def test_two_texts_share_one_delivery_and_wait_for_both_receipts(self):
        self.queue()
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'p1'},{'errcode':0,'requestId':'p2'}]) as send:
            self.assertEqual(w.flush()[0]['status'],'accepted')
            self.assertEqual([x.args[3] for x in send.call_args_list],self.messages)
            self.assertEqual(w.flush(),[])
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET source='test_wechat_pending'")
            w.flush();self.assertEqual(send.call_count,2)
        self.receipt(0)
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'accepted')
        self.receipt(1)
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'sent')
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_unknown_first_part_stops_second_without_automatic_retry(self):
        self.queue()
        with patch.object(w,'http_send',side_effect=TimeoutError) as send:
            self.assertEqual(w.flush()[0]['status'],'unknown')
            with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE deliveries SET source='test_wechat_pending'")
            w.flush();self.assertEqual(send.call_count,1)

    def test_failed_second_part_is_partial_and_never_complete(self):
        self.queue()
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'p1'},{'errcode':1}]) as send:
            self.assertEqual(w.flush()[0]['status'],'partial')
            self.assertEqual(send.call_count,2)
            self.assertEqual(w.flush(),[])
        self.receipt(0)
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'partial')

    def test_existing_images_follow_both_texts_and_all_receipts_are_required(self):
        image='https://coolcdn.igetcool.com/existing.png';self.queue([image])
        with patch.object(w,'http_send',side_effect=[{'errcode':0,'requestId':'p'+str(i)} for i in range(3)]) as send:
            self.assertEqual(w.flush()[0]['status'],'accepted')
            self.assertEqual([x.args[3] for x in send.call_args_list],self.messages+[image])
        self.receipt(0);self.receipt(1)
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'accepted')
        self.receipt(2)
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'sent')

    def test_legacy_single_text_is_not_reinterpreted_as_two(self):
        self.queue(legacy=True)
        with patch.object(w,'http_send',return_value={'errcode':0,'requestId':'p1'}) as send:
            w.flush();self.assertEqual(send.call_count,1)
        self.receipt(0)
        self.assertEqual(s.read('SELECT status FROM deliveries')[0]['status'],'sent')

    def test_clock_reset_drops_text_boundaries_with_the_delivery(self):
        self.queue()
        before=tm.state()
        with self.assertRaises(ValueError):tm.activate('2025-10-25 09:00',False)
        self.assertEqual(tm.state(),before)
        self.assertEqual(json.loads(s.read('SELECT messages FROM test_delivery_texts')[0]['messages']),self.messages)
        with patch.object(w,'http_send',side_effect=lambda cfg,route,key,body:{'errcode':0,'requestId':'reset-'+key}):w.flush()
        self.receipt(0);self.receipt(1)
        tm.activate('2025-10-25 09:00',False)
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])
        self.assertEqual(s.read('SELECT * FROM test_delivery_texts'),[])


if __name__=='__main__':unittest.main()
