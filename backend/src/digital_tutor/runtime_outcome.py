"""Internal service results, validated against durable records without waking business."""
from typing import Literal
from pydantic import Field
from .domain import Strict, ID, Positive, require
from .storage import digest

class OutcomeRef(Strict):
    kind: Literal['message','event','report','operation','control','pending','memory','evidence']
    id: ID
    version: Positive

class ServiceOutcome(Strict):
    request_id: ID
    outcome: Literal['action_taken','no_contact','waiting','needs_input','draft_only']
    refs: list[OutcomeRef] = Field(min_length=1)
    reason: str = Field(min_length=1)
    remaining_work: list[str] = Field(default_factory=list)
    resume_condition: str | None = None
    draft: str | None = None

def record_outcome(app, token, args):
    cmd = ServiceOutcome.model_validate(args)
    with app.store.transaction() as db:
        family, binding = app.auth(token, {'service'})
        run_id = binding.get('run_id')
        require(run_id and binding.get('lease'), 'RUNTIME_BINDING_REQUIRED', '内部结果须绑定真实Worker运行')
        old = db.execute("SELECT 1 FROM records WHERE kind='run_outcome' AND id=?", (run_id,)).fetchone()
        payload = cmd.model_dump()
        if old:
            saved = app.store.get('run_outcome', run_id, family)
            require(saved['payload_digest']==digest(payload), 'IDEMPOTENCY_CONFLICT', '同运行内部结果不可改写')
            return saved
        records = [(r.kind, app.store.get(r.kind, r.id, family)) for r in cmd.refs]
        for ref, (_, rec) in zip(cmd.refs, records):
            require(ref.version==rec['version'], 'EVIDENCE_SUPERSEDED', '内部结果引用必须是当前真实记录')
        scope = binding.get('reply_scope', {})
        recovery = binding.get('recovery_only',False)
        allowed_events = binding.get('event_ids', [])
        job = app.store.get('job', binding['job_id'], family)
        if job.get('event_id'):
            allowed_events = [job['event_id']]
        require(any((k=='message' and (r['id'] in scope or (recovery and r['id']==job.get('message_id')))) or
                    (k=='event' and r['id'] in allowed_events) or
                    (k=='operation' and r.get('run_id')==run_id)
                    for k,r in records), 'OUTCOME_SCOPE', '结果必须关联本次任务的真实消息、事项或操作')
        outstanding=[]
        message_scope=set(scope)
        if recovery and job.get('message_id'):message_scope.add(job['message_id'])
        for mid in message_scope:
            message=app.store.get('message',mid,family)
            if message['interpreted']:
                outstanding += [i for i in message['intents'] if i['requires_reply'] and not i['reply_operation_refs']
                                and (not recovery or i.get('partial_reply_operation_refs'))]
        if cmd.outcome=='action_taken':
            require(not outstanding,'OUTCOME_INCOMPLETE','仍有未满足来信意图，须保存waiting或needs_input及恢复条件')
            require(any((k=='operation' and (r.get('run_id')==run_id or (recovery and r.get('send_capability',{}).get('job_id')==binding['job_id'])) and r['stage'] in {'sent','delivered'}) or
                        (k in {'control','pending'} and r.get('source_message_id') in scope) or
                        (k=='report' and r.get('origin_run_id')==run_id)
                        for k,r in records), 'OUTCOME_EVIDENCE_MISSING', '已行动必须引用本次实际交付或持久变更')
        if cmd.outcome in {'waiting','needs_input'}:
            require(cmd.remaining_work and cmd.resume_condition, 'OUTCOME_INCOMPLETE', '等待须保留欠项和恢复条件')
        if cmd.outcome=='draft_only':
            require(binding.get('output_mode')=='teacher_draft' and cmd.draft, 'OUTCOME_SCOPE', '草稿仅用于可信内部草稿任务')
        if cmd.outcome in {'no_contact','draft_only'}:
            require(not any(op.get('run_id')==run_id and op['dispatch_count'] for op in app.store.list('operation',family)),
                    'OUTCOME_CONFLICT', '本运行已经发起外发，不能声明未联系或仅草稿')
        from .jobs import job_operation_observation
        observed=job_operation_observation(app,family,binding['job_id'])
        return app.store.put('run_outcome', run_id, family, {**payload, 'run_id':run_id,
            'observed_job_operations':observed,
            'awaiting_operation_ids':[rec['id'] for kind,rec in records if kind=='operation' and rec.get('stage') in {'unknown','accepted','dispatching'}],
            'job_id':binding['job_id'], 'payload_digest':digest(payload), 'recorded_at':app.clock.iso()})

SPECS = {'service.record_outcome': ({'service'}, ServiceOutcome,
          '记录内部结果及真实记录引用；不发送、不结案、不代替报告，等待须附欠项与恢复条件')}
HANDLERS = {'service.record_outcome': record_outcome}
