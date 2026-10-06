"""Provenance, backwards compatibility and trusted course-binding checks."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import openpyxl
from pydantic import ValidationError
from digital_tutor.application import Application, Clock
from digital_tutor.course_binding import confirm_course_binding
from digital_tutor.domain import BusinessError
from digital_tutor.learning import import_workbook, query_learning
from digital_tutor.source_rows import SOURCE_SCHEMA
from digital_tutor.storage import encode


def workbook_fixture(path, bad_identity=False, group_conflict=False, bad_summary=False):
    book = openpyxl.Workbook()
    summary = book.active
    summary.title = '汇总'
    summary.append(['用户ID', '分组', '到课', '完课', '作业(提交/配置)', 'AI伴读(提交/配置)', '打卡'])
    learners = [('mock-1', 'group-a'), ('mock-2', 'group-b')]
    for uid, group in learners:
        summary.append([uid, group, 'not-a-ratio' if bad_summary else '240/240', '240/240', '1/1', '1/1', '0/240'])
    for name in ['到课', '完课', '作业', 'AI伴读', '打卡']:
        sheet = book.create_sheet(name)
        sheet.append(['用户ID', '分组'] + [f'第{day}天' for day in range(1, 241)] + ['完成/有效'])
        sheet.append([None, None] + ['03-16' if day == 106 else '10-13' for day in range(1, 241)])
        for uid, group in learners:
            values = []
            for day in range(1, 241):
                values.append({'到课': '已到课', '完课': '已完课', '打卡': '未打卡',
                               '作业': '已提交' if day == 106 else '无作业',
                               'AI伴读': '已提交' if day == 106 else '无AI作业'}[name])
            sheet.append([uid, group] + values + ['source-summary-preserved'])
    daily = book.create_sheet('作业-按天')
    daily.append(['用户ID', '分组', '第106天', '提交/配置'])
    daily.append([None, None, '03-16 W22'])
    daily.append([None, None, '虚构表达题'])
    for uid, group in learners:
        daily.append(['unexpected-uid' if bad_identity else uid,
                      'different-source-group' if group_conflict else group, '已提交', '1/1'])
    detail = book.create_sheet('作业明细')
    detail.append(['用户ID', '分组', '第N天', '学习日期', '章节', '提交状态', '提交次数', '提交时间',
                   '作业题目', 'AI表达题类型', 'AI表达题名称', '题干', 'AI引导语', '用户回答', '回答轮数', '含语音回答数'])
    for uid, group in learners:
        detail.append([uid, group, 106, '2026-03-16', '虚构章节', '已提交', 1, '2026-03-16 10:00:00',
                       '虚构题目', '虚构题型', '虚构名称', None, '虚构引导', '虚构回答-' + uid, 2, 1])
    book.save(path)
    book.close()


class Stage3LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / 'synthetic.xlsx'
        workbook_fixture(self.source)
        self.app = Application(Path(self.tmp.name) / 'learning.sqlite', Clock('2026-10-03T09:00:00+08:00'))
        self.app.create_family('f', 'mock-1', 'mock:f')
        self.app.create_family('other', 'mock-2', 'mock:other')
        self.service = self.app.open_session('f', 'service')
        self.admin = self.app.open_session('f', 'admin')

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def query(self, sql, parameters=None):
        return query_learning(self.app, self.service, {'sql': sql, 'parameters': parameters or {}})['rows']

    def binding_args(self, batch, **fields):
        return {'request_id': 'binding-1', 'batch_id': batch,
                'expected_family_version': self.app.store.family('f')['version'],
                'source_note': '虚构管理员已核实课程资料', **fields}

    def test_eight_sheets_and_all_source_fields_are_readable(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        meta = import_workbook(self.app, self.source)
        self.assertEqual((meta['user_count'], meta['learning_day_count'], meta['assignment_count']), (2, 480, 2))
        self.assertEqual(meta['source_sheet_count'], 8)
        self.assertEqual(meta['source_row_count'], 16)
        self.assertEqual(self.query('SELECT count(DISTINCT sheet_name) AS n FROM learning_source_rows'), [{'n': 8}])
        self.assertEqual(self.query('SELECT ai_expression_type,ai_expression_name,source_group FROM assignment_details'),
                         [{'ai_expression_type': '虚构题型', 'ai_expression_name': '虚构名称', 'source_group': 'group-a'}])
        homework = self.query('SELECT day,source_date_label,question_title,evidence_locator FROM homework_days')[0]
        self.assertEqual(homework, {'day': 106, 'source_date_label': '03-16 W22',
                                   'question_title': '虚构表达题', 'evidence_locator': '作业-按天!C4'})
        summary = self.query("SELECT raw_value,numerator,denominator FROM learning_summaries WHERE metric='到课'")[0]
        self.assertEqual(summary, {'raw_value': '240/240', 'numerator': 240, 'denominator': 240})
        raw = self.query("SELECT values_json,metadata_json FROM learning_source_rows WHERE sheet_name='到课'")[0]
        self.assertEqual(json.loads(raw['values_json'])[-1], 'source-summary-preserved')
        self.assertEqual(json.loads(raw['metadata_json'])[0][107], '03-16')
        self.assertEqual(import_workbook(self.app, self.source)['outcome'], 'duplicate')
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_all_extension_tables_remain_family_scoped(self):
        import_workbook(self.app, self.source)
        for table in SOURCE_SCHEMA:
            with self.subTest(table=table):
                self.assertEqual(self.query(f'SELECT DISTINCT uid FROM {table}'), [{'uid': 'mock-1'}])
                self.assertEqual(self.query(f'SELECT uid FROM {table} WHERE uid=:uid', {'uid': 'mock-2'}), [])
        with self.assertRaises(BusinessError) as error:
            self.query('DELETE FROM learning_source_rows')
        self.assertEqual(error.exception.code, 'SQL_REJECTED')

    def test_legacy_database_reimport_only_adds_provenance(self):
        meta = import_workbook(self.app, self.source)
        old_rows = self.query('SELECT * FROM learning_days')
        old_assignments = self.query('SELECT * FROM assignments')
        with self.app.store.transaction() as db:
            for table in SOURCE_SCHEMA:
                db.execute(f'DELETE FROM {table}')
            db.execute('DELETE FROM learning_extensions')
            legacy = {key: value for key, value in meta.items() if key not in {
                'outcome', 'extension_version', 'source_sheet_count', 'source_row_count'}}
            db.execute('UPDATE imports SET metadata=?', (encode(legacy),))
        self.assertEqual(self.query('SELECT * FROM assignment_details'), [])
        upgraded = import_workbook(self.app, self.source)
        self.assertEqual(upgraded['outcome'], 'upgraded')
        self.assertEqual(upgraded['imported_at'], legacy['imported_at'])
        self.assertEqual(self.query('SELECT * FROM learning_days'), old_rows)
        self.assertEqual(self.query('SELECT * FROM assignments'), old_assignments)
        self.assertEqual(import_workbook(self.app, self.source)['outcome'], 'duplicate')

    def test_bad_eighth_sheet_identity_rolls_back_all_rows(self):
        workbook_fixture(self.source, bad_identity=True)
        with self.assertRaises(BusinessError) as error:
            import_workbook(self.app, self.source)
        self.assertEqual(error.exception.code, 'IMPORT_SCHEMA')
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM imports').fetchone()[0], 0)
        self.assertEqual(self.app.store.connection.execute('SELECT count(*) FROM learning_days').fetchone()[0], 0)

    def test_unknowns_and_group_conflicts_are_preserved(self):
        workbook_fixture(self.source, group_conflict=True, bad_summary=True)
        meta = import_workbook(self.app, self.source)
        self.assertTrue(any(flag['code'] == 'source_group_conflict' for flag in meta['quality_flags']))
        self.assertEqual(self.query("SELECT source_group FROM homework_days"), [{'source_group': 'different-source-group'}])
        self.assertEqual(self.query("SELECT numerator,denominator FROM learning_summaries WHERE metric='到课'"),
                         [{'numerator': None, 'denominator': None}])
        self.assertEqual(self.query('SELECT coverage_through,source_timezone,course_binding FROM learning_batches'),
                         [{'coverage_through': None, 'source_timezone': None, 'course_binding': None}])
        self.assertEqual(self.query('SELECT * FROM course_bindings'), [])

    def test_course_binding_is_host_only_scoped_and_idempotent(self):
        meta = import_workbook(self.app, self.source)
        args = self.binding_args(meta['batch_id'], course_id='mock-course', course_version='mock-v1',
                                 lesson_map=[{'day': 106, 'lesson_ref': 'mock-lesson-106'}])
        with self.assertRaises(BusinessError) as error:
            confirm_course_binding(self.app, self.service, args)
        self.assertEqual(error.exception.code, 'ROLE_FORBIDDEN')
        result = confirm_course_binding(self.app, self.admin, args)
        self.assertEqual(result['outcome'], 'applied')
        self.assertEqual(confirm_course_binding(self.app, self.admin, args)['outcome'], 'duplicate')
        self.assertEqual(self.query('SELECT uid,course_id,coverage_through FROM course_bindings'),
                         [{'uid': 'mock-1', 'course_id': 'mock-course', 'coverage_through': None}])
        other = self.app.open_session('other', 'service')
        self.assertEqual(query_learning(self.app, other, {'sql': 'SELECT * FROM course_bindings'})['rows'], [])

    def test_binding_correction_preserves_omitted_fields_and_history(self):
        batch = import_workbook(self.app, self.source)['batch_id']
        first = confirm_course_binding(self.app, self.admin, self.binding_args(batch,
            course_id='mock-course', course_version='mock-v1'))['data']
        second_args = self.binding_args(batch, request_id='binding-2', source_timezone='Asia/Shanghai',
                                        coverage_through='2026-09-21T20:00:00+08:00')
        second = confirm_course_binding(self.app, self.admin, second_args)['data']
        self.assertEqual(second['course_id'], first['course_id'])
        self.assertEqual(second['version'], 2)
        self.assertEqual(self.app.store.connection.execute(
            "SELECT count(*) FROM history WHERE kind='course_binding' AND id=?", (first['id'],)).fetchone()[0], 2)
        with self.assertRaises(BusinessError) as error:
            confirm_course_binding(self.app, self.admin, {**second_args, 'request_id': 'stale-write'})
        self.assertEqual(error.exception.code, 'STALE_INPUT')

    def test_binding_rejects_unknown_batch_duplicate_day_and_ambiguous_time(self):
        batch = import_workbook(self.app, self.source)['batch_id']
        with self.assertRaises(BusinessError) as error:
            confirm_course_binding(self.app, self.admin, self.binding_args('not-imported', course_id='mock'))
        self.assertEqual(error.exception.code, 'BATCH_SCOPE_MISMATCH')
        args = self.binding_args(batch, course_id='mock', course_version='v1',
            lesson_map=[{'day': 106, 'lesson_ref': 'one'}, {'day': 106, 'lesson_ref': 'two'}])
        with self.assertRaises(BusinessError) as error:
            confirm_course_binding(self.app, self.admin, args)
        self.assertEqual(error.exception.code, 'INVALID_COURSE_MAPPING')
        with self.assertRaises(BusinessError):
            confirm_course_binding(self.app, self.admin, self.binding_args(batch, coverage_through='2026-01-01'))
        with self.assertRaises(ValidationError):
            confirm_course_binding(self.app, self.admin, self.binding_args(batch, source_timezone='Unknown/Time'))

    def test_course_version_correction_invalidates_old_lesson_mapping(self):
        batch = import_workbook(self.app, self.source)['batch_id']
        first = confirm_course_binding(self.app, self.admin, self.binding_args(batch,
            course_id='mock-course', course_version='v1',
            lesson_map=[{'day': 106, 'lesson_ref': 'v1-lesson'}]))['data']
        second = confirm_course_binding(self.app, self.admin, self.binding_args(batch,
            request_id='version-correction', course_version='v2'))['data']
        self.assertIsNone(second['lesson_map'])
        self.assertEqual(second['mapping_reset_reason'], 'course_identity_changed')
        historical = self.app.store.connection.execute(
            "SELECT data FROM history WHERE kind='course_binding' AND id=? AND version=1", (first['id'],)).fetchone()[0]
        self.assertEqual(json.loads(historical)['lesson_map'][0]['lesson_ref'], 'v1-lesson')

    def test_binding_idempotency_distinguishes_omitted_and_explicit_null(self):
        batch = import_workbook(self.app, self.source)['batch_id']
        args = self.binding_args(batch, course_id='mock-course', course_version='v1')
        confirm_course_binding(self.app, self.admin, args)
        with self.assertRaises(BusinessError) as error:
            confirm_course_binding(self.app, self.admin, {**args, 'source_timezone': None})
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
