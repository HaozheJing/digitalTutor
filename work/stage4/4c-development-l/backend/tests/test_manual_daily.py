"""Finite manual daily contracts; fake model proposals through real Worker/Store."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.append(str(Path(__file__).resolve().parents[1]/'scripts'))
from manual_daily_runtime import ManualDaily,ManualSources,DemoError,FAMILY,BATCH
from demo_daily import main


class EmptyDailyHost:
    """Program fixture, not a native/model acceptance claim."""
    def __init__(self,project,runtime_root,budget_seconds,**kwargs):
        self.runtime_root=Path(runtime_root);self.last_result=None;self.calls=0

    def __call__(self,job,tools):
        self.calls+=1
        if job['kind']!='daily':raise AssertionError('Only empty daily is simulated in these program tests')
        context=tools.call('context.read',{})['result']
        result=tools.call('planner.commit_daily',{'request_id':'empty-'+job['run_id'],
            'business_date':tools.app.clock.now().date().isoformat(),
            'expected_family_version':context['family_version'],'candidate_decisions':[]})
        if not result['ok']:raise AssertionError(result)
        self.last_result={'run_id':job['run_id'],'api_calls':0,'tool_trace':[]}
        return {}


class ManualDailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.project=Path(self.tmp.name)/'project';self.project.mkdir()
        source=Path(__file__).resolve().parents[2]
        for directory in ('backend/src','backend/scripts','agent/prompts'):
            shutil.copytree(source/directory,self.project/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        self.demos=[]

    def tearDown(self):
        for demo in self.demos:demo.close()
        self.tmp.cleanup()

    def open(self,run='daily-one',**kwargs):
        kwargs.setdefault('clock','2026-10-04T10:00:00+08:00')
        demo=ManualDaily(self.project,run,host_factory=EmptyDailyHost,
            approval_loader=lambda project:{'fixture_approval':'unchanged'},**kwargs)
        self.demos.append(demo);return demo

    def test_default_does_not_run_scheduler_or_model_or_send(self):
        demo=self.open();version=demo.app.store.family(FAMILY)['version'];result=demo.turn()
        self.assertEqual(result['status'],'idle');self.assertEqual(result['exit_code'],0)
        self.assertEqual(result['completed_native_runs'],0);self.assertEqual(demo.host.calls,0)
        self.assertEqual(result['after']['channel_count'],0)
        self.assertEqual(demo.app.store.family(FAMILY)['version'],version)
        self.assertEqual(demo.app.store.list('job',FAMILY),[])

    def test_explicit_tick_uses_strict_daily_worker_once_and_idle_is_stable(self):
        demo=self.open();first=demo.turn(tick=True)
        self.assertEqual(first['status'],'planned');self.assertEqual(first['completed_native_runs'],1)
        self.assertEqual(len(first['plans']),1);self.assertEqual(first['after']['channel_count'],0)
        self.assertEqual(demo.app.mode,'daily');self.assertTrue(demo.app.strict_runtime)
        second=demo.turn(tick=True)
        self.assertEqual(second['completed_native_runs'],0);self.assertEqual(second['before'],second['after'])
        self.assertEqual(second['selected_events'],[])

    def test_resume_same_state_new_day_and_shared_clock_without_new_family(self):
        demo=self.open();first=demo.turn(tick=True);demo.close()
        resumed=self.open(resume=True,clock='2026-10-05T08:00:00+08:00')
        second=resumed.turn(tick=True)
        self.assertEqual(second['completed_native_runs'],1)
        self.assertEqual(len(resumed.app.store.list('plan',FAMILY)),2)
        self.assertEqual(resumed.app.store.connection.execute('SELECT count(*) FROM families').fetchone()[0],1)
        self.assertEqual(Path(resumed.app.clock.shared_path),resumed.state/'clock.json')
        self.assertNotEqual(first['evidence'],second['evidence'])

    def test_early_window_and_known_reports_do_not_invoke_model(self):
        demo=self.open(clock='2026-10-04T07:59:00+08:00');early=demo.turn(tick=True)
        self.assertEqual(early['completed_native_runs'],0)
        with demo.app.store.transaction():
            demo.app.store.put('report','historical-unconsumed','synthetic-manual-daily-family',{'resolution':None})
        gate=demo.turn(tick=True,clock='2026-10-04T08:00:00+08:00')
        self.assertEqual(gate['completed_native_runs'],0);self.assertEqual(gate['exit_code'],2)
        self.assertEqual(gate['unconsumed_reports'][0]['resolution'],None)
        self.assertEqual(demo.host.calls,0)

    def test_message_can_be_staged_without_planning_or_interpretation(self):
        demo=self.open();result=demo.turn('虚构家长希望了解第1天课程。')
        self.assertEqual(result['completed_native_runs'],0);self.assertEqual(result['exit_code'],2)
        self.assertTrue(result['unanswered']);self.assertEqual(result['plans'],[])
        self.assertFalse(demo.app.store.list('message',FAMILY)[0]['interpreted'])

    def test_structured_source_setup_cannot_write_model_plan_event_or_report(self):
        sources={'synthetic':True,'imported_at':'2026-10-04T07:30:00+08:00',
                 'learning_days':[{'day':1,'completion_status':'incomplete'}],
                 'course':{'course_id':'simulation-L2-fox','course_version':'simulation-v1',
                           'lesson_map':[{'day':1,'lesson_ref':'LESSON-L2-FOX-D01'}]}}
        demo=self.open(sources=sources)
        self.assertEqual(demo.app.store.connection.execute('SELECT batch_id FROM learning_days').fetchone()[0],BATCH)
        self.assertEqual(len(demo.app.store.list('course_binding',FAMILY)),1)
        for kind in ('event','plan','report'):self.assertEqual(demo.app.store.list(kind,FAMILY),[])
        from pydantic import ValidationError
        for forbidden in ('events','plans','reports','family_id','model','approved_materials'):
            with self.subTest(field=forbidden),self.assertRaises(ValidationError):
                ManualSources.model_validate({**sources,forbidden:[]})

    def test_resume_rejects_wrong_owner_source_change_and_path_input(self):
        demo=self.open();demo.close()
        with self.assertRaises(DemoError):self.open()
        with self.assertRaises(DemoError):self.open(run='../backend-stage4/old',resume=True)
        marker=json.loads((demo.state/'daily-demo.json').read_text(encoding='utf-8'))
        marker['owner']='acceptance'
        (demo.state/'daily-demo.json').write_text(json.dumps(marker),encoding='utf-8')
        with self.assertRaises(DemoError):self.open(resume=True)
        fresh=self.open(run='daily-source-drift');fresh.close()
        (self.project/'agent/prompts/common.md').write_text('changed',encoding='utf-8')
        with self.assertRaises(DemoError):self.open(run='daily-source-drift',resume=True)

    def test_per_turn_rechecks_sqlite_links_before_accepting_message(self):
        demo=self.open();target=self.project/'historical-channel.sqlite';target.write_bytes(b'protected')
        link=demo.state/'business.channel.sqlite-wal'
        try:link.symlink_to(target)
        except OSError:self.skipTest('Symbolic links unavailable')
        with self.assertRaises(DemoError):demo.turn('Do not accept this input',tick=True)
        self.assertEqual(target.read_bytes(),b'protected')
        self.assertEqual(demo.app.store.list('message',FAMILY),[]);link.unlink()

    def test_interactive_quit_is_finite_and_does_not_tick_implicitly(self):
        created=[]
        def factory(project,run,**kwargs):
            kwargs.pop('sources',None)
            demo=self.open(run=run,**kwargs);created.append(demo);return demo
        with redirect_stdout(io.StringIO()):
            code=main(['--run','cli-quit','--clock','2026-10-04T10:00:00+08:00'],project=self.project,
                      demo_factory=factory,input_fn=lambda prompt:'/quit',is_interactive=True)
        self.assertEqual(code,0);self.assertEqual(created[0].host.calls,0)


if __name__=='__main__':unittest.main()
