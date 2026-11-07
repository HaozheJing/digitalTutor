import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sql_tools as s
import message_hooks as h
import wechat_test as w
import emoji_assets as a
import emoji_policy as policy
import emoji_knowledge as knowledge


class EmojiPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'config').mkdir();(self.root/'runtime').mkdir()
        for key,value in [('R',self.root),('DB',self.root/'runtime/live.sqlite')]:
            p=patch.object(s,key,value);p.start();self.addCleanup(p.stop)
        p=patch.object(w,'config',return_value=None);p.start();self.addCleanup(p.stop)
        with sqlite3.connect(s.DB) as c:
            c.executescript((Path(__file__).parent/'schema.sql').read_text())
            c.execute("INSERT INTO metadata VALUES('test_mode',?)",(json.dumps({'run_id':'scope-one','uid':'family-one'}),))
            h.tables(c)
        w.initialize();(self.root/'config/message-hooks.json').write_text('{"enabled":true}')
        self.card={'asset_id':'one','image_sha256':'a'*64,'status':'approved','caption':'真棒','suitable':'具体进展','avoid':['投诉']}
        self.raw='# card\n```json\n'+json.dumps(self.card)+'\n```\n'
        self.uri='viking://resources/a-test/one/card.md'
        self.registry={'version':'v1','allowed_hosts':['assets.example.test'],'assets':[{'asset_id':'one','sha256':'a'*64,'status':'approved','reviewed_by':'test',
            'url':'https://assets.example.test/one.gif','knowledge':{'uri':self.uri,'image_uri':'viking://resources/a-test/one/image.gif','sha256':hashlib.sha256(self.raw.encode()).hexdigest()}}]}
        self.refs=[{'asset_id':'one','version':'v1'}];self.write_registry()

    def write_registry(self):
        (self.root/'config/emoji-assets.json').write_text(json.dumps(self.registry))

    def begin(self,name):return h.begin('event',name)['token']

    def create(self,key,refs=None,token=None):
        token=token or self.begin(key)
        with h.authority(token),a.selection(refs or []),sqlite3.connect(s.DB) as c,c:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,'测试文字','queued',s.now(),'test_wechat_pending'))
            w.queue_text(c,key,'测试文字')
        return token

    def status(self,key,value):
        with sqlite3.connect(s.DB) as c,c:c.execute('UPDATE deliveries SET status=? WHERE action_key=?',(value,key))

    def mark_image(self,key,status):
        ident='dsh-'+hashlib.sha256(('scope-one:'+key+':emoji:0').encode()).hexdigest()[:40]
        with sqlite3.connect(s.DB) as c,c:c.execute('INSERT INTO test_outbox(id,body,status,request_id,error,created_at,updated_at,message_type) VALUES(?,?,?,?,?,?,?,?)',(ident,'https://assets.example.test/one.gif',status,'request',None,s.now(),s.now(),5))

    def test_two_images_rejected_before_any_delivery(self):
        with self.assertRaisesRegex(ValueError,'quantity_limit'):self.create('one',self.refs*2)
        self.assertEqual(s.read('SELECT * FROM deliveries'),[])

    def test_policy_rejection_is_actionable_at_model_tool_boundary(self):
        import mcp_server,experiment_runtime,agent_plan
        with patch.object(experiment_runtime,'verify',return_value=None),patch.object(mcp_server,'_generation_call') as dispatch:
            with self.assertRaisesRegex(agent_plan.ServiceValidationError,'emoji_quantity_limit'):
                mcp_server.business_call('reply_to_message',{'emoji_refs':self.refs*2})
            dispatch.assert_not_called()
            dispatch.side_effect=policy.PolicyViolation('emoji_frequency_limit: 本轮请仅回复文字')
            with self.assertRaisesRegex(agent_plan.ServiceValidationError,'emoji_frequency_limit'):
                mcp_server.business_call('reply_to_message',{})

    def test_pending_emoji_reserves_next_turn_and_rejection_rolls_back(self):
        self.create('first',self.refs)
        with self.assertRaisesRegex(ValueError,'frequency_limit'):self.create('second',self.refs)
        self.assertEqual(len(s.read('SELECT * FROM deliveries')),1)

    def test_only_completed_plain_text_releases_frequency(self):
        self.create('first',self.refs);self.mark_image('first','sent');self.status('first','sent')
        self.create('plain')
        with self.assertRaisesRegex(ValueError,'frequency_limit'):self.create('blocked',self.refs)
        self.status('plain','sent');self.create('third',self.refs)

    def test_unknown_image_survives_restart_and_interruption(self):
        self.create('first',self.refs);self.mark_image('first','unknown');self.status('first','superseded')
        # A separate connection is what a restarted worker sees; no in-memory counter.
        with sqlite3.connect(s.DB) as c:
            with self.assertRaisesRegex(ValueError,'frequency_limit'):policy.check(c)
        with self.assertRaisesRegex(ValueError,'frequency_limit'):self.create('second',self.refs)

    def test_interrupted_unsent_draft_does_not_consume_frequency(self):
        self.create('first',self.refs);self.status('first','superseded');self.create('second',self.refs)

    def test_same_turn_cannot_split_extra_image_after_plain_message(self):
        token=self.begin('same-owner');self.create('first',self.refs,token);self.mark_image('first','sent');self.status('first','sent')
        self.create('plain',token=token);self.status('plain','sent')
        with self.assertRaisesRegex(ValueError,'same_turn_limit'):self.create('third',self.refs,token)

    def test_new_run_does_not_inherit_other_family_db_scope(self):
        self.create('first',self.refs)
        with sqlite3.connect(s.DB) as c,c:c.execute("UPDATE metadata SET value=? WHERE key='test_mode'",(json.dumps({'run_id':'scope-two','uid':'family-two'}),))
        self.create('second',self.refs)

    def test_trusted_reconstructed_history_blocks_first_image(self):
        (self.root/'config/emoji-history.json').write_text('[{"has_emoji":true}]')
        with self.assertRaisesRegex(ValueError,'frequency_limit'):self.create('first',self.refs)
        self.create('plain');self.status('plain','sent');self.create('next',self.refs)

    def enable_retrieval(self):
        self.registry['retrieval']={'uri':'viking://resources/a-test','revision':'v1'};self.write_registry()

    def test_selected_asset_must_be_retrieved_in_current_generation(self):
        self.enable_retrieval();token=self.begin('one')
        with self.assertRaisesRegex(ValueError,'search_required'):self.create('first',self.refs,token)
        policy.grant([{'asset_id':'one','version':'v1','document_sha256':self.registry['assets'][0]['knowledge']['sha256']}],'进展')
        self.create('first',self.refs,token)

    def test_old_generation_retrieval_cannot_authorize_new_reply(self):
        self.enable_retrieval();self.begin('old');policy.grant([{'asset_id':'one','version':'v1','document_sha256':self.registry['assets'][0]['knowledge']['sha256']}],'进展')
        with self.assertRaisesRegex(ValueError,'search_required'):self.create('new',self.refs)

    def test_live_search_reads_pinned_body_and_hides_full_catalog(self):
        self.enable_retrieval();self.begin('one')
        self.assertEqual(a.catalog()['assets'],[])
        with patch.object(knowledge,'cli',side_effect=[{'resources':[{'uri':self.uri}]},self.raw]):
            result=knowledge.search('孩子主动读完故事')
        self.assertEqual(result['status'],'ok');self.assertTrue(result['selection_authorized_for_current_turn'])
        self.create('first',self.refs,h.AUTHORITY.get() or s.read("SELECT token FROM message_hook_generations ORDER BY rowid DESC LIMIT 1")[0]['token'])

    def test_changed_card_no_match_and_ov_failure_do_not_grant(self):
        self.enable_retrieval();token=self.begin('one')
        with patch.object(knowledge,'cli',side_effect=[{'resources':[{'uri':self.uri}]},self.raw+'changed']):
            self.assertEqual(knowledge.search('进展')['status'],'changed')
        with patch.object(knowledge,'cli',return_value={'resources':[{'uri':'viking://resources/other/secret'}]}):
            self.assertEqual(knowledge.search('进展')['status'],'no_match')
        with patch.object(knowledge,'cli',side_effect=RuntimeError('offline')):
            self.assertEqual(knowledge.search('进展')['status'],'unavailable')
        with self.assertRaisesRegex(ValueError,'search_required'):self.create('first',self.refs,token)


if __name__=='__main__':unittest.main()
