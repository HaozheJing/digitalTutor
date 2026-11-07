from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch
import voice_context as vc
import voice_understanding as voice

class VoiceContextTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE messages(source_id,speaker,body,created_at,source,visible); CREATE TABLE deliveries(action_key,body,status,created_at); CREATE TABLE preference_forgotten(scope,source_id);')
        self.state={'run_id':'r','uid':'f','as_of':'2025-10-22T20:30:00+08:00'}
    def put(self,ident,text,at='2025-10-22T20:29:00+08:00',role='user',visible=1):
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(ident,role,text,at,'test',visible))
    def context(self):return vc.collect(self.db,'target',self.state,'侨侨')
    def proposal(self,proposed_role,ref,quote,**kw):
        return {'role':proposed_role,'confidence':'high','basis':'explicit_handoff','reason':'current turn handoff','evidence':[{'source':'context','ref':ref,'quote':quote}],**kw}
    def test_cutoff_hidden_forgotten_and_failed_delivery_never_reintroduced(self):
        self.put('old','historical');self.put('forgot','erase me');self.db.execute("INSERT INTO preference_forgotten VALUES('scope','forgot')")
        self.put('hidden','hidden',visible=0)
        self.put('target','[用户发送语音，调用 understand_audio 资产secret]\n附带文字：让侨侨来跟你说。',at=self.state['as_of'])
        self.put('future','future',at='2025-10-22T20:31:00+08:00')
        self.db.execute("INSERT INTO deliveries VALUES('failed','NOT SENT','failed','2025-10-22T20:28:00+08:00')")
        self.db.execute("INSERT INTO deliveries VALUES('sent','老师在听','sent','2025-10-22T20:28:00+08:00')")
        context=self.context();text=json.dumps(context,ensure_ascii=False)
        for omitted in ['erase me','hidden','future','NOT SENT','understand_audio','secret']:self.assertNotIn(omitted,text)
        self.assertIn('老师在听',text);self.assertIn('让侨侨来跟你说',text)
        self.assertNotIn('source_id',json.dumps(vc.public_context(context)))
    def test_context_budget_keeps_target_and_is_bounded(self):
        for i in range(30):self.put(str(i),'字'*2500)
        self.put('target','现在让孩子说',at=self.state['as_of']);c=self.context()
        self.assertLessEqual(len(c['messages']),12);self.assertLessEqual(sum(len(x['content']) for x in c['messages']),8000)
        self.assertTrue(c['selection']['truncated']);self.assertEqual(c['messages'][-1]['source_id'],'target')
    def test_handoff_need_not_match_old_caption_regex(self):
        self.put('m','我把手机递给侨侨了，让他和您聊。')
        self.put('target','[语音消息]',at=self.state['as_of']);context=self.context();ref=context['messages'][0]['ref']
        speaker=vc.assess(self.proposal('child',ref,'我把手机递给侨侨了，让他和您聊。'),context,'',False)
        self.assertEqual(speaker['address_mode'],'child');self.assertEqual(speaker['address'],'侨侨宝贝')
    def test_quote_must_exist_and_cannot_come_from_assistant_or_old_message(self):
        self.put('old','我是侨侨妈妈','2025-10-20T20:29:00+08:00')
        self.put('assistant','侨侨妈妈你好',role='assistant');self.put('target','[语音消息]',at=self.state['as_of'])
        c=self.context()
        for ref,quote in [(c['messages'][0]['ref'],'我是侨侨妈妈'),(c['messages'][1]['ref'],'侨侨妈妈你好'),(c['messages'][2]['ref'],'不存在的介绍')]:
            self.assertEqual(vc.assess(self.proposal('mother',ref,quote),c,'',False)['address_mode'],'neutral')
    def test_low_confidence_multiple_and_audio_without_speech_stay_neutral(self):
        self.put('target','爸爸来和您说',at=self.state['as_of']);c=self.context();ref=c['messages'][0]['ref']
        for changes in [{'confidence':'medium'},{'role':'multiple'},{'basis':'insufficient'}]:
            self.assertEqual(vc.assess(self.proposal('father',ref,'爸爸来和您说',**changes),c,'',False)['address_mode'],'neutral')
        proposal={'role':'father','confidence':'high','basis':'self_identification','evidence':[{'source':'audio','ref':'audio','quote':'我是爸爸'}]}
        self.assertEqual(vc.assess(proposal,c,'',False)['address_mode'],'neutral')
        self.assertEqual(vc.assess(proposal,c,'我是爸爸',True)['address_mode'],'father')
    def test_model_unknown_is_not_overridden_by_old_regex(self):
        self.put('target','[语音]附带文字：我是侨侨妈妈，这条语音是我发的。')
        result={'status':'no_speech','current_speaker':{'address_mode':'neutral','instruction':'uncertain'}}
        with patch.object(voice,'speaker_hint',side_effect=AssertionError('must not override model uncertainty')):
            self.assertEqual(voice.clarify_for_speaker(self.db,result,'target')['current_speaker']['address_mode'],'neutral')

if __name__=='__main__':unittest.main()
