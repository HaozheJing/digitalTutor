"""Isolated native create_job -> tick -> script -> business Scheduler acceptance."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid


def main():
    root = Path(__file__).resolve().parents[2]
    run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    work = root / 'state' / 'backend-stage3' / ('native-cron-' + run)
    home = work / 'profile'
    home.mkdir(parents=True, exist_ok=False)
    os.environ['HERMES_HOME'] = str(home)
    os.environ['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
    os.chdir(home)
    (home / 'config.yaml').write_text('security:\n  allow_lazy_installs: false\ncron:\n  max_parallel_jobs: 1\n', encoding='utf-8')
    sys.path[:0] = [str(root / 'backend/src'), str(root / 'vendor/hermes')]
    from digital_tutor.application import Application, Clock
    from digital_tutor.hermes_cron import install_wakeup
    from cron.scheduler_tick import tick
    from cron.jobs import get_job
    from cron.executions import list_executions

    db = work / 'business.sqlite'
    app = Application(db, Clock('2026-10-03T08:00:00+08:00'))
    app.create_family('cron-sim-family', 'cron-sim-uid', 'mock:cron-sim')
    app.close()
    due = datetime.now(timezone.utc) + timedelta(seconds=3)
    installed = install_wakeup(home, root, db, root / 'runtime/backend/bin/python', due.isoformat(),
                               paused=False, fixed_now='2026-10-03T08:00:00+08:00')
    job_id = installed['job']['id']
    while datetime.now(timezone.utc) < due:
        time.sleep(0.2)
    first = tick(verbose=False, sync=True)
    second = tick(verbose=False, sync=True)
    executions = list_executions(job_id=job_id)
    job = get_job(job_id)
    receipt_path = Path(installed['receipt_path'])
    receipt = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else None
    app = Application(db, Clock('2026-10-03T08:00:00+08:00'))
    try:
        jobs = app.store.list('job', 'cron-sim-family')
        operations = app.store.list('operation', 'cron-sim-family')
        messages = app.store.list('message', 'cron-sim-family')
    finally:
        app.close()
    outputs = list((home / 'cron').rglob('*.md'))
    silent_recorded = any('silent (empty output)' in p.read_text(encoding='utf-8') for p in outputs)
    success = (first == 1 and second == 0 and len(jobs) == 1 and jobs[0]['kind'] == 'daily'
               and not operations and not messages and receipt is not None
               and any(e['status'] == 'completed' for e in executions) and silent_recorded)
    result = {
        'run_id': run, 'successful': success, 'hermes_home': str(home), 'cwd': str(home),
        'business_db': str(db), 'native_job_id': job_id,
        'native_first_tick_count': first, 'native_second_tick_count': second,
        'native_job': {k: job.get(k) for k in ('no_agent','deliver','failure_deliver','origin','last_status','state')},
        'native_executions': [{k: e.get(k) for k in ('id','job_id','status','source','claimed_at','finished_at','delivery_outcome','error')} for e in executions],
        'business_jobs': [{'kind': j['kind'], 'status': j['status']} for j in jobs],
        'business_operations': len(operations), 'business_messages': len(messages),
        'silent_output_recorded': silent_recorded, 'wakeup_receipt': receipt,
        'real_model_used': False, 'real_wechat_used': False,
        'source_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (Path(__file__).resolve(), root/'backend/src/digital_tutor/hermes_cron.py')},
    }
    target = root / 'evaluations/backend-stage3' / ('native-cron-' + run + '.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'artifact': str(target), 'successful': success, 'native_executions': len(executions)}))
    if not success:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
