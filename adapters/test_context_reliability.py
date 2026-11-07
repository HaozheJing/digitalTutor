import io,json,sqlite3,tempfile,unittest,subprocess,sys,concurrent.futures
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import request_budget as budget
import working_context as work
import sql_tools as s
from experiment_parameter_proxy import ParameterProxy

class BudgetTests(unittest.TestCase):
    def test_complete_request_counts_tools_and_late_tool_results(self):
        body={'max_completion_tokens':8192,'messages':[{'role':'user','content':'tiny'}]}
        self.assertGreater(budget.check(body)['input_upper_estimate'],0)
        for key,value in [('tools',[{'description':'大'*150000}]),('messages',[{'role':'tool','content':'x'*400000}])]:
            with self.subTest(key=key),self.assertRaisesRegex(budget.BudgetExceeded,'request_context_budget_exceeded'):
                budget.check({**body,key:value})
    def test_clamped_output_cannot_be_masked_by_proxy_override(self):
        with self.assertRaisesRegex(budget.BudgetExceeded,'output_budget_exhausted'):
            budget.check({'max_tokens':8192},incoming={'max_completion_tokens':1})
    def test_exact_local_boundary(self):
        body={'max_tokens':1024,'messages':[]};n=budget.check(body)['input_upper_estimate']
        budget.check(body,context_window=n+1024+4096)
        with self.assertRaises(budget.BudgetExceeded):budget.check(body,context_window=n+1024+4095)
    def test_one_token_and_giant_packet_never_reach_upstream(self):
        model='deepseek-v4.1-flash-economy'
        with tempfile.TemporaryDirectory() as tmp:
            proxy=ParameterProxy('https://ark.cn-beijing.volces.com/api/plan/v3','secret',model,{'max_tokens':8192},Path(tmp)/'audit');proxy.start()
            try:
                for extra in [{'max_completion_tokens':1},{'max_completion_tokens':8192,'tools':[{'description':'private'*60000}]}]:
                    req=Request(proxy.url+'/chat/completions',json.dumps({'model':model,**extra}).encode(),{'Authorization':'Bearer '+proxy.token})
                    with patch('experiment_parameter_proxy.urlopen') as send,self.assertRaises(HTTPError) as error:urlopen(req)
                    self.assertEqual(error.exception.code,400);send.assert_not_called()
                audit=(Path(tmp)/'audit').read_text();self.assertNotIn('private',audit);self.assertNotIn('secret',audit)
                self.assertEqual(len(audit.splitlines()),2)
            finally:proxy.close()

class ViewTests(unittest.TestCase):
    def test_terminal_growth_is_not_reinjected_and_obligations_survive(self):
        feedback={'kind':'family_feedback','body':{'quote':'暂停到下周','source_message_id':'parent'}}
        value={'business_time':'2025-11-07T20:15:00+08:00','tasks':[{'id':str(i),'status':'done','day':'2025-10-13','result':'old'*1000} for i in range(1000)],
          'catalog':[{'skill':'S01','planning_contract':{'fixed_time':'06:30'},'body':'规则'*30000,'references':[{'body':'重复'*5000}]}],
          'ledger':{'constraints':[{'status':'paused'}],'cases':[{'id':'closed','status':'completed','timeline':[feedback]}, {'id':'open','status':'paused','goals':[{'lesson_id':'lesson'}],'timeline':[feedback],'tasks':[{'status':'unknown','result':'receipt'}]}]}}
        before=json.dumps(value,ensure_ascii=False);view=work.project(value)
        self.assertEqual(json.dumps(value,ensure_ascii=False),before)
        self.assertEqual(view['tasks'],[]);self.assertEqual(view['ledger']['constraints'],value['ledger']['constraints'])
        self.assertEqual(view['ledger']['family_evidence'][0]['family_evidence'],[feedback])
        self.assertEqual(view['ledger']['cases'][0]['tasks'][0]['status'],'unknown')
        self.assertLess(len(json.dumps(view,ensure_ascii=False)),2000)
    def test_archive_pages_are_complete_and_scoped(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(s,'DB',Path(tmp)/'state.sqlite'):
            state={'run_id':'run-A','uid':'family-A'};original={'tasks':[{'id':'old','status':'done','result':'证据'*9000}]}
            with patch('test_mode.state',return_value=state):
                view=work.model(original);ident=view['context_archive']['context_id'];parts=[];offset=0
                while True:
                    page=work.page(ident,'tasks',offset);parts.append(page['json_fragment']);offset=page['next_offset']
                    self.assertLessEqual(len(parts[-1]),6000)
                    if offset is None:break
                self.assertEqual(json.loads(''.join(parts)),original['tasks'])
            for foreign in [{'run_id':'run-A','uid':'family-B'},{'run_id':'run-B','uid':'family-A'}]:
                with patch('test_mode.state',return_value=foreign),self.assertRaisesRegex(ValueError,'context_not_in_current_family_run'):work.page(ident,'tasks')

@unittest.skipIf(sys.platform=='win32','Linux flock contract')
class OwnerTests(unittest.TestCase):
    def test_second_owner_rejected_and_crash_releases(self):
        script=Path(__file__).with_name('workspace_owner.py')
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'runtime').mkdir()
            def start():return subprocess.Popen([sys.executable,'-B',str(script),tmp],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            first=start()
            try:
                self.assertEqual(first.stdout.readline().strip(),'owned')
                second=start();self.assertEqual(second.stdout.readline().strip(),'busy');second.communicate(timeout=5);self.assertEqual(second.returncode,2)
                first.kill();first.communicate(timeout=5)
                third=start();self.assertEqual(third.stdout.readline().strip(),'owned');third.communicate(timeout=5);self.assertEqual(third.returncode,0)
            finally:
                if first.poll() is None:first.kill();first.communicate(timeout=5)
