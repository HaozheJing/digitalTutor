"""Read-only locator index tests, no model API and no answer injection."""
from pathlib import Path
import json,tempfile,unittest
from digital_tutor.application import Application,Clock
from digital_tutor.context import build_context
from digital_tutor.learning import SOURCE_INDEX_LIMIT,SOURCE_KEY_LIMIT,SOURCE_KEY_LENGTH_LIMIT,SOURCE_JSON_BYTE_LIMIT,source_index,query_learning
from digital_tutor.storage import encode
from digital_tutor.tools import ToolRegistry


class SourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=Application(Path(self.temp.name)/'test.sqlite',
            Clock('2026-10-04T10:00:00+08:00'),mode='passive',strict_runtime=True)
        for name in ('f','g'):self.app.create_family(name,'synthetic-'+name,'mock:'+name)
        self.service=self.app.open_session('f','service')

    def tearDown(self):self.app.close();self.temp.cleanup()

    def seed(self,batch='batch-a',family='f',table='learning_days',metadata=None):
        meta=metadata if metadata is not None else {'batch_id':batch,'imported_at':'2026-10-04T08:00:00+08:00',
            'coverage_through':'2026-10-04T07:30:00+08:00','source_timezone':'Asia/Shanghai',
            'course_binding':'EXACT_SOURCE_CONTENT_MUST_BE_READ_BY_SQL'}
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO imports VALUES(?,?)',(batch,encode(meta)))
            if table=='learning_days':
                db.execute('INSERT INTO learning_days VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (batch,'synthetic-'+family,1,'unknown','unknown','unknown','unknown','unknown',None,None,'synthetic-source'))
            elif table=='assignments':
                db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (batch,'synthetic-'+family,1,2,None,'unknown',None,None,None,None,None,None,None,None,'[]'))

    def index(self):return build_context(self.app,self.service)['learning']['source_index']

    def test_index_locates_nonempty_metadata_without_copying_content(self):
        self.seed();context=build_context(self.app,self.service);index=context['learning']['source_index']
        entry=index['batches'][0]
        self.assertEqual(entry['batch_id'],'batch-a');self.assertIn('course_binding',entry['nonempty_metadata_fields'])
        self.assertNotIn('EXACT_SOURCE_CONTENT_MUST_BE_READ_BY_SQL',json.dumps(context))
        self.assertEqual(context['learning']['status'],'not_loaded');self.assertEqual(context['learning']['course_binding'],'unknown')
        result=query_learning(self.app,self.service,{'sql':'SELECT course_binding FROM learning_batches WHERE batch_id=:batch',
                                                     'parameters':{'batch':'batch-a'}})
        self.assertEqual(result['rows'][0]['course_binding'],'EXACT_SOURCE_CONTENT_MUST_BE_READ_BY_SQL')

    def test_other_family_and_unowned_imports_are_never_indexed(self):
        self.seed('mine');self.seed('other',family='g');self.seed('no-rows',table=None)
        index=self.index();self.assertEqual([r['batch_id'] for r in index['batches']],['mine'])
        self.assertEqual(index['total_count'],1)
        result=query_learning(self.app,self.service,{'sql':'SELECT batch_id FROM learning_batches'})
        self.assertEqual(result['rows'],[{'batch_id':'mine'}])

    def test_assignment_only_owned_batch_is_discoverable(self):
        self.seed('assignment-source',table='assignments')
        entry=self.index()['batches'][0];self.assertEqual(entry['owned_row_tables'],['assignments'])
        self.assertEqual(entry['batch_id'],'assignment-source')

    def test_empty_family_has_explicit_empty_index(self):
        self.seed('only-other',family='g');index=self.index()
        self.assertEqual(index['batches'],[]);self.assertEqual(index['total_count'],0);self.assertFalse(index['has_more'])

    def test_unknown_metadata_stays_null_and_sql_does_not_crash(self):
        self.seed(metadata={});entry=self.index()['batches'][0]
        self.assertIsNone(entry['imported_at']);self.assertIsNone(entry['coverage_through'])
        self.assertEqual(entry['nonempty_metadata_fields'],[])
        result=query_learning(self.app,self.service,{'sql':'SELECT batch_id,course_binding,coverage_through FROM learning_batches'})
        self.assertEqual(result['rows'],[{'batch_id':'batch-a','course_binding':None,'coverage_through':None}])

    def test_nonempty_does_not_promote_unvalidated_binding(self):
        self.seed(metadata={'course_binding':'unknown','coverage_through':None,'imported_at':None,'source_timezone':None})
        context=build_context(self.app,self.service)
        self.assertEqual(context['learning']['course_binding'],'unknown')
        self.assertIn('course_binding',context['learning']['source_index']['batches'][0]['nonempty_metadata_fields'])

    def test_index_is_bounded_and_stable_across_insertion_order(self):
        for n in reversed(range(SOURCE_INDEX_LIMIT+5)):self.seed('batch-%03d'%n)
        first=self.index();second=self.index()
        self.assertEqual(first,second);self.assertEqual(first['returned_count'],SOURCE_INDEX_LIMIT)
        self.assertEqual(first['total_count'],SOURCE_INDEX_LIMIT+5);self.assertTrue(first['has_more'])
        self.assertEqual([r['batch_id'] for r in first['batches']],['batch-%03d'%n for n in range(SOURCE_INDEX_LIMIT)])

    def test_repeated_index_does_not_change_family_records_or_history(self):
        self.seed();before=self.app.store.family('f')['version']
        count=self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0]
        records=self.app.store.connection.execute('SELECT count(*) FROM records').fetchone()[0]
        for _ in range(4):self.index()
        self.assertEqual(before,self.app.store.family('f')['version'])
        self.assertEqual(count,self.app.store.connection.execute('SELECT count(*) FROM history').fetchone()[0])
        self.assertEqual(records,self.app.store.connection.execute('SELECT count(*) FROM records').fetchone()[0])

    def test_malformed_timestamp_does_not_preload_arbitrary_text(self):
        self.seed(metadata={'imported_at':'ANSWER_HIDDEN_IN_TIMESTAMP','coverage_through':'bad date','course_binding':'ANSWER_BODY'})
        index=self.index();entry=index['batches'][0]
        self.assertIsNone(entry['imported_at']);self.assertIsNone(entry['coverage_through'])
        self.assertEqual(entry['invalid_time_fields'],['imported_at','coverage_through'])
        self.assertNotIn('ANSWER_',json.dumps(index))

    def test_loader_cannot_replace_host_family_index(self):
        self.seed();expected=self.index()
        context=build_context(self.app,self.service,material_loader=lambda _: {'learning':{'status':'loaded','source_index':{'foreign':'batch'}}})
        self.assertEqual(context['learning']['source_index'],expected)

    def test_schema_notes_explain_queryable_metadata_without_case_specific_content(self):
        registry=ToolRegistry(self.app,self.service);schema=registry.call('learning.schema',{})
        self.assertTrue(schema['ok']);self.assertIn('course_binding',schema['result']['table_notes']['learning_batches'])
        specs={r['name']:r for r in registry.describe()}
        self.assertIn('learning_batches',specs['learning.query_sql']['description'])
        self.assertNotIn('EXACT_SOURCE_CONTENT',json.dumps(schema))

    def test_canonical_import_key_is_shared_by_index_and_sql(self):
        self.seed('actual-batch',metadata={'batch_id':'wrong-metadata-id'})
        self.assertEqual(self.index()['batches'][0]['batch_id'],'actual-batch')
        result=query_learning(self.app,self.service,{'sql':'SELECT batch_id FROM learning_batches'})
        self.assertEqual(result['rows'],[{'batch_id':'actual-batch'}])

    def test_shared_batch_is_counted_once_and_owned_table_presence_is_family_scoped(self):
        self.seed('shared')
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('shared','synthetic-g',1,2,None,'unknown',None,None,None,None,None,None,None,None,'[]'))
        index=self.index();self.assertEqual(index['total_count'],1)
        self.assertEqual(index['batches'][0]['owned_row_tables'],['learning_days'])

    def test_json_top_level_keys_only_no_nested_keys_or_values(self):
        value={'resource_link':'https://private.invalid/SECRET_VALUE',
               'nested':{'NESTED_SECRET_KEY':'NESTED_SECRET_VALUE'},'rows':[{'ARRAY_SECRET_KEY':'ARRAY_VALUE'}]}
        self.seed(metadata={'course_binding':json.dumps(value)})
        entry=self.index()['batches'][0]
        structure=entry['metadata_structure']['course_binding']
        self.assertEqual(structure['keynames'],['nested','resource_link','rows'])
        self.assertEqual(structure['status'],'object')
        serialized=json.dumps(entry)
        for hidden in ('https://','SECRET_','NESTED_','ARRAY_'):self.assertNotIn(hidden,serialized)
        result=query_learning(self.app,self.service,{'sql':'SELECT course_binding FROM learning_batches'})
        self.assertEqual(json.loads(result['rows'][0]['course_binding']),value)

    def test_structure_is_generic_for_queryable_metadata_fields(self):
        self.seed(metadata={'source_timezone':json.dumps({'generic_field':'HIDDEN_TIMEZONE_VALUE'})})
        entry=self.index()['batches'][0]
        self.assertEqual(entry['metadata_structure']['source_timezone']['keynames'],['generic_field'])
        self.assertNotIn('HIDDEN_TIMEZONE_VALUE',json.dumps(entry))

    def test_invalid_array_scalar_and_oversized_json_are_unknown(self):
        values=['invalid {','["ARRAY_VALUE"]','"scalar"','42','null',
                json.dumps({'oversized':'x'*SOURCE_JSON_BYTE_LIMIT})]
        for i,value in enumerate(values):self.seed('unknown-%d'%i,metadata={'course_binding':value})
        for entry in self.index()['batches']:
            self.assertEqual(entry['metadata_structure']['course_binding'],{'status':'unknown','keynames':[]})

    def test_top_level_keys_are_bounded_sorted_and_long_keys_not_truncated(self):
        value={'key-%02d'%i:'HIDDEN_VALUE' for i in reversed(range(SOURCE_KEY_LIMIT+5))}
        value['x'*(SOURCE_KEY_LENGTH_LIMIT+1)]='hidden';value['bad\nkey']='hidden'
        value['this is source body']='hidden';value['https://secret.invalid']='hidden';value['正文']='hidden'
        self.seed(metadata={'course_binding':json.dumps(value)})
        structure=self.index()['batches'][0]['metadata_structure']['course_binding']
        self.assertEqual(structure['keynames'],['key-%02d'%i for i in range(SOURCE_KEY_LIMIT)])
        self.assertTrue(structure['has_more']);self.assertNotIn('HIDDEN_VALUE',json.dumps(structure))

    def test_json_structure_is_family_scoped_and_repeated_reads_are_readonly(self):
        self.seed('mine',metadata={'course_binding':json.dumps({'own_key':'OWN_VALUE'})})
        self.seed('other',family='g',metadata={'course_binding':json.dumps({'FOREIGN_KEY':'FOREIGN_VALUE'})})
        before=list(self.app.store.connection.iterdump())
        first=self.index()
        for _ in range(3):self.assertEqual(self.index(),first)
        self.assertEqual(before,list(self.app.store.connection.iterdump()))
        self.assertNotIn('FOREIGN_',json.dumps(first));self.assertNotIn('OWN_VALUE',json.dumps(first))

    def test_empty_object_is_known_structure_but_not_confirmed_binding(self):
        self.seed(metadata={'course_binding':'{}'})
        context=build_context(self.app,self.service)
        structure=context['learning']['source_index']['batches'][0]['metadata_structure']['course_binding']
        self.assertEqual(structure['status'],'object');self.assertEqual(structure['keynames'],[])
        self.assertFalse(structure['has_more']);self.assertEqual(context['learning']['course_binding'],'unknown')
