"""Workbench time application authorizes execution at the selected business time."""
import json
import sqlite3
import sys
from contextlib import closing
import sql_tools as s
import test_mode as tm


def apply_time(action, as_of):
    if action not in ('activate', 'advance'):
        raise ValueError('Unsupported clock action')
    automatic=__import__('manual_workbench').clock_authority(action,as_of)
    if action == 'activate':
        result = tm.activate(as_of, automatic)
    else:
        # Keep validation and busy/receipt guards ahead of any clock mutation.
        from console_control import idle
        with closing(sqlite3.connect(s.DB)) as c:
            idle(c, receipt_complete=True)
        result = tm.advance(as_of)
        result['auto_highlights'] = automatic
        result.pop('manual_service_task_id', None)
        with closing(sqlite3.connect(s.DB)) as c, c:
            c.execute("UPDATE metadata SET value=? WHERE key='test_mode'", (s.encoded(result),))
    return {**result, 'execution': 'automatic_due_tasks_and_existing_highlights' if automatic else 'manual_only'}


if __name__ == '__main__':
    try:
        value = apply_time(sys.argv[1], sys.argv[2])
    except ValueError as error:
        value = {'status': 'rejected', 'error': str(error)}
    print(json.dumps(value, ensure_ascii=False))
