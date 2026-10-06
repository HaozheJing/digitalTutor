"""Native Hermes cron wakeup adapter; no model execution or message dispatch."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex

from .application import Application, Clock
from .domain import require
from .scheduler import Scheduler


def install_wakeup(profile_root, project_root, business_db, backend_python, schedule,
                   *, paused=True, fixed_now=None):
    """Trusted host operation. Default paused; never exposed as a model tool."""
    from hermes_constants import get_hermes_home
    from cron.jobs import create_job, list_jobs

    profile = Path(profile_root).resolve()
    project = Path(project_root).resolve()
    db = Path(business_db).resolve()
    # Preserve the venv executable symlink: resolving it selects the base interpreter
    # and discards the environment's installed dependencies.
    python = Path(backend_python).absolute()
    require(get_hermes_home().resolve() == profile, 'HERMES_PROFILE_MISMATCH', '必须在指定隔离Hermes进程注册')
    require(db.is_file() and db.is_relative_to(project / 'state'), 'INVALID_CONFIG', '业务库必须是项目state内既有文件')
    require(python.is_file(), 'INVALID_CONFIG', '后端解释器不存在')
    name = 'digitaltutor-business-wakeup'
    require(not any(j.get('name') == name for j in list_jobs(include_disabled=True)),
            'CRON_ALREADY_REGISTERED', '该profile已有固定唤醒任务，请查询原任务')
    scripts = profile / 'scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    script = scripts / 'digitaltutor-wakeup.sh'
    require(not script.exists(), 'CRON_SCRIPT_EXISTS', '既有唤醒脚本需明确更新，不能覆盖')
    receipt = profile / 'business-wakeup.json'
    argv = [str(python), '-m', 'digital_tutor.hermes_cron', '--db', str(db), '--receipt', str(receipt)]
    if fixed_now is not None:
        argv += ['--fixed-now', fixed_now]
    script.write_text('#!/bin/bash\nset -euo pipefail\n'
                      + 'export PYTHONPATH=' + shlex.quote(str(project / 'backend/src')) + '\n'
                      + 'exec ' + shlex.join(argv) + '\n', encoding='utf-8')
    script.chmod(0o700)
    job = create_job(prompt=None, schedule=schedule, name=name, no_agent=True,
                     script=str(script), workdir=str(profile), deliver='local', failure_deliver='local',
                     origin=None, attach_to_session=False, enabled_toolsets=[], paused=paused,
                     paused_reason='Awaiting explicit host activation' if paused else None)
    return {'job': job, 'receipt_path': str(receipt), 'script_path': str(script)}


def wake_business(business_db, receipt_path, fixed_now=None):
    require(Path(business_db).is_file(), 'INVALID_CONFIG', '指定业务库不存在')
    app = Application(business_db, Clock(fixed_now))
    try:
        result = Scheduler(app).tick()
        receipt = {
            'executed_at': datetime.now(timezone.utc).isoformat(),
            'business_now': app.clock.iso(), 'scheduler_result': result,
            'model_invoked': False, 'channel_dispatched': False,
        }
        destination = Path(receipt_path)
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(receipt, ensure_ascii=False) + '\n', encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(destination)
        return receipt
    finally:
        app.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--fixed-now')
    args = parser.parse_args()
    wake_business(args.db, args.receipt, args.fixed_now)
    # Empty stdout is the native no_agent silent-success contract.


if __name__ == '__main__':
    main()
