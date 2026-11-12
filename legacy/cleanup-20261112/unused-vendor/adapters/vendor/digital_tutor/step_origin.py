"""Read-only identity of the executed job, separate from an Event's later plan."""
from copy import deepcopy
import json
from .domain import BusinessError
from .scheduling import _versioned_record,step_key


def step_origin(app,family,run_id,job_id=None):
    if not run_id:
        return None
    jobs=[j for j in app.store.list('job',family) if j.get('run_id')==run_id and (not job_id or j['id']==job_id)]
    if not jobs:
        rows=app.store.connection.execute(
            "SELECT id,version,data FROM history WHERE family_id=? AND kind='job' AND json_extract(data,'$.run_id')=? ORDER BY version DESC",
            (family,run_id))
        seen=set()
        for row in rows:
            if row['id'] in seen or (job_id and row['id']!=job_id):
                continue
            seen.add(row['id']);jobs.append({'id':row['id'],'version':row['version'],**json.loads(row['data'])})
    if len(jobs)!=1:
        return {'origin_run_id':run_id,'identity_verified':False,'gap':'origin_job_missing_or_ambiguous'}
    job=jobs[0]
    if job.get('kind')!='step':
        return None
    value={'origin_run_id':run_id,'job_id':job['id'],'event_id':job.get('event_id'),
        'bound_step_key':job.get('step_key'),'bound_event_version':job.get('event_version'),
        'recovery_only':bool(job.get('recovery_only')),
        'recovery_operation_ids':deepcopy(job.get('recovery_operation_ids',[])),
        'identity_verified':False,'bound_next_action':None,
        'scope':'Historical task identity only; no execution capability or proof of completion.'}
    try:
        prior=_versioned_record(app,family,'event',{'id':job.get('event_id'),'version':job.get('event_version')})
        current=app.event(family,job['event_id'])
    except BusinessError:
        value['gap']='bound_event_history_missing'
        return value
    value.update(current_step_key=step_key(current),current_next_action=deepcopy(current.get('next_action')))
    if step_key(prior)!=job.get('step_key'):
        value['gap']='bound_step_history_mismatch'
        return value
    value.update(identity_verified=True,bound_next_action=deepcopy(prior.get('next_action')))
    return value


def report_step_origins(app,family,reports):
    result=[]
    for report in reports:
        origin=step_origin(app,family,report.get('origin_run_id'))
        if origin:
            if origin.get('event_id') and origin['event_id'] not in report.get('event_ids',[]):
                origin={'origin_run_id':report.get('origin_run_id'),'identity_verified':False,
                        'gap':'origin_event_outside_bound_report'}
            result.append({'report_id':report['report_id'],'revision':report['revision'],
                'report_created_at':report.get('created_at'),'execution_origin':origin})
    return result
