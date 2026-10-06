"""CLI/runtime logic tests use deterministic tool-driving Host substitutes, never API."""
from contextlib import redirect_stdout,redirect_stderr
import io,json,tempfile,unittest
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]/'scripts'))
from manual_demo_runtime import ManualDemo,DemoError
from demo_passive import main
from digital_tutor.mock_channel import channel_for


class FakeHost:
    def __init__(self,project,runtime_root,*_,**__):
        self.runtime_root=Path(runtime_root);self.last_result={};self.calls=[]

    def __call__(self,job,tools):
        app=tools.app;family,binding=app.auth(tools.token,{'service'});self.calls.append(job)
        self.last_result={'run_id':binding['run_id'],'test_driver':'deterministic_not_real_model','tool_trace':[]}
        def call(name,args):
            result=tools.call(name,args)
            if not result['ok']:raise AssertionError(result)
            return result['result']
        msg=app.store.get('message',job['message_id'],family)
        if job.get('recovery_only'):
            op=app.store.get('operation',job['recovery_operation_ids'][0],family)
        else:
            if not msg['interpreted']:
                call('message.interpret',{'request_id':'interpret-'+job['run_id'],'message_id':msg['id'],
                    'expected_version':msg['version'],'intents':[{'intent_id':'question','text':'Synthetic question'}]})
            op=call('wechat.send_message',{'request_id':'reply-'+job['run_id'],
                'expected_family_version':app.store.family(family)['version'],'body':'Synthetic test reply',
                'action_kind':'reply','reply_to':[{'message_id':msg['id'],'intent_id':'question'}]})['data']
        msg=app.store.get('message',msg['id'],family)
        args={'request_id':'outcome-'+job['run_id'],'outcome':'action_taken' if op['stage'] in {'sent','delivered'} else 'waiting',
            'reason':'Program driver result','refs':[{'kind':'message','id':msg['id'],'version':msg['version']},
                {'kind':'operation','id':op['id'],'version':op['version']}]}
        if args['outcome']=='waiting':args.update(remaining_work=['Original receipt'],resume_condition='New original receipt')
        call('service.record_outcome',args)
        return {'outcome_id':binding['run_id']}


class FailureHost(FakeHost):
    def __call__(self,job,tools):
        self.calls.append(job);self.last_result={'test_driver':'deterministic_error'}
        return {'retryable_error':'TEST_FAILURE'}


class WaitingHost(FakeHost):
    def __call__(self,job,tools):
        self.calls.append(job);app=tools.app;family,binding=app.auth(tools.token,{'service'})
        self.last_result={'run_id':binding['run_id'],'test_driver':'deterministic_wait'}
        msg=app.store.get('message',job['message_id'],family)
        interpreted=tools.call('message.interpret',{'request_id':'interpret-'+job['run_id'],'message_id':msg['id'],
            'expected_version':msg['version'],'intents':[{'intent_id':'question','text':'Synthetic question'}]})
        if not interpreted['ok']:raise AssertionError(interpreted)
        msg=app.store.get('message',msg['id'],family)
        result=tools.call('service.record_outcome',{'request_id':'wait-'+job['run_id'],'outcome':'waiting',
            'reason':'Material missing','remaining_work':['Answer original question'],'resume_condition':'Approved source becomes available',
            'refs':[{'kind':'message','id':msg['id'],'version':msg['version']}]})
        if not result['ok']:raise AssertionError(result)
        return {'outcome_id':binding['run_id']}


class ManualDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.active=[]
        for name in ('demo_passive.py','manual_demo_runtime.py','accept_stage4_passive.py','stage4_passive_fixtures.py'):
            path=self.root/'backend/scripts'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('test version one')
        path=self.root/'backend/src/digital_tutor/application.py';path.parent.mkdir(parents=True);path.write_text('test code fingerprint')
        path=self.root/'agent/prompts/service.md';path.parent.mkdir(parents=True);path.write_text('test prompt fingerprint')
        self.approved={'chain':'R','destination':'maas.qianwenaiapi.com','files':{'fixture':'approved-hash'}}

    def tearDown(self):
        for demo in self.active:demo.close()
        self.tmp.cleanup()

    def create(self,run='manual-one',**kwargs):
        demo=ManualDemo(self.root,run,host_factory=kwargs.pop('host_factory',FakeHost),
            approval_loader=lambda _:self.approved,**kwargs);self.active.append(demo);return demo

    def test_empty_start_is_zero_model_and_external_activity(self):
        demo=self.create();summary=demo.turn()
        self.assertEqual(summary['model_runs'],0);self.assertEqual(summary['status'],'idle');self.assertEqual(summary['exit_code'],0)
        self.assertEqual(summary['channel_replies'],[]);self.assertEqual(demo.host.calls,[])
        self.assertTrue((Path(summary['evidence'])/'database.redacted.sqlite').exists())

    def test_multiple_turns_and_resume_preserve_family_and_prior_evidence(self):
        demo=self.create(clock='2026-10-04T10:00:00+08:00');first=demo.turn('Fictional first question');second=demo.turn('Fictional next question')
        self.assertEqual([first['status'],second['status']],['replied','replied'])
        self.assertEqual([first['model_runs'],second['model_runs']],[1,1]);owner=demo.marker['instance_id']
        old=(Path(first['evidence'])/'summary.json').read_bytes();demo.close()
        resumed=self.create(resume=True);third=resumed.turn('Fictional third question')
        self.assertEqual(third['status'],'replied');self.assertEqual(resumed.marker['instance_id'],owner)
        self.assertEqual(old,(Path(first['evidence'])/'summary.json').read_bytes())
        self.assertEqual(resumed.app.store.connection.execute('SELECT count(*) FROM families').fetchone()[0],1)
        self.assertEqual(channel_for(resumed.app).count(),3)

    def test_existing_directory_requires_resume_and_new_resume_rejected(self):
        self.create()
        with self.assertRaises(DemoError):self.create()
        with self.assertRaises(DemoError):self.create('missing',resume=True)

    def test_source_change_blocks_resume_without_database_write(self):
        demo=self.create();path=demo.state/'business.sqlite';demo.close();before=path.read_bytes()
        (self.root/'agent/prompts/service.md').write_text('changed source')
        with self.assertRaises(DemoError):self.create(resume=True)
        self.assertEqual(path.read_bytes(),before)

    def test_approval_change_blocks_resume(self):
        demo=self.create();demo.close();self.approved['files']['fixture']='different'
        with self.assertRaises(DemoError):self.create(resume=True)

    def test_marker_mismatch_and_acceptance_path_rejected(self):
        demo=self.create();marker=demo.state/'manual-demo.json';demo.close()
        data=json.loads(marker.read_text());data['owner']='acceptance';marker.write_text(json.dumps(data))
        with self.assertRaises(DemoError):self.create(resume=True)
        for bad in ('../4b1-frozen','/root/state/backend-stage4/4b1-frozen','state/backend-stage4/x'):
            with self.assertRaises(DemoError):self.create(bad,resume=True)

    def test_manual_namespace_cannot_symlink_to_acceptance_directory(self):
        target=self.root/'evaluations/backend-stage4';target.mkdir(parents=True)
        parent=self.root/'state';parent.mkdir()
        (parent/'backend-manual-demo').symlink_to(target,target_is_directory=True)
        with self.assertRaises(DemoError):self.create()
        self.assertEqual(list(target.iterdir()),[])

    def test_unknown_receipt_reconciles_original_then_recovers_without_resending(self):
        demo=self.create(clock='2026-10-04T10:00:00+08:00');demo.app.mock_send_outcome='unknown'
        summary=demo.turn('Fictional question with receipt uncertainty')
        self.assertEqual(summary['exit_code'],0,summary);self.assertEqual(summary['model_runs'],2)
        self.assertEqual(channel_for(demo.app).count(),1)
        self.assertEqual(len({j['id'] for j in demo.host.calls}),1)
        self.assertFalse(demo.host.calls[0].get('recovery_only'));self.assertTrue(demo.host.calls[1]['recovery_only'])
        self.assertTrue(any(o['changed'] for o in summary['reconciliation']))

    def test_unchanged_receipt_has_no_repeated_recovery_or_version_bump(self):
        demo=self.create(clock='2026-10-04T10:00:00+08:00');demo.app.mock_send_outcome='unknown'
        channel=channel_for(demo.app);original_query=channel.query;channel.query=lambda _:None
        first=demo.turn('Fictional uncertain send');self.assertEqual(first['model_runs'],1)
        self.assertEqual(sum(bool(j.get('recovery_only')) for j in demo.host.calls),0)
        self.assertEqual(first['exit_code'],2);version=demo.app.store.family('synthetic-4b1-a')['version']
        second=demo.turn();self.assertEqual(second['model_runs'],0);self.assertEqual(second['exit_code'],2)
        self.assertEqual(version,demo.app.store.family('synthetic-4b1-a')['version'])
        channel.query=original_query;third=demo.turn();self.assertEqual(third['model_runs'],1);self.assertEqual(third['exit_code'],0)
        self.assertEqual(channel.count(),1)

    def test_waiting_keeps_unanswered_and_has_nonzero_status_without_false_delivery(self):
        demo=self.create(clock='2026-10-04T10:00:00+08:00',host_factory=WaitingHost)
        result=demo.turn('Fictional missing material question')
        self.assertEqual(result['status'],'waiting');self.assertEqual(result['exit_code'],2)
        self.assertEqual(len(result['unanswered']),1);self.assertEqual(result['channel_replies'],[])
        self.assertEqual(demo.turn()['model_runs'],0)

    def test_failure_has_error_code_and_persistent_round_evidence(self):
        demo=self.create(clock='2026-10-04T10:00:00+08:00',host_factory=FailureHost)
        summary=demo.turn('Fictional question');self.assertEqual(summary['exit_code'],1)
        self.assertEqual(summary['status'],'failed');self.assertTrue(summary['failures'])
        self.assertTrue((Path(summary['evidence'])/'summary.json').exists())

    def test_simulated_window_blocks_before_model_and_clock_is_shared(self):
        demo=self.create(clock='2026-10-04T21:01:00+08:00');summary=demo.turn('Fictional late message')
        self.assertEqual(summary['model_runs'],0);self.assertEqual(summary['exit_code'],2)
        self.assertEqual(demo.app.clock.shared_path,str((demo.state/'clock.json').resolve()))
        demo.close();resumed=self.create(resume=True,clock='2026-10-05T10:00:00+08:00')
        self.assertEqual(resumed.turn()['status'],'replied')

    def test_cli_empty_noninteractive_and_waiting_exit_codes_are_truthful(self):
        def factory(project,run,**kwargs):return self.create(run,**kwargs)
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            code=main(['--run','empty'],project=self.root,demo_factory=factory,is_interactive=False)
            waiting=main(['--run','late','--clock','2026-10-04T21:01:00+08:00','--message','Fictional late question'],
                         project=self.root,demo_factory=factory,is_interactive=False)
        self.assertEqual(code,0);self.assertEqual(waiting,2)

    def test_cli_interactive_uses_one_session_and_no_background_service(self):
        inputs=iter(['Fictional interactive question','/quit'])
        def factory(project,run,**kwargs):return self.create(run,**kwargs)
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            code=main(['--run','interactive','--clock','2026-10-04T10:00:00+08:00'],project=self.root,
                demo_factory=factory,input_fn=lambda _:next(inputs),is_interactive=True)
        self.assertEqual(code,0);self.assertEqual(len(self.active),1)

    def test_resume_family_identity_mismatch_is_rejected(self):
        demo=self.create();demo.app.store.connection.execute("UPDATE families SET uid='unexpected-real-identity'");demo.close()
        with self.assertRaises(DemoError):self.create(resume=True)

    def test_explicit_failed_channel_operation_is_failure_not_waiting_success(self):
        demo=self.create(clock='2026-10-04T10:00:00+08:00');demo.app.mock_send_outcome='failed'
        result=demo.turn('Fictional failed delivery')
        self.assertEqual(result['exit_code'],1);self.assertEqual(result['status'],'failed')
        self.assertEqual(result['channel_replies'],[])
        self.assertTrue(any(f.get('reason')=='CHANNEL_OPERATION_FAILED' for f in result['failures']))

    def test_completed_resume_is_idle_without_replaying_prior_answer(self):
        demo=self.create(clock='2026-10-04T10:00:00+08:00');first=demo.turn('Fictional question');demo.close()
        self.assertEqual(first['status'],'replied')
        resumed=self.create(resume=True);summary=resumed.turn()
        self.assertEqual(summary['model_runs'],0);self.assertEqual(summary['channel_replies'],[])
        self.assertEqual(channel_for(resumed.app).count(),1)

    def test_linked_business_database_cannot_open_acceptance_state(self):
        demo=self.create();database=demo.state/'business.sqlite';demo.close()
        target=self.root/'acceptance.sqlite';database.rename(target);database.symlink_to(target)
        with self.assertRaises(DemoError):self.create(resume=True)

    def test_mid_session_source_drift_stops_before_ingesting_new_input(self):
        demo=self.create();(self.root/'agent/prompts/service.md').write_text('mid-session change')
        with self.assertRaises(DemoError):demo.turn('Fictional question')
        self.assertEqual(demo.app.store.list('message','synthetic-4b1-a'),[]);self.assertEqual(demo.host.calls,[])

    def test_resume_rejects_ledger_and_all_sqlite_sidecar_links_without_touching_targets(self):
        names=['business.channel.sqlite','business.sqlite-wal','business.sqlite-shm','business.sqlite-journal',
               'business.channel.sqlite-wal','business.channel.sqlite-shm','business.channel.sqlite-journal']
        for index,name in enumerate(names):
            with self.subTest(file=name):
                run='linked-resume-'+str(index);demo=self.create(run);demo.close()
                target=self.root/('acceptance-target-'+str(index));original=b'unchanged acceptance bytes\x00\x01'
                target.write_bytes(original);linked=demo.state/name
                if linked.exists():linked.unlink()
                linked.symlink_to(target)
                with self.assertRaises(DemoError):self.create(run,resume=True,clock='2026-10-04T10:00:00+08:00')
                self.assertEqual(target.read_bytes(),original)

    def test_each_turn_rechecks_ledger_and_sqlite_sidecars_before_any_input_or_channel_access(self):
        names=['business.channel.sqlite','business.sqlite-wal','business.sqlite-shm','business.sqlite-journal',
               'business.channel.sqlite-wal','business.channel.sqlite-shm','business.channel.sqlite-journal']
        for index,name in enumerate(names):
            with self.subTest(file=name):
                demo=self.create('linked-live-'+str(index));target=self.root/('live-target-'+str(index))
                original=b'untouched live acceptance target\x00\x01';target.write_bytes(original)
                linked=demo.state/name
                if linked.exists():linked.unlink()
                linked.symlink_to(target)
                with self.assertRaises(DemoError):demo.turn('Fictional input must never be ingested')
                self.assertEqual(target.read_bytes(),original)
                self.assertEqual(demo.host.calls,[])
                self.assertEqual(demo.app.store.list('message','synthetic-4b1-a'),[])
                demo.close();self.assertEqual(target.read_bytes(),original)

    def test_new_pause_runs_while_original_send_receipt_is_unchanged(self):
        class PauseHost(FakeHost):
            def __call__(host,job,tools):
                app=tools.app;family,binding=app.auth(tools.token,{'service'})
                message=app.store.get('message',job['message_id'],family)
                if message['body']!='请先不要催补课。':return super().__call__(job,tools)
                host.calls.append(job);host.last_result={'run_id':binding['run_id'],'test_driver':'deterministic_pause'}
                def call(name,args):
                    result=tools.call(name,args)
                    if not result['ok']:raise AssertionError(result)
                    return result['result']
                message=call('message.interpret',{'request_id':'interpret-'+job['run_id'],
                    'message_id':message['id'],'expected_version':message['version'],
                    'intents':[{'intent_id':'pause','text':'暂停补课催促','requires_reply':False}]})['data']
                control=call('control.apply',{'request_id':'control-'+job['run_id'],
                    'action_kinds':['remind_makeup'],'reason':'家长明确暂停',
                    'source_message_id':message['id'],'source_version':message['version']})['data']
                call('service.record_outcome',{'request_id':'outcome-'+job['run_id'],'outcome':'waiting',
                    'reason':'已保存暂停；原发送仍待核实','remaining_work':['核实原发送'],'resume_condition':'原号出现新回执',
                    'refs':[{'kind':'message','id':message['id'],'version':message['version']},
                            {'kind':'control','id':control['id'],'version':control['version']}]})
                return {'outcome_id':binding['run_id']}
        demo=self.create(clock='2026-10-04T10:00:00+08:00',host_factory=PauseHost);demo.app.mock_send_outcome='unknown'
        channel=channel_for(demo.app);channel.query=lambda _:None
        first=demo.turn('Fictional uncertain question');self.assertEqual(first['exit_code'],2)
        recovery_count=sum(bool(j.get('recovery_only')) for j in demo.host.calls)
        second=demo.turn('请先不要催补课。')
        self.assertEqual(second['model_runs'],1,second)
        controls=demo.app.store.list('control','synthetic-4b1-a')
        self.assertEqual(len(controls),1);self.assertEqual(controls[0]['status'],'active')
        self.assertEqual(controls[0]['action_kinds'],['remind_makeup'])
        self.assertEqual(channel.count(),1)
        self.assertEqual(recovery_count,sum(bool(j.get('recovery_only')) for j in demo.host.calls))
        third=demo.turn();self.assertEqual(third['model_runs'],0)
