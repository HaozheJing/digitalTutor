"""Leased runtime cursor program tests; no model calls or production state."""
from pathlib import Path
import tempfile,unittest
from digital_tutor.application import Application,Clock
from digital_tutor.context import build_context
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue
from digital_tutor.runtime_config import reply_scope
from digital_tutor.storage import encode
from digital_tutor.wechat import read_messages


class MessageCursorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=Application(Path(self.tmp.name)/'test.sqlite',
            Clock('2026-10-04T10:00:00+08:00'),mode='passive',strict_runtime=True)
        for family in ('f','g'):self.app.create_family(family,'synthetic-'+family,'mock:'+family)
        self.admin={f:self.app.open_session(f,'admin') for f in ('f','g')};self.sequence=0
        self.queue=JobQueue(self.app)

    def tearDown(self):self.app.close();self.tmp.cleanup()
    def key(self):self.sequence+=1;return 'cursor-'+str(self.sequence)

    def incoming(self,body='Synthetic natural question',family='f'):
        return self.app.ingest_message(self.admin[family],{'external_id':self.key(),'body':body,
            'occurred_at':self.app.clock.iso()})['data']

    def outgoing_fixture(self,family='f'):
        # Explicit historical mock channel output fixture, no sending action is being tested.
        mid=self.key()
        with self.app.store.transaction() as db:
            record=self.app.store.put('message',mid,family,{'external_id':mid,'body':'Historical mock reply',
                'occurred_at':self.app.clock.iso(),'direction':'outbound','source_kind':'mock_wechat',
                'interpreted':True,'intents':[]})
            db.execute('INSERT INTO message_index(message_id,family_id,external_id,direction) VALUES(?,?,?,?)',
                       (mid,family,mid,'outbound'))
        return record

    def claim(self,family='f'):
        claim=self.queue.claim(self.key(),lease_seconds=60)
        self.assertIsNotNone(claim);self.assertEqual(claim['family_id'],family)
        return claim,self.queue.session(claim)

    def cursors(self,family='f'):return self.app.store.list('runtime_cursor',family)
    def cursor(self,direction='all',family='f'):
        return next((c for c in self.cursors(family) if c['direction']==direction),None)

    def test_all_covers_complete_snapshot_ignoring_limit(self):
        for _ in range(5):self.incoming()
        _,token=self.claim();result=read_messages(self.app,token,{'mode':'all','limit':1})
        self.assertEqual(result['returned_count'],5)
        self.assertEqual(self.cursor()['after_seq'],result['messages'][-1]['seq'])
        self.assertFalse(self.cursor()['read_marks_answered'])

    def test_since_pages_extend_contiguous_prefix_with_current_record_version(self):
        for _ in range(5):self.incoming()
        _,token=self.claim();after=0;seen=[];versions=[]
        while True:
            result=read_messages(self.app,token,{'mode':'since','after_seq':after,'limit':2})
            seen.extend(m['id'] for m in result['messages']);after=result['next_cursor']
            versions.append(self.cursor()['version'])
            if not result['has_more']:break
        self.assertEqual(len(set(seen)),5);self.assertEqual(versions,[1,2,3])
        self.assertEqual(self.cursor()['after_seq'],after)

    def test_all_after_partial_since_updates_with_expected_version(self):
        for _ in range(4):self.incoming()
        _,token=self.claim();read_messages(self.app,token,{'mode':'since','limit':1})
        self.assertEqual(self.cursor()['version'],1)
        result=read_messages(self.app,token,{'mode':'all'})
        self.assertEqual(self.cursor()['version'],2);self.assertEqual(self.cursor()['after_seq'],result['next_cursor'])

    def test_latest_does_not_claim_unread_prefix(self):
        for _ in range(5):self.incoming()
        _,token=self.claim();latest=read_messages(self.app,token,{'mode':'latest','limit':2})
        self.assertTrue(latest['has_older']);self.assertEqual(self.cursors(),[])
        read_messages(self.app,token,{'mode':'since','after_seq':latest['next_cursor'],'limit':10})
        self.assertEqual(self.cursors(),[])
        first=read_messages(self.app,token,{'mode':'since','after_seq':0,'limit':2})
        self.assertEqual(self.cursor()['after_seq'],first['next_cursor'])

    def test_latest_preserves_existing_cursor(self):
        for _ in range(5):self.incoming()
        _,token=self.claim();read_messages(self.app,token,{'mode':'since','limit':1})
        cursor=self.cursor();read_messages(self.app,token,{'mode':'latest','limit':5})
        self.assertEqual(self.cursor(),cursor)

    def test_jumped_since_never_advances_prefix(self):
        for _ in range(6):self.incoming()
        _,token=self.claim();read_messages(self.app,token,{'mode':'since','limit':1});cursor=self.cursor()
        result=read_messages(self.app,token,{'mode':'since','after_seq':3,'limit':2})
        self.assertEqual(result['returned_count'],2);self.assertEqual(self.cursor(),cursor)

    def test_overlapping_since_can_extend_but_never_move_backward(self):
        for _ in range(5):self.incoming()
        _,token=self.claim();read_messages(self.app,token,{'mode':'since','limit':3});cursor=self.cursor()
        read_messages(self.app,token,{'mode':'since','after_seq':0,'limit':1})
        self.assertEqual(self.cursor(),cursor)
        result=read_messages(self.app,token,{'mode':'since','after_seq':2,'limit':3})
        self.assertEqual(self.cursor()['after_seq'],result['next_cursor']);self.assertEqual(self.cursor()['version'],2)

    def test_direction_cursors_are_independent_and_outbound_cannot_create_input_cursor(self):
        self.incoming();self.outgoing_fixture();self.incoming();self.outgoing_fixture()
        _,token=self.claim();inbound=read_messages(self.app,token,{'mode':'all','direction':'inbound'})
        self.assertEqual(self.cursor('inbound')['after_seq'],inbound['next_cursor']);self.assertIsNone(self.cursor('all'))
        saved=self.cursor('inbound');read_messages(self.app,token,{'mode':'all','direction':'outbound'})
        self.assertEqual(self.cursors(),[saved])
        all_read=read_messages(self.app,token,{'mode':'all','direction':'all'})
        self.assertGreater(self.cursor()['after_seq'],saved['after_seq']);self.assertEqual(self.cursor('inbound'),saved)
        self.assertEqual(all_read['returned_count'],4)

    def test_family_sequence_gaps_do_not_merge_family_cursors(self):
        self.incoming(family='f');self.incoming(family='g');self.incoming(family='f');self.incoming(family='g')
        _,token_f=self.claim('f');_,token_g=self.claim('g')
        f=read_messages(self.app,token_f,{'mode':'all'});g=read_messages(self.app,token_g,{'mode':'all'})
        self.assertEqual({m['body'] for m in f['messages']},{'Synthetic natural question'})
        self.assertTrue(set(m['id'] for m in f['messages']).isdisjoint(m['id'] for m in g['messages']))
        self.assertEqual(self.cursor(family='f')['after_seq'],3);self.assertEqual(self.cursor(family='g')['after_seq'],4)
        context=build_context(self.app,token_f)
        self.assertEqual(context['message_read_cursors'],self.cursors('f'))

    def test_empty_since_and_identical_all_do_not_increment_any_version(self):
        self.incoming();_,token=self.claim();read_messages(self.app,token,{'mode':'all'})
        cursor=self.cursor();version=self.app.store.family('f')['version']
        history=self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0]
        read_messages(self.app,token,{'mode':'since','after_seq':cursor['after_seq']})
        read_messages(self.app,token,{'mode':'all'})
        self.assertEqual(self.cursor(),cursor);self.assertEqual(version,self.app.store.family('f')['version'])
        self.assertEqual(history,self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0])

    def test_empty_all_cannot_forge_prefix_using_ignored_after_seq(self):
        self.incoming();_,token=self.claim()
        # Inbound-only read has no records in a new empty host fixture direction snapshot.
        with self.app.store.transaction() as db:
            db.execute("UPDATE message_index SET direction='outbound' WHERE family_id='f'")
            message=self.app.store.list('message','f')[0];message['direction']='outbound'
            self.app.store.put('message',message['id'],'f',message,message['version'])
        read_messages(self.app,token,{'mode':'all','direction':'inbound','after_seq':9999})
        self.assertEqual(self.cursors(),[])

    def test_read_bookkeeping_does_not_change_family_or_answer_responsibility(self):
        msg=self.incoming();service=self.app.open_session('f','service')
        self.app.interpret(service,{'request_id':self.key(),'message_id':msg['id'],'expected_version':msg['version'],
            'intents':[{'intent_id':'question','text':'A question','requires_reply':True}]})
        before=self.app.store.get('message',msg['id'],'f');scope=reply_scope(self.app,'f');version=self.app.store.family('f')['version']
        claim,token=self.claim();read_messages(self.app,token,{'mode':'all'})
        self.assertEqual(before,self.app.store.get('message',msg['id'],'f'))
        self.assertEqual(scope,reply_scope(self.app,'f'));self.assertEqual(version,self.app.store.family('f')['version'])
        self.queue.finish(claim,'waiting','KNOWLEDGE_PENDING')
        self.assertIsNone(self.queue.claim('no-self-wake'))

    def test_admin_unleased_service_and_legacy_reads_do_not_forge_host_cursor(self):
        self.incoming();read_messages(self.app,self.admin['f'],{'mode':'all'})
        read_messages(self.app,self.app.open_session('f','service'),{'mode':'all'})
        self.assertEqual(self.cursors(),[])
        _,token=self.claim();self.app.strict_runtime=False;read_messages(self.app,token,{'mode':'all'})
        self.assertEqual(self.cursors(),[])

    def test_teacher_draft_has_no_text_reply_capability(self):
        msg=self.incoming()
        with self.app.store.transaction():
            job=self.app.store.get('job','message:'+msg['id'],'f');job['output_mode']='teacher_draft'
            self.app.store.put('job',job['id'],'f',job,job['version'])
        _,token=self.claim();context=build_context(self.app,token)
        self.assertFalse(context['run_capabilities']['text_reply'])
        self.assertEqual(context['run_contract']['output_mode'],'teacher_draft')

    def test_ordinary_service_still_has_text_reply_capability(self):
        self.incoming();_,token=self.claim()
        self.assertTrue(build_context(self.app,token)['run_capabilities']['text_reply'])

    def test_failed_oversized_all_does_not_write_checkpoint(self):
        self.incoming();_,token=self.claim()
        with self.app.store.transaction() as db:
            for n in range(10000):
                mid='historical-'+str(n)
                db.execute('INSERT INTO records VALUES(?,?,?,?,?)',('message',mid,'f',1,encode({
                    'external_id':mid,'body':'Historical incoming','direction':'inbound','occurred_at':self.app.clock.iso(),
                    'interpreted':False,'intents':[]})))
                db.execute('INSERT INTO message_index(message_id,family_id,external_id,direction) VALUES(?,?,?,?)',(mid,'f',mid,'inbound'))
        with self.assertRaises(BusinessError) as caught:read_messages(self.app,token,{'mode':'all'})
        self.assertEqual(caught.exception.code,'MESSAGE_RESULT_TOO_LARGE');self.assertEqual(self.cursors(),[])
