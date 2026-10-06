"""No model API: evidence false positives and free SELECT projections."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
scripts=Path(__file__).resolve().parents[1]/'scripts'
if not scripts.is_dir():scripts=Path(__file__).resolve().parent/'candidate/backend/scripts'
sys.path.append(str(scripts))
from stage4_sql_evidence import learning_query_evidence, _rows


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.family='synthetic-family';self.target={'uid':'synthetic-a','batch_id':'batch-a','day':2}
        self.tables={'learning_days':[
            {'uid':'synthetic-a','batch_id':'batch-a','day':day,'completion_status':completion,'homework_status':homework}
            for day,completion,homework in [(1,'completed','submitted'),(2,'incomplete','not_submitted'),(3,'unknown','unknown')]]}

    def evidence(self,sql,params,*,rows=None,family=None,ok=True):
        if rows is None:rows=_rows(sql,params,self.tables,200)
        trace={'tool':'learning.query_sql','ok':ok,'arguments':{'sql':sql,'parameters':params},
               'result':{'ok':ok,'result':{'rows':rows,'scope':'bound_family_uid','read_only':True,'truncated':False}}}
        calls=[{'index':1,'family_id':family or self.family,'native':{'tool_trace':[trace]}}]
        return learning_query_evidence(calls,self.family,self.tables,self.target)

    def test_correct_parameter_with_arbitrary_projection_and_aliases(self):
        queries=[
            'SELECT completion_status,homework_status FROM learning_days WHERE day=:d',
            'SELECT completion_status AS state FROM learning_days WHERE day=:d',
            'SELECT * FROM learning_days WHERE :d=day',
            'SELECT day FROM learning_days WHERE day IN (:d)',
            'WITH actual AS (SELECT * FROM learning_days WHERE day=:d) SELECT homework_status FROM actual',
            'SELECT completion_status FROM learning_days WHERE completion_status=:state',
        ]
        for sql in queries:
            with self.subTest(sql=sql):self.assertTrue(self.evidence(sql,{'d':2,'state':'incomplete'})['observed'])

    def test_wrong_date_binding_empty_result_rejected(self):
        result=self.evidence('SELECT completion_status FROM learning_days WHERE day=:d',{'d':'2026-10-04'})
        self.assertFalse(result['observed']);self.assertEqual(result['attempts'][0]['reason'],'empty_or_truncated_result')

    def test_unrelated_parameters_and_projection_parameter_rejected(self):
        for sql,params in [
            ('SELECT * FROM learning_days WHERE day=2',{'unused':2}),
            ('SELECT :label,completion_status FROM learning_days',{'label':2}),
            ('SELECT * FROM learning_days WHERE :label=:label',{'label':2}),
            ('SELECT day,:p AS marker,completion_status,homework_status FROM learning_days WHERE :p=:p',{'p':2}),
            ('SELECT * FROM learning_days WHERE day=:day',{'day':1,'unused':2}),
        ]:
            with self.subTest(sql=sql):self.assertFalse(self.evidence(sql,params)['observed'])

    def test_success_and_scope_and_matching_trace_result_required(self):
        sql='SELECT completion_status FROM learning_days WHERE day=:d'
        self.assertFalse(self.evidence(sql,{'d':2},ok=False)['observed'])
        self.assertFalse(self.evidence(sql,{'d':2},family='another-family')['observed'])
        self.assertFalse(self.evidence(sql,{'d':2},rows=[{'completion_status':'completed'}])['observed'])

    def test_aggregate_and_unstable_limit_not_credited(self):
        for sql in ['SELECT count(*) FROM learning_days WHERE uid=:uid',
                    'SELECT completion_status FROM learning_days WHERE uid=:uid LIMIT 1']:
            with self.subTest(sql=sql):self.assertFalse(self.evidence(sql,{'uid':'synthetic-a'})['observed'])

    def test_same_query_can_filter_by_family_and_still_prove_day2_contribution(self):
        result=self.evidence('SELECT day,homework_status FROM learning_days WHERE uid=:uid',{'uid':'synthetic-a'})
        self.assertTrue(result['observed'])
        self.assertEqual(result['attempts'][0]['influential_parameters'],['uid'])

    def test_no_oracle_source_mutation(self):
        before=copy.deepcopy(self.tables)
        self.evidence('SELECT * FROM learning_days WHERE day=:d',{'d':2})
        self.assertEqual(self.tables,before)

    def test_missing_and_duplicate_target_are_uncovered(self):
        self.tables['learning_days']=[row for row in self.tables['learning_days'] if row['day']!=2]
        self.assertEqual(learning_query_evidence([],self.family,self.tables,self.target)['reason'],'oracle_target_not_unique')

    def test_real_fixture_target_and_readonly_projection(self):
        from digital_tutor.application import Application,Clock
        from digital_tutor.learning import query_learning
        from accept_stage4_passive import seed_sources
        from stage4_passive_fixtures import frozen_inputs,frozen_oracle
        from stage4_sql_evidence import family_sources
        inputs=frozen_inputs('learning');oracle=frozen_oracle('learning')['parameterized_sql_target']
        self.assertNotIn('parameterized_sql_target',inputs)
        with tempfile.TemporaryDirectory() as directory:
            app=Application(Path(directory)/'evidence.sqlite',Clock(inputs['business_time']),mode='passive',strict_runtime=True)
            try:
                families=seed_sources(app,inputs);family=families[oracle['family_fixture']]
                target={'uid':app.store.family(family)['uid'],'batch_id':oracle['batch_id'],'day':oracle['day']}
                token=app.open_session(family,'service')
                args={'sql':'SELECT completion_status AS completion,homework_status FROM learning_days WHERE day=:day','parameters':{'day':2}}
                receipt=query_learning(app,token,args)
                before=(app.store.family(family)['version'],app.store.connection.total_changes)
                tables=family_sources(app,family)
                targets=[row for row in tables['learning_days'] if all(row[k]==v for k,v in target.items())]
                self.assertEqual(len(targets),1)
                self.assertEqual((targets[0]['completion_status'],targets[0]['homework_status']),('incomplete','not_submitted'))
                calls=[{'family_id':family,'native':{'tool_trace':[{'tool':'learning.query_sql','ok':True,
                    'arguments':args,'result':{'ok':True,'result':receipt}}]}}]
                self.assertTrue(learning_query_evidence(calls,family,tables,target)['observed'])
                self.assertEqual((app.store.family(family)['version'],app.store.connection.total_changes),before)
            finally:app.close()


if __name__=='__main__':unittest.main()
