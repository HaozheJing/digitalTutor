"""Trusted-host confirmation of a source batch's course and timing applicability."""
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import Field, field_validator

from .domain import Strict, ID, Positive, require, timestamp
from .storage import digest, encode

COURSE_COLUMNS = ['batch_id', 'uid', 'course_id', 'course_version', 'cohort_id', 'level',
                  'lesson_map_json', 'coverage_through', 'source_timezone', 'source_note',
                  'confirmed_at', 'binding_version', 'binding_status']


class LessonBinding(Strict):
    day: Annotated[int, Field(strict=True, ge=1, le=240)]
    lesson_ref: ID


class ConfirmBinding(Strict):
    request_id: ID
    batch_id: ID
    expected_family_version: Positive
    source_note: Annotated[str, Field(min_length=1, max_length=2000)]
    course_id: ID | None = None
    course_version: ID | None = None
    cohort_id: ID | None = None
    level: ID | None = None
    lesson_map: list[LessonBinding] | None = None
    coverage_through: str | None = None
    source_timezone: str | None = None

    @field_validator('coverage_through')
    @classmethod
    def validate_coverage(cls, value):
        if value is not None:
            timestamp(value)
        return value

    @field_validator('source_timezone')
    @classmethod
    def validate_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError('source_timezone必须是已知IANA时区') from None
        return value


def confirm_course_binding(app, admin_token, args):
    """Not registered as a model tool. Scoped admin capability plus fresh input required."""
    cmd = ConfirmBinding.model_validate(args)
    patch = cmd.model_dump(exclude_unset=True, exclude={
        'request_id', 'batch_id', 'expected_family_version', 'source_note'})
    require(bool(patch), 'INVALID_SCHEMA', '至少确认或更正一个适用性字段')

    def apply(family, context):
        app.check_version(family, cmd.expected_family_version)
        uid = app.store.family(family)['uid']
        observed_days = {row[0] for row in app.store.connection.execute(
            'SELECT day FROM learning_days WHERE batch_id=? AND uid=?', (cmd.batch_id, uid))}
        require(bool(observed_days), 'BATCH_SCOPE_MISMATCH', '该导入批次不包含当前家庭学情')
        if cmd.lesson_map is not None:
            mapped = [entry.day for entry in cmd.lesson_map]
            require(len(mapped) == len(set(mapped)) and set(mapped).issubset(observed_days),
                    'INVALID_COURSE_MAPPING', '教学日重复或超出当前批次')
        record_id = 'course-binding:' + digest([family, cmd.batch_id])
        old = app.store.connection.execute(
            "SELECT 1 FROM records WHERE kind='course_binding' AND id=? AND family_id=?",
            (record_id, family)).fetchone()
        previous = app.store.get('course_binding', record_id, family) if old else None
        fields = {key: None for key in ('course_id', 'course_version', 'cohort_id', 'level',
                                         'lesson_map', 'coverage_through', 'source_timezone')}
        if previous:
            fields.update({key: previous.get(key) for key in fields})
        fields.update(patch)
        identity_changed = previous and any(fields[key] != previous.get(key)
                                            for key in ('course_id', 'course_version'))
        if identity_changed and 'lesson_map' not in patch:
            fields['lesson_map'] = None
            fields['mapping_reset_reason'] = 'course_identity_changed'
        require(not fields.get('lesson_map') or (fields['course_id'] and fields['course_version']),
                'INVALID_COURSE_MAPPING', '课节映射必须绑定课程及内容版本')
        fields.update(batch_id=cmd.batch_id, uid=uid, source_note=cmd.source_note,
                      confirmed_at=app.clock.iso(), source_kind='trusted_host_confirmation')
        return app.store.put('course_binding', record_id, family, fields,
                             previous['version'] if previous else None)

    # Omitted fields preserve prior confirmations; explicit null clears them.
    # Their request digests must therefore remain distinct.
    return app.write(admin_token, {'admin'}, 'confirm_course_binding', cmd.model_dump(exclude_unset=True), apply)


def binding_rows(app, family):
    rows = []
    for record in app.store.list('course_binding', family):
        row = {column: record.get(column) for column in COURSE_COLUMNS}
        row.update(lesson_map_json=encode(record.get('lesson_map')),
                   binding_version=record['version'],
                   binding_status='confirmed_fields_only')
        rows.append(row)
    return rows
