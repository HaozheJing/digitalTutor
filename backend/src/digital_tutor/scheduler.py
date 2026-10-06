"""Clock-driven host scheduling, independent of model reasoning or text generation."""
from datetime import timedelta
from threading import Event, Thread
from .domain import BusinessError, timestamp
from .jobs import JobQueue, enabled, recovery_state
from .storage import digest
from .tools import ToolRegistry
from .send_policy import action_paused
from .runtime_config import allowed_kind


def step_key(event):
    return digest({'event_id':event['id'],'next_action':event['next_action']})


class Scheduler:
    def __init__(self, app):
        self.app=app; self.queue=JobQueue(app)

    def tick(self):
        app=self.app; created=0; now=app.clock.now(); today=now.date().isoformat()
        with app.store.transaction() as db:
            if not enabled(app):
                return {'created':0,'enabled':False}
            families=[r['id'] for r in db.execute('SELECT id FROM families')]
            for family in families:
                if allowed_kind(app, 'daily') and app.service_start<=now.hour<app.service_end and not db.execute('SELECT 1 FROM plans WHERE family_id=? AND business_date=?',(family,today)).fetchone():
                    created+=self.queue.enqueue(family,'daily:'+digest([family,today]),{'kind':'daily','business_date':today})
                if not allowed_kind(app, 'step'):
                    continue
                for event in app.store.list('event',family):
                    current=app.event(family,event['id'])
                    if current['update_pending'] or event['status'] in {'done','cancelled'}:
                        continue
                    waiting=event.get('waiting')
                    if waiting and waiting.get('review_at') and timestamp(waiting['review_at'])<=now:
                        created+=self.queue.enqueue(family,'review:'+digest([event['id'],waiting]),
                                  {'kind':'review','event_id':event['id'],'event_version':event['version'],'contact_allowed':False})
                    if event['status'] in {'pending','in_progress'} and event.get('next_action'):
                        if action_paused(app,family,event['next_action']['action_kind'],event['id']):
                            continue
                        key=step_key(event)
                        created+=self.queue.enqueue(family,'step:'+digest([key,today]),{'kind':'step','event_id':event['id'],
                                  'step_key':key,'event_version':event['version'],'business_date':today})
        return {'created':created,'enabled':True}


class Worker:
    """The handler can be deterministic or a native Hermes adapter; the loop is shared."""
    def __init__(self, app, handler, name='worker', lease_seconds=60):
        self.app=app; self.queue=JobQueue(app); self.handler=handler; self.name=name
        self.lease_seconds=lease_seconds

    def run_once(self):
        claim=self.queue.claim(self.name,self.lease_seconds)
        if not claim:
            return {'ran':False}
        token=None; stop=Event()
        def heartbeat():
            while not stop.wait(max(0.1,self.lease_seconds/3)):
                try:
                    self.queue.renew(claim,self.lease_seconds)
                except BusinessError:
                    break
        thread=Thread(target=heartbeat,daemon=True)
        thread.start()
        try:
            token=self.queue.session(claim)
            job=claim['job']
            if job['kind'] in {'step','review'}:
                event=self.app.event(claim['family_id'],job['event_id'])
                if event['update_pending'] or event['status'] in {'done','cancelled'} or (job['kind']=='step' and (event['status']=='waiting' or step_key(event)!=job['step_key'])):
                    self.queue.finish(claim,'succeeded','obsolete_step')
                    return {'ran':True,'outcome':'obsolete_step'}
                if job['kind']=='step' and action_paused(self.app,claim['family_id'],event['next_action']['action_kind'],event['id']):
                    self.queue.finish(claim,'waiting','CONTROL_ACTIVE')
                    return {'ran':True,'outcome':'paused'}
            decision=self.handler(job,ToolRegistry(self.app,token)) or {}
            if decision.get('retryable_error'):
                raise BusinessError(decision['retryable_error'], 'Native run failed')
            if self.app.strict_runtime and job['kind'] in {'service','step','review'}:
                family, binding = self.app.auth(token, {'service'})
                outcome_id = decision.get('outcome_id')
                if outcome_id != binding['run_id']:
                    raise BusinessError('INTERNAL_RESULT_MISSING', 'Run needs a verified internal outcome')
                outcome = self.app.store.get('run_outcome', outcome_id, family)
                if outcome.get('job_id') != job['id']:
                    raise BusinessError('INTERNAL_RESULT_INVALID', 'Outcome must belong to this job')
            status='waiting' if decision.get('wait') else 'succeeded'
            if self.app.strict_runtime and job['kind'] in {'service','step','review'}:
                if outcome['outcome'] in {'waiting','needs_input'}:
                    status='waiting'
            if status=='succeeded' and job['kind']=='daily':
                plans=self.app.store.list('plan',claim['family_id'])
                if not any(p['business_date']==job['business_date'] for p in plans):
                    raise BusinessError('INCOMPLETE_JOB','daily没有实际提交')
            if status=='succeeded' and job['kind']=='service':
                msg=self.app.store.get('message',job['message_id'],claim['family_id'])
                if not msg['interpreted'] or any(i['requires_reply'] and not i['reply_operation_refs'] for i in msg['intents']):
                    if job.get('recovery_only') and not recovery_state(self.app, claim['family_id'], job):
                        # Complete internal accounting first, then obtain a fresh ordinary reply capability.
                        status='ready'
                    else:
                        status='waiting'
            if status=='succeeded' and job['kind']=='progress':
                report=self.app.store.get('report',job['report_key'],claim['family_id'])
                if report['resolution'] not in {'applied','superseded'}:
                    status='waiting'
            if status=='succeeded' and job['kind']=='review' and not self.app.strict_runtime and not decision.get('no_contact'):
                status='waiting'
            if status=='succeeded' and job['kind']=='step':
                event=self.app.event(claim['family_id'],job['event_id'])
                if not event['update_pending'] and event['status'] not in {'done','cancelled'}:
                    status='waiting'
            self.queue.finish(claim,status,decision.get('reason'),decision.get('retry_at'))
            return {'ran':True,'kind':job['kind'],'outcome':status}
        except BusinessError as exc:
            try:
                saved=self.queue.fail(claim,exc.code)
                return {'ran':True,'outcome':saved['status'],'error':exc.code}
            except BusinessError as cleanup:
                return {'ran':True,'outcome':'rejected','error':exc.code,'cleanup_error':cleanup.code}
        except Exception as exc:
            result={'ran':True,'outcome':'failed','error':type(exc).__name__}
            try:
                saved=self.queue.fail(claim,type(exc).__name__)
                result['outcome']=saved['status']
            except BusinessError as cleanup:
                result['cleanup_error']=cleanup.code
            return result
        finally:
            stop.set(); thread.join(timeout=1)
            if token:
                self.app.revoke_session(token)
