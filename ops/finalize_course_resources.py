"""Build the handoff map from observed CLI reads; never invent cloud filenames."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('snapshot')
    args = parser.parse_args()
    base = ROOT / 'build/openviking/courses' / args.snapshot

    def read(name):
        return json.loads((base / name).read_text(encoding='utf-8'))

    def write(name, data):
        (base / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    manifest = read('manifest.json')
    coverage = read('coverage-w01-w02-w03-w04.json')
    by_id = {unit['unit_id']: unit for unit in coverage['units']}
    assert len(by_id) == 28 and set(by_id) == {u['unit_id'] for u in manifest['units']}
    units = [{**unit, **{key: by_id[unit['unit_id']][key] for key in
              ['canonical_scope_uri', 'actual_read_uris', 'all_source_blocks_present']}}
             for unit in manifest['units']]
    uri_owner = {uri: unit['unit_id'] for unit in units for uri in unit['actual_read_uris']}
    assert len(uri_owner) == sum(len(u['actual_read_uris']) for u in units)

    tasks = []
    for week in range(1, 5):
        records = sorted((base / 'cli-records').glob(f'task-week-{week:02d}-*.json'))
        record = json.loads(records[-1].read_text(encoding='utf-8'))
        result = record['parsed']['result']
        import_result = result.get('result') or {}
        import_meta = import_result.get('meta') or {}
        tasks.append({'week': week, 'task_id': result['task_id'], 'status': result['status'],
                      'error': result.get('error'), 'processing_errors': import_result.get('errors', []),
                      'failed_files': import_meta.get('failed_files', []),
                      'unsupported_files': import_meta.get('unsupported_files', []),
                      'skipped_files': import_meta.get('skipped_files', []),
                      'processed_file_count': len(import_meta.get('processed_files', [])),
                      'evidence': records[-1].relative_to(base).as_posix()})

    query_cases = [
        ('find-w01-wind', 'LESSON-L2-FOX-D02', False),
        ('find-w01-verbs', 'LESSON-L2-FOX-D03', False),
        ('find-book-wind', 'LESSON-L2-FOX-D02', False),
        ('find-book-day20', 'LESSON-L2-FOX-D20', False),
        ('find-scoped-makeup-day02', 'LESSON-L2-FOX-D02', True),
        ('find-week04-overview', 'L2-FOX-W04-WEEK_OVERVIEW', False),
    ]
    retrieval = []
    for label, expected, strict_scope in query_cases:
        record = read('cli-records/' + label + '.json')
        assert record['exit_code'] == 0 and record['parsed']['ok'] is True
        results = record['parsed']['result']['resources']
        uris = [r['uri'] for r in results]
        owners = [uri_owner.get(uri) for uri in uris]
        scope = record['args'][record['args'].index('--uri') + 1]
        retrieval.append({'case': label, 'query': record['args'][1], 'scope': scope,
                          'expected_unit': expected, 'returned_unit_ids': owners, 'returned_uris': uris,
                          'top1_expected': bool(owners) and owners[0] == expected,
                          'all_results_within_scope': all(u.startswith(scope + '/') for u in uris),
                          'strict_scope_case': strict_scope,
                          'evidence': 'cli-records/' + label + '.json'})

    raw_source = ROOT / manifest['units'][0]['source_file']
    source_unchanged = hashlib.sha256(raw_source.read_bytes()).hexdigest() == manifest['source_sha256']
    generated_files_match = all(hashlib.sha256((base / 'content' / u['relative_path']).read_bytes()).hexdigest()
                                == u['file_sha256'] for u in units)
    passed = (source_unchanged and generated_files_match and coverage['passed'] and
              all(t['status'] == 'completed' and t['error'] is None and not t['processing_errors']
                  and not t['failed_files'] and not t['unsupported_files'] and not t['skipped_files']
                  and t['processed_file_count'] == 7 for t in tasks) and
              all(r['top1_expected'] and r['all_results_within_scope'] for r in retrieval))
    access = {'schema': 'digitaltutor-course-access/1', 'snapshot': args.snapshot,
              'target_root': manifest['target_root'], 'source_sha256': manifest['source_sha256'],
              'usage_status': 'technical_pilot_business_unverified',
              'read_contract': {
                  'lookup': 'Resolve verified course/book/snapshot and original source day. Never derive lesson from weekday.',
                  'full_read': 'Read every actual_read_uris entry in listed order; any failure means incomplete reading.',
                  'discovery': 'find results and generated abstracts are navigation only; map each result back to a source unit.',
                  'provenance': 'Load unit metadata from this manifest even when a parsed fragment omits the frontmatter.',
                  'missing': 'No matching unit or unknown version/mapping must remain unresolved; do not silently substitute adjacent content.',
                  'authority': 'The source declares AI generation and has no confirmed business review. Course goals do not prove student attainment.',
              }, 'units': units}
    validation = {'schema': 'digitaltutor-course-validation/1', 'snapshot': args.snapshot,
                  'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'technical_checks_passed': passed,
                  'source_unchanged': source_unchanged, 'generated_files_match_manifest': generated_files_match,
                  'units': len(units), 'detail_fragments': len(uri_owner), 'tasks': tasks,
                  'full_read_source_coverage_passed': coverage['passed'],
                  'coverage_evidence': 'coverage-w01-w02-w03-w04.json', 'retrieval_cases': retrieval,
                  'source_day_21_exists': any(u['source_day'] == 21 for u in units),
                  'unknown_metadata_preserved': all(u['course_content_version'] is None and u['book_edition'] is None
                                                    and u['actual_lesson_id'] is None and u['cohort_mapping'] is None
                                                    for u in units),
                  'limits': [
                      'Coverage verifies source wording and table row associations, not byte-identical cloud Markdown.',
                      'Six retrieval examples are smoke checks, not a general accuracy benchmark or an agent dialogue evaluation.',
                      'Generated summaries may omit AI-generation/unverified caveats; authoritative provenance must accompany full reads.',
                      'Business accuracy, actual course-version mapping, student learning evidence and agent integration remain unverified.',
                  ]}
    write('access-map.json', access)
    write('validation.json', validation)
    print(json.dumps({k: validation[k] for k in ['technical_checks_passed', 'units', 'detail_fragments',
                                                'source_unchanged', 'full_read_source_coverage_passed']}, ensure_ascii=False))
    print(json.dumps({'retrieval_cases': len(retrieval),
                      'top1_expected': sum(r['top1_expected'] for r in retrieval),
                      'all_results_within_scope': all(r['all_results_within_scope'] for r in retrieval)}))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
