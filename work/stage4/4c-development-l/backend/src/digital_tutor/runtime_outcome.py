"""Internal service results, validated against durable records without waking business."""
from typing import Literal
from pydantic import Field
from .domain import Strict, ID, Positive, require
from .storage import digest
from .service_context import control_scope_snapshot

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
    resume_condition: str | None = Field(default=None, description=(
        '仍欠输入或证据何时可再次核实的说明；不是暂停范围或解除权限。'
        '控制范围由宿主从真实control记录派生为effective_control_scopes；'
        'event_id为空覆盖全家庭相应动作，不因事项主题不同而排除。'))
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
        from .request_responsibility import ready_request_settlements, apply_request_settlements
        settlements = ready_request_settlements(app,family,job) if recovery else []
        settling_pairs = {(s['message_id'],s['intent_id']) for s in settlements}
        if job.get('event_id'):
            allowed_events = job.get('report_review_event_ids') or [job['event_id']]
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
                                and (mid,i['intent_id']) not in settling_pairs
                                and (recovery or '*' in scope.get(mid,[]) or i['intent_id'] in scope.get(mid,[]))
                                and (not recovery or i.get('partial_reply_operation_refs'))]
        if cmd.outcome=='action_taken':
            if job.get('commitment_review_id'):
                from .commitment_review import has_expiration_report
                require(has_expiration_report(app,family,job),'COMMITMENT_REPORT_REQUIRED',
                        'Expiry review requires its actual original-obligation report')
            if job.get('report_review_key'):
                from .learning_review import revised_report
                revised=revised_report(app,family,job)
                require(revised and any(k=='report' and r['id']==revised['id']
                        and r.get('origin_run_id')==run_id for k,r in records),
                        'REPORT_REVISION_REQUIRED','Review action requires the actual next report revision')
            require(not outstanding,'OUTCOME_INCOMPLETE','仍有未满足来信意图，须保存waiting或needs_input及恢复条件')
            require(any((k=='operation' and (r.get('run_id')==run_id or (recovery and r.get('send_capability',{}).get('job_id')==binding['job_id'])) and r['stage'] in {'sent','delivered'}) or
                        (k in {'control','pending'} and r.get('source_message_id') in scope) or
                        (k=='report' and r.get('origin_run_id')==run_id)
                         for k,r in records) or settlements, 'OUTCOME_EVIDENCE_MISSING', '已行动必须引用本次实际交付或持久变更')
            if job['kind']=='step':
                effect_runs={run_id}
                if recovery:
                    effect_runs.update(op.get('run_id') for op in app.store.list('operation',family)
                                       if op.get('send_capability',{}).get('job_id')==job['id'])
                require(any(k=='report' and job['event_id'] in r.get('event_ids',[])
                            and r.get('origin_run_id') in effect_runs for k,r in records),
                        'STEP_REPORT_REQUIRED','步骤行动须提交绑定原事项的真实报告，不以发送成功替代报告')
        if cmd.outcome in {'waiting','needs_input'}:
            require(cmd.remaining_work and cmd.resume_condition, 'OUTCOME_INCOMPLETE', '等待须保留欠项和恢复条件')
        if cmd.outcome=='draft_only':
            require(binding.get('output_mode')=='teacher_draft' and cmd.draft, 'OUTCOME_SCOPE', '草稿仅用于可信内部草稿任务')
        if cmd.outcome in {'no_contact','draft_only'}:
            require(not any(op.get('run_id')==run_id and op['dispatch_count'] for op in app.store.list('operation',family)),
                    'OUTCOME_CONFLICT', '本运行已经发起外发，不能声明未联系或仅草稿')
        if job['kind']=='step' and cmd.outcome=='no_contact' and not any(
                k=='report' and job['event_id'] in r.get('event_ids',[]) and r.get('origin_run_id')==run_id
                for k,r in records):
            require(cmd.remaining_work and cmd.resume_condition,'OUTCOME_INCOMPLETE','无变化步骤须保留真实等待条件，不能静默完成')
        if job.get('report_review_key') and cmd.outcome=='no_contact':
            from .learning_review import revised_report
            require(revised_report(app,family,job) or (cmd.remaining_work and cmd.resume_condition),
                    'OUTCOME_INCOMPLETE','Unrevised source review must retain its missing work and resume condition')
        if job.get('commitment_review_id') and cmd.outcome=='no_contact':
            from .commitment_review import has_expiration_report
            require(has_expiration_report(app,family,job) or (cmd.remaining_work and cmd.resume_condition),
                    'OUTCOME_INCOMPLETE','Expiry review without a report must retain its remaining work and resume condition')
        from .jobs import job_operation_observation
        observed=job_operation_observation(app,family,binding['job_id'])
        from .scheduling import planning_input_observation
        from .request_responsibility import acknowledgements
        scope_facts = ({'effective_control_scopes': control_scope_snapshot(app, family),
                        'resume_condition_authority': 'model_input_recovery_note_not_control_or_send_permission'}
                       if app.strict_runtime else {})
        settled_refs=apply_request_settlements(app,family,settlements)
        return app.store.put('run_outcome', run_id, family, {**payload, 'run_id':run_id,
            **scope_facts,
            'observed_job_operations':observed,
            'observed_planning_inputs':planning_input_observation(app,family,job),
            'delegation_acknowledgements':acknowledgements(app,family,run_id),
            'settled_delegated_request_refs':settled_refs,
            'awaiting_operation_ids':[rec['id'] for kind,rec in records if kind=='operation' and rec.get('stage') in {'unknown','accepted','dispatching'}],
            'job_id':binding['job_id'], 'payload_digest':digest(payload), 'recorded_at':app.clock.iso()})

SPECS = {'service.record_outcome': ({'service'}, ServiceOutcome,
          '记录内部结果及真实记录引用；不发送、不结案、不代替报告，等待须附欠项与恢复条件。暂停实际范围由宿主control记录派生，恢复说明不改变控制或发送资格')}
HANDLERS = {'service.record_outcome': record_outcome}
