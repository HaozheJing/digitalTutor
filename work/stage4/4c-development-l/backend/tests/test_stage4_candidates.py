"""Candidate contracts: real temporary storage and Worker, no model API."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from digital_tutor.application import Application,Clock
from digital_tutor.candidates import configure_source,refresh_candidates
from digital_tutor.context import build_context
from digital_tutor.course_binding import confirm_course_binding
from digital_tutor.domain import BusinessError
from digital_tutor.jobs import JobQueue
from digital_tutor.scheduler import Scheduler,Worker
from digital_tutor.storage import encode


def fixture_step(app, candidate):
    """A deterministic program proposal, never injected into a model runner."""
    requested = candidate.get('requested_time') or {}
    execute_at = app.clock.iso()
    if requested.get('meaning') == 'schedule':
        if requested.get('precision') == 'datetime':
            execute_at = requested['value']
        elif requested.get('precision') == 'date':
            execute_at = requested['value'] + 'T09:00:00+08:00'
    goal = candidate['completion']['kind'] == 'goal'
    return {'action_kind': 'remind_makeup' if goal else 'deliver_feedback',
            'target_refs': candidate['completion']['lesson_refs'] if goal else candidate['completion']['required_part_ids'],
            'purpose': 'Program fixture advances the unchanged original responsibility.',
            'execute_at': execute_at}


class TrustedCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=Application(Path(self.tmp.name)/'test.sqlite',
            Clock('2026-10-04T09:00:00+08:00'),mode='daily',strict_runtime=True)
        self.app.create_family('f','synthetic-f','mock:f');self.app.create_family('g','synthetic-g','mock:g')
        self.admin=self.app.open_session('f','admin')
        self.sequence=0

    def tearDown(self):self.app.close();self.tmp.cleanup()

    def key(self):self.sequence+=1;return 'r'+str(self.sequence)

    def learning_source(self,completion='incomplete',coverage=True,enabled=True):
        time='2026-10-04T08:30:00+08:00'
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO imports VALUES(?,?)',('synthetic-batch',encode({'batch_id':'synthetic-batch',
                'imported_at':time,'coverage_through':time if coverage else None,
                'source_timezone':'Asia/Shanghai','course_binding':None,'synthetic':True})))
            db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('synthetic-batch','synthetic-f',1,'attended',completion,'unknown','unknown','unknown',None,None,'synthetic-row-1'))
        confirm_course_binding(self.app,self.admin,{'request_id':self.key(),'batch_id':'synthetic-batch',
            'expected_family_version':self.app.store.family('f')['version'],'source_note':'Confirmed synthetic curriculum',
            'course_id':'synthetic-course','course_version':'v1','lesson_map':[{'day':1,'lesson_ref':'synthetic-lesson-1'}],
            'coverage_through':time,'source_timezone':'Asia/Shanghai'})
        return configure_source(self.app,self.admin,{'source_id':'source-makeup','source_kind':'makeup_rule',
            'simulation':True,'confirmed':True,'enabled':enabled,'rule_version':'simulation-rule-v1',
            'active_from':'2026-10-04T08:00:00+08:00','active_until':'2026-10-06T20:00:00+08:00',
            'due_at':'2026-10-06T20:00:00+08:00','batch_id':'synthetic-batch','day':1,
            'required_coverage_at':time})

    def delivery_source(self):
        return configure_source(self.app,self.admin,{'source_id':'source-delivery','source_kind':'periodic_text_delivery',
            'simulation':True,'confirmed':True,'enabled':True,'rule_version':'periodic-v1',
            'active_from':'2026-10-04T08:00:00+08:00','active_until':'2026-10-06T20:00:00+08:00',
            'due_at':'2026-10-05T16:00:00+08:00','contract_id':'synthetic-weekly-feedback',
            'period_start':'2026-09-28T00:00:00+08:00','period_end':'2026-10-04T09:00:00+08:00',
            'required_stage':'delivered','parts':[{'part_id':'participation','media_type':'text','purpose':'参与情况回顾'},
                                             {'part_id':'advice','media_type':'text','purpose':'基于可用证据的文字建议'}]})

    def command(self):
        token=self.app.open_session('f','planner_daily')
        candidates=[c for c in self.app.store.list('candidate','f') if c['status']=='ready']
        new=[];decisions=[];refs=[]
        for i,c in enumerate(candidates):
            ref=c['existing_event_id'] or 'new-'+str(i)
            disposition='linked' if c['existing_event_id'] else 'created'
            decisions.append({'candidate_id':c['id'],'expected_version':c['version'],
                'disposition':disposition,'event_ref':ref,'reason':'Source-supported fixture decision'})
            refs.append(ref)
            if disposition=='created':new.append({'client_ref':ref,'candidate_id':c['id'],
                'goal_key':c['goal_key'],'goal_text':c['goal_text'],'completion':c['completion'],
                'delivery_parts':c['delivery_parts'],'due_at':c['due_at'],
                'next_action':fixture_step(self.app,c)})
        return token,{'request_id':self.key(),'business_date':self.app.clock.now().date().isoformat(),
            'expected_family_version':self.app.store.family('f')['version'],'new_events':new,
            'plan_refs':refs,'candidate_decisions':decisions}

    def rejected(self,token,args,code):
        with self.assertRaises(BusinessError) as caught:self.app.commit_daily(token,args)
        self.assertEqual(caught.exception.code,code)
        self.assertEqual(self.app.store.list('plan','f'),[])

    def test_goal_and_delivery_created_from_confirmed_sources(self):
        self.learning_source();self.delivery_source();token,args=self.command()
        result=self.app.commit_daily(token,args)
        events=self.app.store.list('event','f')
        self.assertEqual({e['completion']['kind'] for e in events},{'goal','delivery'})
        delivery=next(e for e in events if e['completion']['kind']=='delivery')
        self.assertEqual(delivery['completion']['required_stage'],'delivered')
        self.assertEqual(len(delivery['delivery_parts']),2)
        self.assertEqual(len(self.app.store.list('candidate_decision','f')),2)
        self.assertEqual(len(result['data']['plan']['items']),2)

    def test_free_goal_without_candidate_is_rejected(self):
        token,args=self.command()
        args['new_events']=[{'client_ref':'made-up','goal_key':'made-up','goal_text':'Untrusted goal',
            'completion':{'kind':'goal','lesson_refs':['synthetic-made-up']}}]
        self.rejected(token,args,'CANDIDATE_SOURCE_MISSING')
        self.assertEqual(self.app.store.list('event','f'),[])

    def test_changed_key_completion_deadline_parts_and_goal_text_are_rejected(self):
        self.delivery_source();token,args=self.command()
        changes=[('goal_key','different'),('due_at','2026-10-06T16:00:00+08:00'),
            ('goal_text','Different obligation'),('delivery_parts',[]),
            ('completion',{'kind':'delivery','required_part_ids':['participation'],'required_stage':'sent'})]
        for name,value in changes:
            with self.subTest(field=name):
                bad=deepcopy(args);bad['request_id']=self.key();bad['new_events'][0][name]=value
                self.rejected(token,bad,'CANDIDATE_CONTRACT_MISMATCH')
                self.assertEqual(self.app.store.list('event','f'),[])

    def test_missing_decision_coverage_rejected(self):
        self.learning_source();token,args=self.command();args['candidate_decisions']=[]
        self.rejected(token,args,'CANDIDATE_COVERAGE_INCOMPLETE')

    def test_cross_family_candidate_rejected(self):
        self.learning_source();_,foreign=self.command()
        token=self.app.open_session('g','planner_daily')
        foreign.update(request_id=self.key(),expected_family_version=self.app.store.family('g')['version'])
        with self.assertRaises(BusinessError) as caught:self.app.commit_daily(token,foreign)
        self.assertEqual(caught.exception.code,'CANDIDATE_COVERAGE_INCOMPLETE')
        self.assertEqual(self.app.store.list('event','g'),[])

    def test_underlying_source_change_without_family_bump_invalidates_snapshot(self):
        self.learning_source();token,args=self.command()
        self.app.store.connection.execute("UPDATE learning_days SET completion_status='completed'")
        self.rejected(token,args,'STALE_CANDIDATE')

    def test_unknown_completion_does_not_generate_active_goal(self):
        self.learning_source(completion='unknown');token,args=self.command()
        self.assertEqual(args['new_events'],[])
        self.assertEqual(self.app.store.list('candidate','f')[0]['gaps'],['no_confirmed_incomplete_fact'])
        self.app.commit_daily(token,args)

    def test_insufficient_coverage_preserves_gap_and_empty_plan(self):
        self.learning_source(coverage=False);token,args=self.command()
        self.assertEqual(args['new_events'],[])
        self.assertEqual(self.app.store.list('candidate','f')[0]['gaps'],['coverage_incomplete'])
        self.app.commit_daily(token,args)

    def test_disabled_rule_stays_disabled(self):
        self.learning_source(enabled=False);token,args=self.command()
        self.assertEqual(args['new_events'],[])
        self.app.commit_daily(token,args)

    def test_repeat_source_and_candidate_scan_do_not_bump(self):
        self.learning_source();self.command();before=self.app.store.family('f')['version']
        candidate=self.app.store.list('candidate','f')[0]
        for _ in range(20):
            with self.app.store.transaction():refresh_candidates(self.app,'f')
        self.assertEqual(self.app.store.family('f')['version'],before)
        self.assertEqual(self.app.store.list('candidate','f')[0]['version'],candidate['version'])

    def test_next_day_same_goal_links_original_id_and_preserves_contract(self):
        self.learning_source();token,args=self.command();self.app.commit_daily(token,args)
        original=self.app.store.list('event','f')[0]
        self.app.clock=Clock('2026-10-05T09:00:00+08:00')
        token,args=self.command();self.assertEqual(args['new_events'],[])
        self.assertEqual(args['candidate_decisions'][0]['event_ref'],original['id'])
        self.app.commit_daily(token,args)
        self.assertEqual(self.app.store.list('event','f'),[original])
        self.assertEqual(len(self.app.store.list('plan','f')),2)

    def test_arbitrary_pending_text_must_be_retained(self):
        channel=self.app.open_session('f','channel')
        m=self.app.ingest_message(channel,{'external_id':'need','body':'请给我一份新东西',
                                          'occurred_at':self.app.clock.iso()})['data']
        service=self.app.open_session('f','service')
        p=self.app.record_pending(service,{'request_id':'pending','source_message_id':m['id'],'source_version':m['version'],
            'request':'自由提议没有确认结构','remaining_work':['待确认目标合同']})['data']
        token,args=self.command();args['pending_decisions']=[{'input_id':p['id'],'disposition':'created',
            'event_ref':'untrusted','reason':'Cannot self certify'}]
        self.rejected(token,args,'PENDING_SOURCE_UNCONFIRMED')
        args['pending_decisions'][0].update(disposition='retained',event_ref=None)
        self.app.commit_daily(token,args)
        self.assertEqual(self.app.store.list('pending','f')[0]['disposition'],'pending')

    def test_context_covers_candidates_waiting_and_all_original_responsibilities(self):
        self.learning_source();token,args=self.command();context=build_context(self.app,token)
        self.assertEqual(len(context['candidates']),1)
        self.assertEqual(context['candidate_versions'],{context['candidates'][0]['id']:1})
        self.assertEqual(context['candidate_coverage'],'all_configured_sources_with_explicit_gaps')

    def test_daily_worker_strict_path_commits_only_provided_candidate(self):
        self.learning_source();queue=JobQueue(self.app)
        with self.app.store.transaction():queue.enqueue('f','daily-worker',{'kind':'daily','business_date':'2026-10-04'})
        def handler(job,tools):
            context=build_context(self.app,tools.token);c=context['candidates'][0]
            result=tools.call('planner.commit_daily',{'request_id':'worker-commit','business_date':'2026-10-04',
                'expected_family_version':context['family_version'],'new_events':[{'client_ref':'goal','candidate_id':c['id'],
                    'goal_key':c['goal_key'],'goal_text':c['goal_text'],'completion':c['completion'],'due_at':c['due_at'],
                    'next_action':fixture_step(self.app,c)}],
                'plan_refs':['goal'],'candidate_decisions':[{'candidate_id':c['id'],'expected_version':c['version'],
                    'disposition':'created','event_ref':'goal','reason':'Confirmed source fixture'}]})
            self.assertTrue(result['ok'],result);return {}
        self.assertEqual(Worker(self.app,handler).run_once()['outcome'],'succeeded')
        self.assertEqual(len(self.app.store.list('event','f')),1)
        self.assertEqual(len(self.app.store.list('plan','f')),1)
        self.assertFalse(Worker(self.app,handler).run_once()['ran'])

    def test_known_report_gate_zero_model_and_keeps_pending_source(self):
        self.learning_source()
        with self.app.store.transaction():
            self.app.store.put('report','gate-report','f',{'resolution':None})
            JobQueue(self.app).enqueue('f','gated-daily',{'kind':'daily','business_date':'2026-10-04'})
        calls=[];worker=Worker(self.app,lambda j,t:calls.append(j))
        for _ in range(20):self.assertFalse(worker.run_once()['ran'])
        self.assertEqual(calls,[])
        self.assertIsNone(self.app.store.get('report','gate-report','f')['resolution'])
        self.assertEqual(self.app.store.list('job','f')[0]['reason'],'REPORTS_PENDING')

    def test_source_configuration_not_exposed_to_service(self):
        token=self.app.open_session('f','service')
        from digital_tutor.tools import ToolRegistry
        names={t['name'] for t in ToolRegistry(self.app,token).describe()}
        self.assertFalse(any('candidate' in name for name in names))

    def test_same_day_repeated_daily_and_empty_no_candidate_behavior(self):
        token,args=self.command();self.app.commit_daily(token,args)
        token,args=self.command()
        with self.assertRaises(BusinessError) as caught:self.app.commit_daily(token,args)
        self.assertEqual(caught.exception.code,'DAILY_ALREADY_COMMITTED')
        self.assertEqual(self.app.store.list('event','f'),[])


    def test_post_commit_idle_candidate_refresh_does_not_wake_itself(self):
        self.learning_source();token,args=self.command();self.app.commit_daily(token,args)
        before=self.app.store.family('f')['version']
        versions={c['id']:c['version'] for c in self.app.store.list('candidate','f')}
        for _ in range(20):
            with self.app.store.transaction():refresh_candidates(self.app,'f')
        self.assertEqual(self.app.store.family('f')['version'],before)
        self.assertEqual({c['id']:c['version'] for c in self.app.store.list('candidate','f')},versions)

    def test_unconfirmed_pending_cannot_be_silently_discarded(self):
        channel=self.app.open_session('f','channel')
        message=self.app.ingest_message(channel,{'external_id':'request','body':'请帮我做一件新的事',
            'occurred_at':self.app.clock.iso()})['data']
        service=self.app.open_session('f','service')
        pending=self.app.record_pending(service,{'request_id':'record-unknown','source_message_id':message['id'],
            'source_version':message['version'],'request':'尚未确认结构的请求','remaining_work':['待确认']})['data']
        token,args=self.command()
        args['pending_decisions']=[{'input_id':pending['id'],'disposition':'not_adopted','reason':'Unsupported'}]
        self.rejected(token,args,'PENDING_SOURCE_UNCONFIRMED')

    def test_service_cannot_configure_candidate_sources(self):
        source=self.delivery_source()
        data={k:v for k,v in source.items() if k not in {'id','version'}}
        token=self.app.open_session('f','service')
        with self.assertRaises(BusinessError) as caught:configure_source(self.app,token,data)
        self.assertEqual(caught.exception.code,'ROLE_FORBIDDEN')

    def test_same_confirmed_source_configuration_is_noop(self):
        source=self.delivery_source();before=self.app.store.family('f')['version']
        value={k:v for k,v in source.items() if k not in {'id','version'}}
        self.assertEqual(configure_source(self.app,self.admin,value),source)
        self.assertEqual(self.app.store.family('f')['version'],before)

    def test_rule_activation_must_be_explicit_boolean(self):
        from pydantic import ValidationError
        source=self.delivery_source();value={k:v for k,v in source.items() if k not in {'id','version'}}
        for enabled in (None,1,'true'):
            with self.subTest(enabled=enabled),self.assertRaises(ValidationError):
                configure_source(self.app,self.admin,{**value,'enabled':enabled})
        del value['enabled']
        with self.assertRaises(ValidationError):configure_source(self.app,self.admin,value)

    def test_gap_becomes_ready_outside_family_version_stales_old_snapshot(self):
        self.learning_source(completion='unknown');token,args=self.command()
        before=self.app.store.family('f')['version']
        self.app.store.connection.execute("UPDATE learning_days SET completion_status='incomplete'")
        self.assertEqual(self.app.store.family('f')['version'],before)
        self.rejected(token,args,'STALE_CANDIDATE')

    def test_raw_import_coverage_correction_stales_snapshot(self):
        self.learning_source();token,args=self.command()
        self.app.store.connection.execute('UPDATE imports SET metadata=?',
            (encode({'coverage_through':'2026-10-01T00:00:00+08:00'}),))
        self.rejected(token,args,'STALE_CANDIDATE')

    def test_invalid_coverage_blocks_without_crashing_daily(self):
        self.learning_source()
        self.app.store.connection.execute('UPDATE imports SET metadata=?',
            (encode({'coverage_through':'not-a-date'}),))
        token,args=self.command();self.assertEqual(args['new_events'],[])
        self.assertEqual(self.app.store.list('candidate','f')[0]['status'],'blocked')
        self.app.commit_daily(token,args)

    def test_refresh_precedes_bound_family_snapshot(self):
        self.learning_source();before=self.app.store.family('f')['version']
        token,args=self.command();_,binding=self.app.auth(token,{'planner_daily'})
        self.assertGreater(binding['family_version'],before)
        self.assertEqual(binding['family_version'],args['expected_family_version'])
        self.assertEqual(binding['candidate_versions'],
            {c['id']:c['version'] for c in self.app.store.list('candidate','f') if c['status']=='ready'})

    def test_different_course_version_same_lesson_does_not_link(self):
        self.learning_source();token,args=self.command();self.app.commit_daily(token,args)
        original=self.app.store.list('event','f')[0]
        binding=self.app.store.list('course_binding','f')[0]
        with self.app.store.transaction():
            binding['course_version']='v2'
            self.app.store.put('course_binding',binding['id'],'f',binding,binding['version'])
        self.app.clock=Clock('2026-10-05T09:00:00+08:00')
        token,args=self.command();self.assertEqual(len(args['new_events']),1)
        self.assertNotEqual(args['new_events'][0]['goal_key'],original['goal_key'])
        self.app.commit_daily(token,args)
        self.assertEqual({e['source_identity']['course_version'] for e in self.app.store.list('event','f')},{'v1','v2'})

    def test_legacy_same_lesson_without_course_identity_remains_ambiguous(self):
        self.learning_source();token,args=self.command();self.app.commit_daily(token,args)
        original=self.app.store.list('event','f')[0]
        with self.app.store.transaction():
            original.pop('source_identity');original['goal_key']='legacy-label'
            self.app.store.put('event',original['id'],'f',original,original['version'])
        self.app.clock=Clock('2026-10-05T09:00:00+08:00')
        token,args=self.command();self.assertEqual(args['new_events'],[])
        self.assertEqual(self.app.store.list('candidate','f')[0]['gaps'],['original_goal_identity_ambiguous'])
        self.app.commit_daily(token,args);self.assertEqual(len(self.app.store.list('event','f')),1)

    def test_original_done_goal_does_not_create_replacement(self):
        self.learning_source();token,args=self.command();self.app.commit_daily(token,args)
        original=self.app.store.list('event','f')[0]
        with self.app.store.transaction():
            # Historical completion fixture, not a test of the completion adapter.
            original['status']='done';self.app.store.put('event',original['id'],'f',original,original['version'])
        self.app.clock=Clock('2026-10-05T09:00:00+08:00')
        token,args=self.command();self.assertEqual(args['new_events'],[])
        self.assertEqual(self.app.store.list('candidate','f')[0]['gaps'],['original_goal_already_terminal'])
        self.app.commit_daily(token,args);self.assertEqual(len(self.app.store.list('event','f')),1)

    def test_duplicate_decision_and_stale_candidate_version_rejected(self):
        self.delivery_source();token,args=self.command()
        duplicate=deepcopy(args);duplicate['candidate_decisions']*=2
        self.rejected(token,duplicate,'CANDIDATE_COVERAGE_INCOMPLETE')
        args['candidate_decisions'][0]['expected_version']+=1
        self.rejected(token,args,'STALE_CANDIDATE')

    def test_historical_legacy_daily_receipt_replays_without_new_schema_defaults(self):
        from digital_tutor.storage import digest
        app=Application(Path(self.tmp.name)/'legacy.sqlite',Clock('2026-10-04T09:00:00+08:00'))
        try:
            app.create_family('legacy','legacy-uid','mock:legacy')
            # Explicit H payload shape, predating candidate fields. Do not derive
            # this fixture via today's DailyCommit model or daily_payload helper.
            payload={'request_id':'old-daily','business_date':'2026-10-04','expected_family_version':1,
                'new_events':[{'client_ref':'old','goal_key':'old-goal','goal_text':'Historical goal',
                    'completion':{'kind':'goal','lesson_refs':['old-lesson'],'required_part_ids':[],
                                  'required_stage':'sent'},'status':'pending','waiting':None,
                    'due_at':None,'next_action':None}],
                'event_updates':[],'plan_refs':['old'],'pending_decisions':[]}
            historical={'request_id':'old-daily','outcome':'applied','data':{'plan':{'id':'historical-plan'},
                'event_ids':{'old':'historical-event'}},'record_versions':{'family_input_version':2},'error':None}
            with app.store.transaction() as db:
                db.execute('INSERT INTO receipts VALUES(?,?,?,?)',('legacy','old-daily',
                    digest({'command':'commit_daily','payload':payload}),encode(historical)))
            result=app.commit_daily(app.open_session('legacy','planner_daily'),payload)
            self.assertEqual(result,{**historical,'outcome':'duplicate'})
            self.assertEqual(app.store.family('legacy')['version'],1)
            self.assertEqual(app.store.list('event','legacy'),[])
        finally:app.close()

    def test_equivalent_period_offsets_reuse_original_event_and_keep_source_text(self):
        source=self.delivery_source();token,args=self.command();self.app.commit_daily(token,args)
        original=self.app.store.list('event','f')[0]
        value={k:v for k,v in source.items() if k not in {'id','version'}}
        value.update(source_id='same-contract-utc',period_start='2026-09-27T16:00:00+00:00',
                     period_end='2026-10-04T01:00:00+00:00')
        second=configure_source(self.app,self.admin,value)
        self.app.clock=Clock('2026-10-05T09:00:00+08:00')
        token,args=self.command();args['plan_refs']=list(dict.fromkeys(args['plan_refs']))
        self.assertEqual(args['new_events'],[])
        self.assertEqual({d['event_ref'] for d in args['candidate_decisions']},{original['id']})
        candidates=self.app.store.list('candidate','f')
        self.assertEqual(len({c['goal_key'] for c in candidates}),1)
        first=next(c for c in candidates if c['source_id']==source['id'])
        self.assertEqual(first['coverage']['period_start'],'2026-09-28T00:00:00+08:00')
        self.assertEqual(second['period_start'],'2026-09-27T16:00:00+00:00')
        self.app.commit_daily(token,args);self.assertEqual(len(self.app.store.list('event','f')),1)

    def test_distinct_contract_period_is_not_merged(self):
        source=self.delivery_source()
        value={k:v for k,v in source.items() if k not in {'id','version'}}
        value.update(source_id='next-period',period_start='2026-10-04T09:00:00+08:00',
                     period_end='2026-10-11T09:00:00+08:00')
        configure_source(self.app,self.admin,value)
        token,args=self.command()
        self.assertEqual(len({n['goal_key'] for n in args['new_events']}),2)
        self.app.commit_daily(token,args);self.assertEqual(len(self.app.store.list('event','f')),2)



if __name__=='__main__':unittest.main()
