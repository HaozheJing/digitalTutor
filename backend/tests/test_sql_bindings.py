"""Policy counterexamples plus real scoped SQL integration; no model API."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from digital_tutor.sql_bindings import check_filter_bindings, BindingPolicyError, MAX_GROUP_DEPTH


class BindingPolicyTests(unittest.TestCase):
    def test_direct_literals_rejected_even_with_unrelated_binding(self):
        expressions = [
            'day=2', '2=day', 'day<>2', 'day!=2', 'day>=2', 'day < 2',
            'day=(+2)', '((-2))<day', 'day=(1+1)', 'day=2e0', 'day=0x02',
            "status LIKE 'comp%'", "'comp%' LIKE status", "status='complete''d'",
            'day IN (1,2)', 'day NOT IN (:d,2)', 'day BETWEEN 1 AND :hi',
            'day NOT BETWEEN :lo AND (3)', '(day)=((2))',
            'day /* nonsemantic */ = /* comment */ 2',
            'day=COALESCE(2,0)', 'day=ABS(2)', 'day=CAST(2 AS INTEGER)',
            "day=json_extract('{\"day\":2}','$.day')", 'day IS TRUE',
            'day IS NOT FALSE', 'day=?', "status=X'434F'",
        ]
        for expression in expressions:
            with self.subTest(expression=expression), self.assertRaises(BindingPolicyError):
                check_filter_bindings('SELECT :unrelated AS marker FROM learning_days WHERE '+expression,
                                      {'unrelated':'unused','d':2,'lo':1,'hi':3})

    def test_named_predicates_and_fixed_function_arguments_allowed(self):
        queries = [
            'SELECT * FROM learning_days WHERE day=:d',
            'SELECT * FROM learning_days WHERE ((:d)) = (+day)',
            'SELECT * FROM learning_days WHERE day IN (:lo,:hi)',
            'SELECT * FROM learning_days WHERE day BETWEEN :lo AND :hi',
            'SELECT * FROM learning_days WHERE day IS NULL',
            'SELECT * FROM learning_days WHERE day IS NOT (NULL)',
            "SELECT * FROM learning_days WHERE json_extract(evidence_locator,'$.day')=:d",
            'SELECT * FROM learning_days WHERE COALESCE(day,0)>=:d',
            "SELECT * FROM learning_days WHERE IFNULL(status,'unknown')=:status",
            'SELECT * FROM learning_days WHERE NULLIF(day,0)=:d',
            'SELECT * FROM learning_days WHERE day=COALESCE(:d,0)',
            'SELECT * FROM learning_days WHERE (day=@d OR day=$d)',
            'SELECT count(*) FROM learning_days HAVING count(*)>:d',
            'SELECT a.day FROM learning_days a JOIN learning_days b ON a.day=b.day WHERE a.day=:d',
            'SELECT CASE WHEN day=:d THEN 1 ELSE 0 END AS flag FROM learning_days',
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                check_filter_bindings(sql,{'d':2,'lo':1,'hi':3,'status':'unknown'})

    def test_projection_literals_comments_and_quoted_identifiers_are_not_filters(self):
        queries = [
            "SELECT 2, 'WHERE day=3', COALESCE(day,0) FROM learning_days",
            'SELECT day FROM learning_days -- WHERE day=2\nORDER BY day LIMIT 2',
            'SELECT day FROM learning_days /* WHERE day=2 */',
            'SELECT "day=2", [WHERE], `IN` FROM learning_days',
            'SELECT * FROM learning_days WHERE "day"=:d',
            'SELECT * FROM learning_days WHERE [day]=:d',
            'SELECT * FROM learning_days WHERE `day`=:d',
        ]
        for sql in queries:
            with self.subTest(sql=sql):check_filter_bindings(sql,{'d':2})

    def test_quoted_functions_preserve_sqlite_identifier_forms(self):
        db=sqlite3.connect(':memory:')
        try:
            for function in ('"json_extract"','`json_extract`','[json_extract]'):
                sql="SELECT metadata_json FROM (SELECT :source AS metadata_json) WHERE "+function+"(metadata_json,'$.day')=:d"
                with self.subTest(function=function):
                    params={'source':'{"day":2}','d':2}
                    self.assertEqual(check_filter_bindings(sql,params)['direct_predicate_parameters'],['d'])
                    self.assertEqual(db.execute(sql,params).fetchall(),[('{"day":2}',)])
            # A quoted function name alone is not a dynamic input.
            with self.assertRaises(BindingPolicyError):
                check_filter_bindings('SELECT day FROM learning_days WHERE day="coalesce"(2,0)',{})
        finally:db.close()

    def test_existing_function_structural_positions_preserved(self):
        expressions=[
            "trim(status,' ')=:value", "substr(status,1,3)=:value",
            "substring(status,-3,2)=:value", "round(day,2)=:value",
            "date(source_learning_date,'+1 day')=:value",
            "datetime(source_learning_date,'start of day','+1 hour')=:value",
            "strftime('%Y-%m',source_learning_date,'+1 day')=:value",
            "strftime('%Y-%m',:source,'start of month')=:value",
            '"trim"(status,\' \')=:value',
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                check_filter_bindings('SELECT * FROM learning_days WHERE '+expression,{'value':'synthetic','source':'2026-10-04'})
        for expression in ["trim('fixed',' ')=:value", "substr('fixed',1,3)=:value",
                           'round(2,1)=:value', "date('2026-10-04','+1 day')=:value",
                           "strftime('%Y','2026-10-04')=:value", "trim(status,' ')='fixed'",
                           'round(day,2)=3', "substr(status,1,3)='fixed'"]:
            with self.subTest(expression=expression),self.assertRaises(BindingPolicyError):
                check_filter_bindings('SELECT * FROM learning_days WHERE '+expression,{'value':'synthetic'})

    def test_conditions_in_projection_join_having_and_nested_query_are_checked(self):
        queries = [
            'SELECT day=2 FROM learning_days',
            'SELECT CASE WHEN day=2 THEN 1 ELSE 0 END FROM learning_days',
            'SELECT a.day FROM learning_days a JOIN learning_days b ON b.day=2',
            'SELECT count(*) FROM learning_days HAVING count(*)>0',
            'SELECT * FROM learning_days WHERE day IN (SELECT day FROM learning_days WHERE day=2)',
            'WITH chosen AS (SELECT day FROM learning_days WHERE day=2) SELECT * FROM chosen',
        ]
        for sql in queries:
            with self.subTest(sql=sql),self.assertRaises(BindingPolicyError):check_filter_bindings(sql,{})

    def test_indirect_constants_keep_existing_sql_capability_with_explicit_limit(self):
        queries = [
            'WITH wanted AS (SELECT 2 AS n) SELECT day FROM learning_days,wanted WHERE day=n',
            'SELECT 2 AS wanted,day FROM learning_days WHERE day=wanted',
            'SELECT day FROM learning_days WHERE day IN (SELECT 2)',
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.assertEqual(check_filter_bindings(sql,{})['scope'],'direct_literal_predicates_only')

    def test_projection_parameter_is_not_predicate_parameter(self):
        report=check_filter_bindings('SELECT :label,day FROM learning_days',{'label':'synthetic'})
        self.assertEqual(report['direct_predicate_parameters'],[])
        report=check_filter_bindings('SELECT :label,day FROM learning_days WHERE day=:day',{'label':'synthetic','day':2})
        self.assertEqual(report['direct_predicate_parameters'],['day'])

    def test_missing_named_binding_rejected(self):
        with self.assertRaises(BindingPolicyError) as error:
            check_filter_bindings('SELECT day FROM learning_days WHERE day=:required',{'other':2})
        self.assertEqual(error.exception.code,'SQL_FILTER_PARAMETER_MISSING')

    def test_deep_groups_are_a_diagnostic_policy_error_not_recursion_failure(self):
        for depth in (MAX_GROUP_DEPTH+1,9000):
            sql='SELECT day FROM learning_days WHERE day='+('('*depth)+':d'+(')'*depth)
            with self.subTest(depth=depth),self.assertRaises(BindingPolicyError) as error:
                check_filter_bindings(sql,{'d':2})
            self.assertEqual(error.exception.code,'SQL_FILTER_NESTING_LIMIT')
        self.assertEqual(check_filter_bindings('SELECT day FROM learning_days WHERE day='+
            '('*MAX_GROUP_DEPTH+':d'+')'*MAX_GROUP_DEPTH,{'d':2})['direct_predicate_parameters'],['d'])

    def test_successful_parameter_syntax_does_not_prove_meaningful_results(self):
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE learning_days(day INTEGER,uid TEXT)')
            db.execute("INSERT INTO learning_days VALUES(2,'synthetic-a')")
            sql='SELECT day,uid FROM learning_days WHERE day=:day'
            wrong={'day':'2026-10-04'};right={'day':2}
            self.assertEqual(check_filter_bindings(sql,wrong)['direct_predicate_parameters'],['day'])
            self.assertEqual(db.execute(sql,wrong).fetchall(),[])
            self.assertEqual(db.execute(sql,right).fetchall(),[(2,'synthetic-a')])
        finally:db.close()


class ScopedIntegrationTests(unittest.TestCase):
    def make_app(self,path,strict):
        from digital_tutor.application import Application,Clock
        app=Application(path,Clock('2026-10-04T10:00:00+08:00'),mode='passive',strict_runtime=strict)
        app.create_family('a','synthetic-a','mock:a');app.create_family('b','synthetic-b','mock:b')
        with app.store.transaction() as db:
            db.execute('INSERT INTO imports VALUES(?,?)',('synthetic-batch',json.dumps({'course_binding':None})))
            for uid,day in [('synthetic-a',2),('synthetic-b',3)]:
                db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    ('synthetic-batch',uid,day,'attended','incomplete','not_submitted','unknown','unknown',None,None,'synthetic'))
        return app,app.open_session('a','service')

    def test_strict_tool_rejects_literal_and_preserves_uid_and_business_version(self):
        from digital_tutor.learning import query_learning
        from digital_tutor.domain import BusinessError
        with tempfile.TemporaryDirectory() as directory:
            app,token=self.make_app(Path(directory)/'strict.sqlite',True)
            try:
                before=app.store.family('a')['version']
                with self.assertRaises(BusinessError) as error:
                    query_learning(app,token,{'sql':'SELECT day FROM learning_days WHERE day=2','parameters':{'unused':2}})
                self.assertEqual(error.exception.code,'SQL_FILTER_REQUIRES_PARAMETER')
                result=query_learning(app,token,{'sql':'SELECT uid,day FROM learning_days WHERE day=:day','parameters':{'day':2}})
                self.assertEqual(result['rows'],[{'uid':'synthetic-a','day':2}])
                self.assertEqual(query_learning(app,token,{'sql':'SELECT uid FROM learning_days WHERE uid=:uid','parameters':{'uid':'synthetic-b'}})['rows'],[])
                self.assertEqual(app.store.family('a')['version'],before)
            finally:app.close()

    def test_legacy_literal_query_unchanged(self):
        from digital_tutor.learning import query_learning
        with tempfile.TemporaryDirectory() as directory:
            app,token=self.make_app(Path(directory)/'legacy.sqlite',False)
            try:self.assertEqual(query_learning(app,token,{'sql':'SELECT day FROM learning_days WHERE day=2'})['rows'],[{'day':2}])
            finally:app.close()


if __name__=='__main__':unittest.main()
