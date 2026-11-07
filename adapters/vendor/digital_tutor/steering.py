"""Durable inbound coalescing and same-run steering; no model-granted authority."""
import hashlib
import json
from .domain import require
from .storage import encode


def enabled(app):
    return bool(getattr(app, 'steering_enabled', False) and app.strict_runtime
                and app.context_mode == 'daily_native')


def watermark(app, family):
    return app.store.connection.execute(
        "SELECT COALESCE(MAX(seq),0) FROM message_index WHERE family_id=? AND direction='inbound'",
        (family,)).fetchone()[0]


def closed(app, family, run_id):
    return bool(run_id and app.store.connection.execute(
        "SELECT 1 FROM records WHERE kind='run_outcome' AND family_id=? AND id=?",
        (family,run_id)).fetchone())


def enqueue(app, family, message_id):
    """The inbound transaction owns this operation. Unresolved messages stay records."""
    for job in app.store.list('job', family):
        if (job['kind']=='service' and job['status'] in {'ready','leased'}
                and job.get('steerable') and not job.get('recovery_only')
                and not job.get('yield_requested') and (not closed(app,family,job.get('run_id'))
                    or (job['status']=='ready' and job.get('reason')=='new_inbound_continuation'))):
            job['coalesced_message_ids']=list(dict.fromkeys(job.get('coalesced_message_ids',[job['message_id']])+[message_id]))
            app.store.put('job',job['id'],family,job,job['version'])
            return
    app.store.put('job','message:'+message_id,family,{
        'kind':'service','message_id':message_id,'status':'ready','steerable':True,
        'coalesced_message_ids':[message_id]})


def require_current(app, family, binding):
    """Checked in the same transaction as mutations/dispatch admission."""
    if binding.get('steerable') and not binding.get('recovery_only'):
        require(binding.get('steering_seen_seq',-1)>=watermark(app,family),
                'STEERING_INPUT_PENDING','有新的家长消息尚未进入推理；暂停旧写入，下一轮先接收补充再处理。')


def pending_for_job(app,family,job):
    mids=set(job.get('coalesced_message_ids',[]))
    return any(row['message_id'] in mids for row in app.store.connection.execute(
        "SELECT message_id FROM message_index WHERE family_id=? AND direction='inbound' AND seq>?",
        (family,job.get('steering_seen_seq',0))))


def refresh_binding(app, token):
    """Host only. Refresh scope without issuing a new lease or reviving a closed run."""
    from .runtime_config import reply_scope
    with app.store.transaction() as db:
        family,binding=app.auth(token,{'service'})
        if not binding.get('steerable') or binding.get('recovery_only'):return None
        latest=watermark(app,family);previous=binding.get('steering_seen_seq',0)
        if latest<=previous or closed(app,family,binding['run_id']):return None
        mids=[r[0] for r in db.execute(
            "SELECT message_id FROM message_index WHERE family_id=? AND direction='inbound' AND seq>? AND seq<=? ORDER BY seq",
            (family,previous,latest))]
        from .runtime_config import task_reply_scope
        job=app.store.get('job',binding['job_id'],family)
        binding.update(reply_scope=task_reply_scope(app,family,job),steering_seen_seq=latest,
                       family_version=app.store.family(family)['version'])
        db.execute('UPDATE sessions SET binding=? WHERE token_hash=?',
                   (encode(binding),hashlib.sha256(token.encode()).hexdigest()))
        job=app.store.get('job',binding['job_id'],family)
        job['steering_seen_seq']=latest
        app.store.put('job',job['id'],family,job,job['version'])
        return {'family':family,'binding':binding,'from_seq':previous,'through_seq':latest,'message_ids':mids}


class NativeSteering:
    """Use Hermes' supported step callback and steer(), never replace its loop."""
    def __init__(self,app,token,guard,work,connection,check_memory,method_context):
        self.app,self.token,self.guard,self.work=app,token,guard,work
        self.connection,self.check_memory,self.method_context=connection,check_memory,method_context
        self.agent=None;self.error=None

    def step(self,*unused):
        # Hermes logs callback exceptions; retain an error for the mandatory wire gate.
        try:self._step()
        except Exception as exc:
            self.error=getattr(exc,'code',type(exc).__name__)

    def _step(self):
        self.check_memory()
        update=refresh_binding(self.app,self.token)
        if not update:return
        from .context import read_context
        from .daily_context import project
        from .context_budget import RequestGuard,segment,estimated_tokens
        from .efficient_context import project_context
        from .context_transport import render
        app=self.app;family=update['family'];binding=update['binding']
        context=read_context(app,self.token)
        from .runtime_assembly import attach_method_context
        context=json.loads(attach_method_context(json.dumps(context,ensure_ascii=False),self.method_context))
        folder=self.work/('steer-'+str(update['through_seq']));folder.mkdir(exist_ok=True)
        if hasattr(app,'responsibility_review'):
            from .responsibility_review import review_context
            review_input={**context,'messages':[
                {**m,'interpreted':True} if m['id'] in app.responsibility_review else m
                for m in context.get('messages',[])]}
            reviewed=review_context(review_input,self.connection,folder)
            app.responsibility_review.update({r['message_id']:r for r in reviewed})
            context['response_responsibilities']={'items':list(app.responsibility_review.values()),
                'contract':'每个实质待答子点保留独立责任；补充和纠正优先于旧草稿，不自动结清旧问题。'}
        context=project(app,family,binding,project_context(context))
        context=segment(context,estimated_tokens({'system':self.agent._cached_system_prompt or '',
                                                  'tools':self.agent.tools})+2000)
        # Update the existing guard object (HTTP observer and compressor reference it).
        newer=RequestGuard(context,check_current=self.guard.check_current)
        self.guard.context,self.guard.protected,self.guard.source_hash=newer.context,newer.protected,newer.source_hash
        app.daily_sources=context['sources']
        (folder/'context.json').write_text(render(context,'daily_native'),encoding='utf-8')
        note=('家长在当前服务任务中补充了消息。当前日聊天sources及授权范围已更新；'
              '请读取并解释新增消息，结合补充/纠正调整后续步骤，保留仍有效的查证结果。'
              '尚未发送的旧草稿须重新核对；明确停发则不再发送。新增消息ID：'+json.dumps(update['message_ids']))
        require(self.agent.steer(note),'STEERING_REJECTED','原生引导未接收；保留未处理消息')
        with app.store.transaction():
            app.store.put('runtime_steering',binding['run_id']+':'+str(update['through_seq']),family,
                {k:v for k,v in update.items() if k!='binding'} | {'run_id':binding['run_id'],
                 'job_id':binding['job_id'],'source_hash':self.guard.source_hash,'stage':'queued_to_native'})
        with (self.work/'steering.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps({'run_id':binding['run_id'],'from_seq':update['from_seq'],
                'through_seq':update['through_seq'],'message_ids':update['message_ids'],
                'source_hash':self.guard.source_hash},ensure_ascii=False)+'\n')

    def check(self):
        require(not self.error,'STEERING_REFRESH_FAILED','消息引导准备失败，停止模型请求并保留原消息。')
        self.check_memory()
