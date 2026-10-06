"""One durable queue with family serialization, revocation and fenced leases."""
from datetime import timedelta
import hashlib
import json
from .domain import require, timestamp
from .storage import encode, new_id, digest
from .runtime_config import allowed_kind, lease_clock, reply_scope


def enabled(app):
    return bool(app.store.connection.execute('SELECT enabled FROM runtime_control WHERE singleton=1').fetchone()[0])


def set_enabled(app, value):
    require(type(value) is bool, 'INVALID_CONFIG', 'enabled must be boolean')
    with app.store.transaction() as db:
        db.execute('UPDATE runtime_control SET enabled=?,generation=generation+1 WHERE singleton=1', (int(value),))


def validate_lease(app, family, lease):
    job = app.store.get('job', lease['job_id'], family)
    require(enabled(app), 'RUNTIME_DISABLED', 'Runtime disabled')
    generation = app.store.connection.execute('SELECT generation FROM runtime_control WHERE singleton=1').fetchone()[0]
    require(job['status'] == 'leased' and job.get('lease_token') == lease['token']
            and job.get('generation') == generation == lease.get('generation')
            and timestamp(job['lease_until']) > lease_clock(app).now(), 'LEASE_LOST', 'Lease expired or replaced')
    require(not job.get('yield_requested'), 'RUN_REVOKED', 'New inbound input superseded this snapshot')
    return job


def yield_to_inbound(app, family, message_id):
    """Caller owns the inbound transaction. Keep serial ownership until process exit."""
    db = app.store.connection
    now = lease_clock(app).iso()
    revocable = {'daily', 'progress', 'step', 'review'}
    if app.strict_runtime:
        # New input can invalidate an ordinary reply draft as well as a plan.
        revocable.add('service')
    for job in app.store.list('job', family):
        if job['status'] != 'leased' or job['kind'] not in revocable:
            continue
        job.update(yield_requested=True, yield_message_id=message_id, revoked_at=now,
                   reason='INBOUND_SUPERSEDED', resume_with_fresh_snapshot=True)
        app.store.put('job', job['id'], family, job, job['version'])
        for row in db.execute('SELECT token_hash,binding FROM sessions WHERE family_id=? AND active=1', (family,)).fetchall():
            binding = json.loads(row['binding'])
            if binding.get('lease', {}).get('job_id') == job['id']:
                db.execute('UPDATE sessions SET active=0 WHERE token_hash=?', (row['token_hash'],))


def job_operation_observation(app, family, job_id):
    """Host-only snapshot of original-job external facts; never model-provided."""
    return sorted([{'id':op['id'],'version':op['version'],'stage':op['stage']}
                   for op in app.store.list('operation',family)
                   if op.get('send_capability',{}).get('job_id')==job_id],key=lambda op:op['id'])


def observed_uncertain_wait(app, family, job, outcome=None):
    """A saved waiting outcome already accounted for these same uncertain facts."""
    if not app.strict_runtime or job['kind']!='service' or not job.get('run_id'):
        return False
    if outcome is None:
        row=app.store.connection.execute(
            "SELECT data FROM records WHERE kind='run_outcome' AND id=? AND family_id=?",
            (job['run_id'],family)).fetchone()
        outcome=json.loads(row['data']) if row else None
    if not outcome or outcome.get('job_id')!=job['id'] or outcome.get('outcome') not in {'waiting','needs_input'}:
        return False
    if 'observed_job_operations' not in outcome:
        return False  # Legacy results retain their existing bounded recovery behavior.
    current=job_operation_observation(app,family,job['id'])
    return (any(op['stage'] in {'unknown','accepted','dispatching'} for op in current)
            and outcome['observed_job_operations']==current)


def recovery_state(app, family, job):
    """Only the original job's external facts authorize internal continuation."""
    if not app.strict_runtime or job['kind'] != 'service' or not job.get('attempt'):
        return None
    operations = [op for op in app.store.list('operation', family)
                  if op.get('send_capability', {}).get('job_id') == job['id']
                  and op['stage'] in {'sent', 'delivered', 'unknown', 'accepted', 'dispatching'}]
    if not operations:
        return None
    row = app.store.connection.execute(
        "SELECT data FROM records WHERE kind='run_outcome' AND id=? AND family_id=?",
        (job.get('run_id'), family)).fetchone()
    outcome = json.loads(row['data']) if row else None
    uncertain = any(op['stage'] in {'unknown', 'accepted', 'dispatching'} for op in operations)
    if observed_uncertain_wait(app,family,job,outcome):
        return None
    if outcome and not uncertain:
        if outcome['outcome'] not in {'waiting','needs_input'} or outcome.get('awaiting_operation_ids')==[]:
            return None
    return {'operation_ids':[op['id'] for op in operations],
            'signature':digest([(op['id'],op['version'],op['stage']) for op in operations]),
            'missing_outcome':outcome is None}


class JobQueue:
    def __init__(self, app):
        self.app = app

    def enqueue(self, family, job_id, data):
        if self.app.store.connection.execute("SELECT 1 FROM records WHERE kind='job' AND id=?", (job_id,)).fetchone():
            return False
        self.app.store.put('job', job_id, family, {'status':'ready', 'attempt':0, 'not_before':None, **data})
        return True

    def claim(self, worker, lease_seconds=60):
        require(1 <= lease_seconds <= 3600, 'INVALID_CONFIG', 'Invalid lease duration')
        app = self.app
        with app.store.transaction() as db:
            if not enabled(app):
                return None
            rows = db.execute("SELECT family_id,id FROM records WHERE kind='job' ORDER BY rowid").fetchall()
            jobs = [(r['family_id'], app.store.get('job', r['id'], r['family_id'])) for r in rows]
            generation = db.execute('SELECT generation FROM runtime_control WHERE singleton=1').fetchone()[0]
            now = lease_clock(app).now()
            # Revoked native processes still own serialization until acknowledged or lease expiry.
            busy = {f for f,j in jobs if j['status'] == 'leased' and j.get('generation') == generation
                    and timestamp(j['lease_until']) > now}
            priority = {'service':0, 'progress':1, 'daily':2, 'review':3, 'step':4}
            for family, job in sorted(jobs, key=lambda pair:priority.get(pair[1]['kind'], 9)):
                recovery = recovery_state(app, family, job)
                recover_failed = job['status'] == 'failed' and recovery and job.get('recovery_attempt', 0) < 3
                if family in busy or (job['status'] not in {'ready', 'leased', 'waiting'} and not recover_failed) or not allowed_kind(app, job['kind']):
                    continue
                if recovery and job.get('recovery_attempt', 0) >= 3:
                    if job['status'] != 'failed':
                        job.update(status='failed', reason='INTERNAL_RECOVERY_EXHAUSTED')
                        app.store.put('job', job['id'], family, job, job['version'])
                    continue
                if job['status'] == 'leased' and app.strict_runtime and not job.get('yield_requested'):
                    failures = job.get('failure_count', 0) + 1
                    delay = (30, 120)[min(failures-1, 1)]
                    job.update(status='failed' if failures >= 3 else 'waiting', reason='LEASE_EXPIRED',
                               failure_count=failures, input_version=app.store.family(family)['version'],
                               not_before=None if failures >= 3 else (now+timedelta(seconds=delay)).isoformat())
                    app.store.put('job', job['id'], family, job, job['version']); continue
                if job.get('business_date') and job['business_date'] != app.clock.now().date().isoformat():
                    job.update(status='expired', reason='past_business_date')
                    app.store.put('job', job['id'], family, job, job['version']); continue
                if job.get('not_before') and timestamp(job['not_before']) > lease_clock(app).now():
                    continue
                if job['status'] == 'waiting' and not job.get('not_before'):
                    # New inbound jobs still run, but cannot make an old observed receipt
                    # wait re-enter as an ordinary reply merely by bumping family version.
                    if observed_uncertain_wait(app,family,job):
                        continue
                    if recovery:
                        # Waiting on the original operation is not awakened by audit or unrelated input.
                        if job.get('recovery_only') and job.get('recovery_signature') == recovery['signature']:
                            continue
                    elif job.get('input_version') == app.store.family(family)['version']:
                        continue
                if job['kind'] == 'service':
                    if not recovery and job.get('message_id') not in reply_scope(app, family):
                        missing = app.strict_runtime and job.get('attempt', 0) > 0 and not db.execute(
                            "SELECT 1 FROM records WHERE kind='run_outcome' AND id=? AND family_id=?",
                            (job.get('run_id'), family)).fetchone()
                        job.update(status='failed' if missing else 'succeeded',
                                   reason='INTERNAL_RESULT_MISSING' if missing else 'no_pending_input')
                        app.store.put('job', job['id'], family, job, job['version']); continue
                if job['kind'] == 'daily':
                    if any(plan['business_date'] == job['business_date'] for plan in app.store.list('plan', family)):
                        job.update(status='succeeded', reason='daily_already_committed')
                        app.store.put('job', job['id'], family, job, job['version']); continue
                    if any(report['resolution'] is None for report in app.store.list('report', family)):
                        if job.get('reason') != 'REPORTS_PENDING' or job.get('input_version') != app.store.family(family)['version']:
                            job.update(status='waiting', reason='REPORTS_PENDING', not_before=None,
                                       input_version=app.store.family(family)['version'])
                            app.store.put('job', job['id'], family, job, job['version'])
                        continue
                if job['kind'] == 'progress':
                    report = app.store.get('report', job['report_key'], family)
                    if report['resolution'] is not None:
                        job.update(status='superseded', reason='report_consumed')
                        app.store.put('job', job['id'], family, job, job['version']); continue
                if not recovery and job['kind'] in {'service', 'daily', 'step'} and not app.service_start <= app.clock.now().hour < app.service_end:
                    continue
                token = new_id('lease')
                previous_run_id = job.get('run_id')
                job.update(recovery_only=bool(recovery))
                if recovery:
                    job.update(recovery_attempt=job.get('recovery_attempt', 0)+1,
                               recovery_operation_ids=recovery['operation_ids'],
                               recovery_signature=recovery['signature'], previous_run_id=previous_run_id)
                job.update(status='leased', worker=worker, lease_token=token, generation=generation,
                           lease_until=(now+timedelta(seconds=lease_seconds)).isoformat(),
                           attempt=job.get('attempt', 0)+1, yield_requested=False,
                           claimed_at=now.isoformat(), run_id=new_id('run'), not_before=None)
                saved = app.store.put('job', job['id'], family, job, job['version'])
                return {'family_id':family, 'job':saved,
                        'lease':{'job_id':job['id'], 'token':token, 'generation':generation}}
        return None

    def session(self, claim):
        app = self.app; job = claim['job']; family = claim['family_id']
        if job['kind'] == 'daily':
            role = 'planner_daily'; kwargs = {}
        elif job['kind'] == 'progress':
            report = app.store.get('report', job['report_key'], family)
            role = 'planner_progress'; kwargs = {'report_id':report['report_id'], 'revision':report['revision']}
        else:
            role = 'service'; kwargs = {}
        token = app.open_session(family, role, **kwargs)
        try:
            with app.store.transaction() as db:
                validate_lease(app, family, claim['lease'])
                hashed = hashlib.sha256(token.encode()).hexdigest()
                row = db.execute('SELECT binding FROM sessions WHERE token_hash=?', (hashed,)).fetchone()
                binding = json.loads(row['binding'])
                binding.update(lease=claim['lease'], job_id=job['id'], job_kind=job['kind'],
                               run_id=job['run_id'], mode=app.mode, attempt=job['attempt'],
                               reply_scope={} if job.get('recovery_only') else reply_scope(app, family),
                               no_contact=job['kind']=='review' or bool(job.get('recovery_only')),
                               recovery_only=bool(job.get('recovery_only')),
                               recovery_operation_ids=job.get('recovery_operation_ids', []),
                               previous_run_id=job.get('previous_run_id'),
                               output_mode=job.get('output_mode', 'family_reply'))
                db.execute('UPDATE sessions SET binding=? WHERE token_hash=?', (encode(binding), hashed))
        except BaseException:
            app.revoke_session(token)
            raise
        return token

    def finish(self, claim, status='succeeded', reason=None, retry_at=None):
        require(status in {'succeeded', 'waiting', 'failed', 'ready'}, 'INVALID_JOB_STATUS', 'Invalid job status')
        app = self.app; family = claim['family_id']
        with app.store.transaction():
            job = app.store.get('job', claim['job']['id'], family)
            generation = app.store.connection.execute('SELECT generation FROM runtime_control WHERE singleton=1').fetchone()[0]
            require(enabled(app) and job['status'] == 'leased'
                    and job.get('generation') == generation == claim['lease'].get('generation')
                    and job.get('lease_token') == claim['lease']['token']
                    and timestamp(job['lease_until']) > lease_clock(app).now(),
                    'LEASE_LOST', 'Old run cannot terminate another lease')
            if job.get('yield_requested'):
                job.update(status='ready', reason='INBOUND_SUPERSEDED', not_before=None,
                           yield_requested=False, released_at=lease_clock(app).iso(), resume_with_fresh_snapshot=True)
            else:
                if job.get('recovery_only'):
                    state = recovery_state(app, family, job)
                    if state:
                        job['recovery_signature'] = state['signature']
                job.update(status=status, reason=reason, not_before=retry_at,
                           input_version=app.store.family(family)['version'], finished_at=lease_clock(app).iso())
            return app.store.put('job', job['id'], family, job, job['version'])

    def fail(self, claim, reason):
        """Bound errors, not business waiting or preemption. Explicit recovery can reset failures."""
        app = self.app
        with app.store.transaction():
            job = app.store.get('job', claim['job']['id'], claim['family_id'])
            generation = app.store.connection.execute('SELECT generation FROM runtime_control WHERE singleton=1').fetchone()[0]
            require(enabled(app) and job['status'] == 'leased'
                    and job.get('generation') == generation == claim['lease'].get('generation')
                    and job.get('lease_token') == claim['lease']['token']
                    and timestamp(job['lease_until']) > lease_clock(app).now(), 'LEASE_LOST', 'Old run failure')
            failure_key = 'recovery_failure_count' if job.get('recovery_only') else 'failure_count'
            failures = job.get(failure_key, 0) + (0 if job.get('yield_requested') else 1)
            job[failure_key] = failures
            app.store.put('job', job['id'], claim['family_id'], job, job['version'])
        delay = (30, 120)[min(max(failures-1, 0), 1)]
        retry_at = (lease_clock(app).now()+timedelta(seconds=delay)).isoformat()
        return self.finish(claim, 'failed' if failures >= 3 else 'waiting', reason, None if failures >= 3 else retry_at)

    def renew(self, claim, lease_seconds=60):
        require(1 <= lease_seconds <= 3600, 'INVALID_CONFIG', 'Invalid lease duration')
        with self.app.store.transaction():
            job = validate_lease(self.app, claim['family_id'], claim['lease'])
            job['lease_until'] = (lease_clock(self.app).now()+timedelta(seconds=lease_seconds)).isoformat()
            return self.app.store.put('job', job['id'], claim['family_id'], job, job['version'])
