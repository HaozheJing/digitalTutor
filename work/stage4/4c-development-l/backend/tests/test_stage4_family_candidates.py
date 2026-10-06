"""Program contract tests only; no model actions or API acceptance claims."""
import unittest
import test_stage4_candidates as baseline
from digital_tutor.family_candidates import propose_makeup,explicit_request
from digital_tutor.candidates import refresh_candidates
from digital_tutor.domain import BusinessError,timestamp
from digital_tutor.tools import ToolRegistry


class FamilyCandidateTests(unittest.TestCase):
    setUp=baseline.TrustedCandidateTests.setUp
    tearDown=baseline.TrustedCandidateTests.tearDown
    key=baseline.TrustedCandidateTests.key
    learning_source=baseline.TrustedCandidateTests.learning_source
    rejected=baseline.TrustedCandidateTests.rejected
    def setup_binding(self):
        source=self.learning_source()
        with self.app.store.transaction():
            # The confirmed learning binding is reusable; turn off the unrelated simulated rule.
            source['enabled']=False
            self.app.store.put('candidate_source',source['id'],'f',source,source['version'])

    def message(self,body='请帮我安排补读第1天。',family='f'):
        token=self.admin if family=='f' else self.app.open_session(family,'admin')
        message=self.app.ingest_message(token,{'external_id':self.key(),'body':body,
                    'occurred_at':'2026-10-04T09:00:00+08:00'})['data']
        service=self.app.open_session(family,'service')
        self.app.interpret(service,{'request_id':self.key(),'message_id':message['id'],
            'expected_version':message['version'],'intents':[{
                'intent_id':'arrangement','text':body,'requires_reply':True}]})
        return self.app.store.get('message',message['id'],family)

    def proposal(self,msg,**overrides):
        token=self.app.open_session('f','service')
        args={'request_id':self.key(),'expected_family_version':self.app.store.family('f')['version'],
              'source_message_id':msg['id'],'source_version':msg['version'],'source_excerpt':msg['body'],
              'batch_id':'synthetic-batch','day':1,**overrides}
        return token,args

    def submit_request(self,msg):
        return propose_makeup(self.app,*self.proposal(msg))['data']

    def command(self):
        token,args=baseline.TrustedCandidateTests.command(self)
        args['plan_refs']=list(dict.fromkeys(args['plan_refs']))
        candidates={c['id']:c for c in self.app.store.list('candidate','f')}
        args['pending_decisions']=[{'input_id':candidates[d['candidate_id']]['pending_id'],
            'disposition':d['disposition'],'event_ref':d['event_ref'],'reason':'Program fixture decision'}
            for d in args['candidate_decisions'] if candidates[d['candidate_id']].get('pending_id')]
        # A new real source can arrange the canonical old Event without changing
        # its deadline/completion. Only fixture planning is supplied here.
        updates={}
        for decision in args['candidate_decisions']:
            candidate=candidates[decision['candidate_id']]
            if candidate.get('pending_id') and candidate.get('existing_event_id'):
                event=self.app.event('f',candidate['existing_event_id'])
                updates[event['id']]={'event_id':event['id'],'expected_version':event['version'],
                    'changes':{'next_action':baseline.fixture_step(self.app,candidate)},
                    'evidence_refs':[ref for ref in candidate['source_refs'] if ref['kind']=='message']}
        if updates:args['event_updates']=list(updates.values())
        return token,args

    def test_explicit_request_creates_fixed_goal_and_adopts_pending(self):
        self.setup_binding();pending=self.submit_request(self.message())
        token,args=self.command();self.app.commit_daily(token,args)
        event=self.app.store.list('event','f')[0]
        self.assertEqual(event['completion']['lesson_refs'],['synthetic-lesson-1'])
        self.assertIsNone(event['due_at'])
        self.assertEqual(self.app.store.get('pending',pending['id'],'f')['event_id'],event['id'])

    def test_date_precision_and_original_message_anchor(self):
        self.setup_binding();message=self.message('请帮我们安排明天补读第1天。')
        self.app.clock.fixed=timestamp('2026-10-06T09:00:00+08:00')
        # Parsing uses original message occurrence, independent of eventual processing day.
        parsed=explicit_request(message)
        self.assertEqual(parsed['requested_time']['value'],'2026-10-05')
        self.assertEqual(parsed['requested_time']['precision'],'date')
        self.assertIsNone(parsed['due_at'])

    def test_schedule_is_not_completion_deadline(self):
        message={'body':'请帮我安排明天下午四点补读第1天。','occurred_at':'2026-10-04T09:00:00+08:00'}
        parsed=explicit_request(message)
        self.assertEqual(parsed['requested_time']['value'],'2026-10-05T16:00:00+08:00')
        self.assertEqual(parsed['requested_time']['meaning'],'schedule');self.assertIsNone(parsed['due_at'])

    def test_explicit_completion_deadline_preserved(self):
        self.setup_binding();pending=self.submit_request(self.message('请帮我安排10月5日下午四点前补完第1天。'))
        self.assertEqual(pending['original_deadline'],'10月5日下午四点前')
        self.assertEqual(pending['requested_time']['value'],'2026-10-05T16:00:00+08:00')
        self.assertEqual(pending['requested_time']['precision'],'datetime')

    def test_wrong_proposed_day_cannot_become_trusted(self):
        self.setup_binding();token,args=self.proposal(self.message(),day=2)
        with self.assertRaises(BusinessError) as caught:propose_makeup(self.app,token,args)
        self.assertEqual(caught.exception.code,'LESSON_SOURCE_MISMATCH');self.assertEqual(self.app.store.list('pending','f'),[])

    def test_missing_and_cross_family_source_cannot_create(self):
        self.setup_binding();foreign=self.message(family='g')
        for source in [foreign['id'],'no-such-message']:
            with self.subTest(source=source):
                token,args=self.proposal(foreign,source_message_id=source)
                with self.assertRaises(BusinessError):propose_makeup(self.app,token,args)
        self.assertEqual(self.app.store.list('pending','f'),[])

    def test_excerpt_cannot_cut_negation_or_condition(self):
        self.setup_binding();msg=self.message('不要补读第1天。请帮我安排补读第1天。')
        token,args=self.proposal(msg,source_excerpt='请帮我安排补读第1天。')
        with self.assertRaises(BusinessError) as caught:propose_makeup(self.app,token,args)
        self.assertEqual(caught.exception.code,'SOURCE_EXCERPT_INCOMPLETE')

    def test_ambiguous_and_negative_requests_not_adopted(self):
        self.setup_binding()
        for body in ['请帮我安排补读某一天。','请帮我安排补读第1天和第2天。',
                     '请不要帮我安排补读第1天。','如果有空请帮我安排补读第1天。',
                     '请帮我安排明天四点补读第1天。','请帮我安排明天补读第1天，顺便报名。']:
            with self.subTest(body=body):
                with self.assertRaises(BusinessError):self.submit_request(self.message(body))
        self.assertEqual(self.app.store.list('pending','f'),[])
        self.assertEqual(self.app.store.list('event','f'),[])

    def test_two_confirmed_courses_cannot_be_cherry_picked(self):
        self.setup_binding();binding=self.app.store.list('course_binding','f')[0]
        with self.app.store.transaction():
            second={**binding,'batch_id':'second','course_id':'second-course'}
            self.app.store.put('course_binding','second-binding','f',second)
        with self.assertRaises(BusinessError) as caught:self.submit_request(self.message())
        self.assertEqual(caught.exception.code,'COURSE_BINDING_AMBIGUOUS')

    def test_source_version_must_be_current(self):
        self.setup_binding();token,args=self.proposal(self.message(),source_version=999)
        with self.assertRaises(BusinessError) as caught:propose_makeup(self.app,token,args)
        self.assertEqual(caught.exception.code,'EVIDENCE_SUPERSEDED')

    def test_reply_accounting_version_change_does_not_destroy_source(self):
        self.setup_binding();msg=self.message();self.submit_request(msg)
        with self.app.store.transaction():
            msg['intents'][0]['partial_reply_operation_refs']=['synthetic-receipt-accounting']
            self.app.store.put('message',msg['id'],'f',msg,msg['version'])
        token,args=self.command();self.assertEqual(len(args['new_events']),1)
        self.app.commit_daily(token,args)

    def test_binding_correction_blocks_candidate_without_crashing_daily(self):
        self.setup_binding();self.submit_request(self.message())
        binding=self.app.store.list('course_binding','f')[0]
        with self.app.store.transaction():
            binding['course_id']='changed';self.app.store.put('course_binding',binding['id'],'f',binding,binding['version'])
            refresh_candidates(self.app,'f')
        family_candidate=next(c for c in self.app.store.list('candidate','f') if c.get('pending_id'))
        self.assertEqual(family_candidate['status'],'blocked')

    def test_existing_lesson_event_reused_with_original_deadline(self):
        self.learning_source();token,args=self.command();self.app.commit_daily(token,args)
        original=self.app.store.list('event','f')[0]
        self.app.clock.fixed=timestamp('2026-10-05T09:00:00+08:00')
        self.submit_request(self.message('请帮我安排10月5日下午四点前补完第1天。'))
        token,args=self.command()
        self.assertEqual(args['new_events'],[])
        decision=args['pending_decisions'][0];self.assertEqual(decision['event_ref'],original['id'])
        self.app.commit_daily(token,args)
        self.assertEqual(len(self.app.store.list('event','f')),1)
        self.assertEqual(self.app.store.get('event',original['id'],'f')['due_at'],original['due_at'])

    def test_pending_cannot_target_an_unrelated_event(self):
        self.setup_binding();self.submit_request(self.message());token,args=self.command()
        args['pending_decisions'][0]['event_ref']='different'
        self.rejected(token,args,'PENDING_CANDIDATE_MISMATCH')

    def test_tool_exposes_proposal_not_trust_or_goal_key(self):
        tools=ToolRegistry(self.app,self.app.open_session('f','service')).describe()
        tool=next(t for t in tools if t['name']=='pending.propose_makeup')
        self.assertNotIn('goal_key',tool['input_schema']['properties'])
        self.assertNotIn('confirmed',tool['input_schema']['properties'])
        daily=ToolRegistry(self.app,self.app.open_session('f','planner_daily')).describe()
        self.assertNotIn('pending.propose_makeup',[t['name'] for t in daily])

    def test_changed_label_still_links_same_confirmed_course_identity(self):
        self.learning_source();token,args=self.command();self.app.commit_daily(token,args)
        original=self.app.store.list('event','f')[0]
        with self.app.store.transaction():
            original['goal_key']='legacy-original-goal'
            self.app.store.put('event',original['id'],'f',original,original['version'])
            source=self.app.store.list('candidate_source','f')[0];source['enabled']=False
            self.app.store.put('candidate_source',source['id'],'f',source,source['version'])
        self.app.clock.fixed=timestamp('2026-10-05T09:00:00+08:00')
        self.submit_request(self.message());token,args=self.command()
        self.assertEqual(args['new_events'],[])
        self.assertEqual(args['pending_decisions'][0]['event_ref'],original['id'])
        self.app.commit_daily(token,args);self.assertEqual(len(self.app.store.list('event','f')),1)

    def test_adoption_scan_does_not_wake_itself(self):
        self.setup_binding();self.submit_request(self.message());token,args=self.command();self.app.commit_daily(token,args)
        version=self.app.store.family('f')['version']
        with self.app.store.transaction():refresh_candidates(self.app,'f')
        self.assertEqual(version,self.app.store.family('f')['version'])

    def test_this_week_never_silently_rolls_into_next_week(self):
        parsed=explicit_request({'body':'请帮我安排本周一补读第1天。','occurred_at':'2026-10-04T09:00:00+08:00'})
        self.assertEqual(parsed['requested_time']['value'],'2026-09-28')

    def test_impossible_meridiem_not_accepted(self):
        with self.assertRaises(BusinessError):
            explicit_request({'body':'请帮我安排明天上午20点补读第1天。','occurred_at':'2026-10-04T09:00:00+08:00'})

    def test_disabled_family_source_never_becomes_ready(self):
        self.setup_binding();self.submit_request(self.message())
        source=next(s for s in self.app.store.list('candidate_source','f') if s['source_kind']=='family_makeup_request')
        with self.app.store.transaction():
            source['enabled']=False;self.app.store.put('candidate_source',source['id'],'f',source,source['version'])
            refresh_candidates(self.app,'f')
        candidate=next(c for c in self.app.store.list('candidate','f') if c.get('pending_id'))
        self.assertEqual(candidate['status'],'blocked');self.assertEqual(candidate['gaps'],['source_disabled'])

    def test_date_only_completion_keeps_precision_and_original_text(self):
        self.setup_binding();pending=self.submit_request(self.message('请帮我安排明天前补完第1天。'))
        self.assertEqual(pending['original_deadline'],'明天前')
        self.assertEqual(pending['requested_time']['precision'],'date')
        token,args=self.command();self.app.commit_daily(token,args)
        event=self.app.store.list('event','f')[0]
        self.assertEqual(event['original_deadline'],'明天前')
        self.assertEqual(event['requested_time']['value'],'2026-10-05')
        self.assertIsNone(event['due_at'])

    def test_original_text_and_precision_survive_creation(self):
        self.setup_binding();self.submit_request(self.message('请帮我安排10月5日下午四点前补完第1天。'))
        token,args=self.command();self.app.commit_daily(token,args)
        event=self.app.store.list('event','f')[0]
        self.assertEqual(event['original_deadline'],'10月5日下午四点前')
        self.assertEqual(event['requested_time']['precision'],'datetime')
        self.assertEqual(event['due_at'],'2026-10-05T16:00:00+08:00')
        self.assertEqual(event['source_identity']['course_version'],'v1')

    def test_message_body_changed_without_family_bump_rejects_snapshot(self):
        self.setup_binding();message=self.message();self.submit_request(message);token,args=self.command()
        with self.app.store.transaction():
            message['body']='请不要补读第1天。'
            self.app.store.put('message',message['id'],'f',message,message['version'])
        self.rejected(token,args,'STALE_CANDIDATE')

    def test_binding_changed_without_family_bump_rejects_snapshot(self):
        self.setup_binding();self.submit_request(self.message());token,args=self.command()
        binding=self.app.store.list('course_binding','f')[0]
        with self.app.store.transaction():
            binding['course_version']='revised'
            self.app.store.put('course_binding',binding['id'],'f',binding,binding['version'])
        self.rejected(token,args,'STALE_CANDIDATE')

    def test_exact_request_receipt_replays_without_bump(self):
        self.setup_binding();token,args=self.proposal(self.message())
        first=propose_makeup(self.app,token,args);before=self.app.store.family('f')['version']
        replay=propose_makeup(self.app,token,args)
        self.assertEqual(replay['outcome'],'duplicate');self.assertEqual(replay['data'],first['data'])
        self.assertEqual(self.app.store.family('f')['version'],before)

    def test_new_request_id_cannot_wake_same_proposed_source(self):
        self.setup_binding();message=self.message();self.submit_request(message)
        before=self.app.store.family('f')['version']
        with self.assertRaises(BusinessError) as caught:self.submit_request(message)
        self.assertEqual(caught.exception.code,'REQUEST_ALREADY_RECORDED')
        self.assertEqual(self.app.store.family('f')['version'],before)
        self.assertEqual(len(self.app.store.list('pending','f')),1)

    def test_two_sources_same_contract_can_share_one_new_event(self):
        self.setup_binding();first=self.submit_request(self.message());second=self.submit_request(self.message())
        token,args=self.command()
        self.assertEqual(len(args['new_events']),2)
        keep=args['new_events'][0]['client_ref'];args['new_events']=args['new_events'][:1]
        args['candidate_decisions'][1].update(disposition='linked',event_ref=keep)
        args['pending_decisions'][1].update(disposition='linked',event_ref=keep)
        args['plan_refs']=[keep]
        self.app.commit_daily(token,args)
        event=self.app.store.list('event','f')[0]
        self.assertEqual(len(self.app.store.list('event','f')),1)
        self.assertEqual(self.app.store.get('pending',first['id'],'f')['event_id'],event['id'])
        self.assertEqual(self.app.store.get('pending',second['id'],'f')['event_id'],event['id'])
