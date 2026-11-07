import copy,json,tempfile,unittest,sqlite3
from pathlib import Path
from family_lab import Lab,encode
from family_lab_contacts import Contacts,contact_key,authorized,setting
from family_lab_round import activate


class ContactTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
        self.lab=Lab(self.home/'control.sqlite');self.lab.initialize()
        self.families=[{'family_id':'F0'+str(i),'student':'student'+str(i),'workspace':str(self.home/str(i))} for i in range(1,6)]
        for f in self.families:
            path=Path(f['workspace'])/'runtime/fixtures/mock_uid_00001_first_month.json';path.parent.mkdir(parents=True)
            path.write_text(encode({'learning_daily':[],'enrollments':[]}))
        self.run=self.lab.create_run(self.families,'2025-10-24T17:30:00+08:00','wechat')
        self.legacy={'orgId':'org','imBotId':'bot','imContactId':'old','chatId':'old-chat'}
        (self.home/'registered-contacts.private.json').write_text(encode({'北斋先生':self.legacy}))
        self.rows=[{'name':letter*2,'imContactId':'contact'+letter,'botInfo':{'imBotId':'bot'},'friendshipStatus':3,'chatId':'chat'+letter} for letter in 'ABCDE']
        self.calls=[];self.fail_chat=None
        self.contacts=Contacts(self.home,self.lab,self.fetch)

    def fetch(self,path,query):
        self.calls.append((path,query))
        if path=='/api/v2/customer/list':return {'errcode':0,'data':copy.deepcopy(self.rows),'total':len(self.rows)}
        self.assertEqual(path,'/api/v2/getChatId')
        return {'errcode':0,'chatId':None if query['imContactId']==self.fail_chat else 'chat'+query['imContactId'][-1]}

    def mapping(self):return {f['family_id']:contact_key('org','bot',r['imContactId']) for f,r in zip(self.families,self.rows)}
    def versions(self):return {b['family_id']:b['version'] for b in self.contacts.snapshot()['bindings']}
    def apply(self,mapping=None,versions=None):return self.contacts.apply(self.run,mapping or self.mapping(),versions or self.versions())

    def test_refresh_discovers_without_message_and_grants_nothing(self):
        result=self.contacts.refresh();self.assertEqual(len(result['items']),5)
        self.assertEqual(self.lab.read('SELECT count(*) n FROM ingress')[0]['n'],0)
        self.assertEqual(self.lab.read('SELECT count(*) n FROM outbox')[0]['n'],0)
        self.assertFalse(authorized(self.lab,dict(orgId='org',imBotId='bot',imContactId='contactA',chatId='chatA')))
        self.assertNotIn('imContactId',result['items'][0]);self.assertNotIn('chatId',result['items'][0])

    def test_five_friends_bind_with_no_prior_chat_no_models_or_send(self):
        result=self.apply();self.assertEqual(result['real_families'],5);self.assertTrue(self.lab.current()['paused'])
        self.assertEqual(self.lab.read('SELECT count(*) n FROM model_attempts')[0]['n'],0)
        self.assertEqual(self.lab.read('SELECT count(*) n FROM outbox')[0]['n'],0)
        self.assertTrue(authorized(self.lab,dict(orgId='org',imBotId='bot',imContactId='contactA',chatId='chatA')))
        self.assertFalse(authorized(self.lab,self.legacy))

    def test_duplicate_nickname_remains_distinct_but_same_identity_rejected(self):
        self.rows[1]['name']=self.rows[0]['name'];self.apply()
        mapping=self.mapping();mapping['F02']=mapping['F01']
        with self.assertRaisesRegex(ValueError,'不能同时'):self.apply(mapping)

    def test_stale_versions_fail_atomically(self):
        versions=self.versions();self.apply()
        with self.assertRaisesRegex(ValueError,'绑定已被'):self.apply(versions=versions)
        self.assertEqual(len([b for b in self.contacts.snapshot()['bindings'] if b['mode']=='wechat']),5)

    def test_missing_chat_or_changed_friend_does_not_partially_bind(self):
        self.fail_chat='contactD'
        with self.assertRaisesRegex(ValueError,'会话标识'):self.apply()
        self.assertTrue(all(b['mode']=='simulation' for b in self.contacts.snapshot()['bindings']))
        self.fail_chat=None;mapping=self.mapping();self.rows.pop()
        with self.assertRaisesRegex(ValueError,'已不在'):self.apply(mapping)
        self.assertTrue(authorized(self.lab,self.legacy))

    def test_foreign_bot_or_nonfriend_cannot_be_bound(self):
        mapping=self.mapping();self.rows[1]['botInfo']['imBotId']='foreign';self.rows[2]['friendshipStatus']=2
        self.assertEqual(len(self.contacts.refresh()['items']),3)
        with self.assertRaisesRegex(ValueError,'已不在'):self.apply(mapping)

    def test_rebinding_family_with_history_requires_new_round(self):
        self.apply();self.lab.ingest(self.run,'F01','old','message',{'text':'private student history'})
        mapping=self.mapping();mapping['F01'],mapping['F02']=mapping['F02'],mapping['F01']
        with self.assertRaisesRegex(ValueError,'已有本轮体验记录'):self.apply(mapping)
        self.assertEqual(self.contacts.snapshot()['bindings'][0]['name'],'AA')

    def test_used_family_same_identity_is_idempotent(self):
        self.apply();self.lab.ingest(self.run,'F01','old','message',{'text':'history'})
        versions=self.versions();self.apply();self.assertEqual(self.versions(),versions)

    def test_bind_requires_paused_and_idle(self):
        self.lab.control(self.run,'F01','experiment_pause',False)
        with self.assertRaisesRegex(ValueError,'暂停'):self.apply()
        self.lab.control(self.run,'F01','experiment_pause',True)
        self.lab.model_enqueue(self.run,'F02','task','background','m')
        with self.assertRaisesRegex(ValueError,'等待模型'):self.apply()

    def test_contact_chat_id_conflict_rejects(self):
        self.rows[0]['chatId']='different'
        with self.assertRaisesRegex(ValueError,'不一致'):self.apply()

    def test_new_round_inherits_roster_but_simulation_round_never_binds(self):
        self.apply()
        for mode in ('wechat','simulation'):
            newlab=Lab(self.home/(mode+'.sqlite'));newlab.initialize();new=newlab.create_run(self.families,'2025-10-24T17:30:00+08:00',mode)
            prepared=(newlab.current(),newlab.read('SELECT * FROM families'),newlab.read('SELECT * FROM bindings'))
            activate(self.lab,{'from_run':self.run,'id':'test-'+mode},prepared);self.run=new
            current=self.lab.read('SELECT mode FROM bindings WHERE run_id=?',(new,))
            self.assertTrue(all(x['mode']==mode for x in current))
            self.assertEqual(self.lab.read('SELECT count(*) n FROM outbox')[0]['n'],0)

    def test_same_contact_swaps_clean_families_atomically(self):
        self.apply();mapping=self.mapping();mapping['F01'],mapping['F02']=mapping['F02'],mapping['F01'];self.apply(mapping)
        self.assertEqual([b['name'] for b in self.contacts.snapshot()['bindings']][:2],['BB','AA'])

    def test_pagination_fails_closed_without_overwriting_cache(self):
        self.contacts.refresh();before=self.contacts.snapshot()['updated']
        self.contacts.fetch=lambda *a:{'errcode':0,'data':self.rows,'next_seq':'repeated'}
        with self.assertRaisesRegex(ValueError,'分页未前进'):self.contacts.refresh()
        self.assertEqual(self.contacts.snapshot()['updated'],before)


if __name__=='__main__':unittest.main()
