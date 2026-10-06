"""Clock-driven host scheduling, independent of model reasoning or text generation."""
from datetime import timedelta
from threading import Event, Thread
from .domain import BusinessError, timestamp
from .jobs import JobQueue, enabled, recovery_state
from .storage import digest
from .tools import ToolRegistry
from .send_policy import action_paused
from .runtime_config import allowed_kind
from .scheduling import baseline_committed, step_key, step_ready, event_order


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
                if allowed_kind(app, 'daily'):
                    for pending in app.store.list('pending', family):
                        if pending.get('planning_requested') and pending.get('await_decision'):
                            revision = pending.get('decision_revision', 1)
                            created += self.queue.enqueue(family, 'admission:' + digest([family,pending['id'],revision]),
                                {'kind':'daily','planning_reason':'admission','pending_id':pending['id'],
                                 'decision_revision':revision})
                if allowed_kind(app, 'daily') and app.service_start<=now.hour<app.service_end and not baseline_committed(app,family,today):
                    created+=self.queue.enqueue(family,'daily:'+digest([family,today]),{'kind':'daily','business_date':today})
                if not allowed_kind(app, 'step'):
                    continue
                for event in sorted(app.store.list('event',family), key=lambda e:event_order(app,family,e)):
                    current=app.event(family,event['id'])
                    from .commitment_review import enqueue_expired_review
                    created+=enqueue_expired_review(app,family,current)
                    if current['update_pending'] or event['status'] in {'done','cancelled'}:
                        continue
                    waiting=event.get('waiting')
                    if waiting and waiting.get('review_at') and timestamp(waiting['review_at'])<=now:
                        created+=self.queue.enqueue(family,'review:'+digest([event['id'],waiting]),
                                  {'kind':'review','event_id':event['id'],'event_version':event['version'],'contact_allowed':False})
                    if event['status'] in {'pending','in_progress'} and event.get('next_action'):
                        if app.strict_runtime and not step_ready(app,family,current):
                            continue
                        if event['next_action'].get('execute_at') and timestamp(event['next_action']['execute_at'])>now:
                            continue
                        if action_paused(app,family,event['next_action']['action_kind'],event['id']):
                            continue
                        key=step_key(event)
                        # A committed step is not a daily repetition rule. Legacy
                        # un-timed steps retain their existing date-key behavior.
                        dated = not (app.strict_runtime and event['next_action'].get('execute_at'))
                        created+=self.queue.enqueue(family,'step:'+digest([key,today] if dated else [key]),{'kind':'step','event_id':event['id'],
                                  'step_key':key,'event_version':event['version'],
                                  **({'business_date':today} if dated else {})})
        return {'created':created,'enabled':True}


class Worker:
    """The handler can be deterministic or a native Hermes adapter; the loop is shared."""
    def __init__(self, app, handler, name='worker', lease_seconds=60):
        self.app=app; self.queue=JobQueue(app); self.handler=handler; self.name=name
        self.lease_seconds=lease_seconds

    def run_once(self):
        claim=self.queue.claim(self.name,self.lease_seconds)
        if not claim and enabled(self.app) and hasattr(self.handler,'observe_sources'):
            self.handler.observe_sources(self.app)
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
            if job['kind'] in {'step','review'} and not job.get('recovery_only'):
                event=self.app.event(claim['family_id'],job['event_id'])
                from .learning_evidence import correction_review_allowed
                correction_review=correction_review_allowed(self.app,claim['family_id'],job,event)
                from .learning_review import report_review_allowed
                report_review=report_review_allowed(self.app,claim['family_id'],job)
                if correction_review and event['update_pending']:
                    self.queue.finish(claim,'waiting','REPORTS_PENDING')
                    return {'ran':True,'outcome':'waiting','reason':'REPORTS_PENDING'}
                if (event['update_pending'] and not report_review) or (event['status'] in {'done','cancelled'} and not correction_review and not report_review) or (job['kind']=='step' and (event['status']=='waiting' or step_key(event)!=job['step_key'])):
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
                if job.get('planning_reason')=='admission':
                    pending=self.app.store.get('pending',job['pending_id'],claim['family_id'])
                    if pending.get('await_decision') or pending.get('decision_revision',1)!=job.get('decision_revision',1):
                        raise BusinessError('INCOMPLETE_JOB','本次承接尚无实际决定')
                elif not baseline_committed(self.app,claim['family_id'],job['business_date']):
                    raise BusinessError('INCOMPLETE_JOB','daily没有实际基线提交')
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
            if status=='succeeded' and job.get('report_review_key'):
                from .learning_review import revised_report
                if revised_report(self.app,claim['family_id'],job) is None:
                    status='waiting'
            if status=='succeeded' and job.get('commitment_review_id'):
                from .commitment_review import has_expiration_report
                if not has_expiration_report(self.app,claim['family_id'],job):
                    status='waiting'
            if status=='succeeded' and job['kind']=='step':
                event=self.app.event(claim['family_id'],job['event_id'])
                if event['status'] not in {'done','cancelled'} and (self.app.strict_runtime or not event['update_pending']):
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
