"""Internal service results, validated against durable records without waking business."""
from typing import Literal
from pydantic import Field
from .domain import Strict, ID, Positive, require
from .storage import digest
from .service_context import control_scope_snapshot

class OutcomeRef(Strict):
    kind: Literal['message','event','report','operation','control','pending','memory','evidence','human_request','delivery_accounting']
    id: ID
    version: Positive

class ServiceOutcome(Strict):
    request_id: ID
    outcome: Literal['action_taken','no_contact','waiting','needs_input','draft_only'] = Field(description='action_taken是outcome的取值；本工具没有单独的action_taken、action_kind或evidence_refs参数。')
    refs: list[OutcomeRef] = Field(default_factory=list, description='可省略，由宿主在本次事务中组装当前任务及本轮真实操作的引用。显式提供时仍逐个检查版本、范围和实际存在性。')
    reason: str = Field(min_length=1, description='只按最后成功工具回执陈述本轮实际效果。发送返回ok=false表示没有送达，不能写已答复或已确认。此工具是本轮最后一次业务写入；成功后不能补发或补登记。')
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
        from .steering import require_current
        require_current(app,family,binding)
        run_id = binding.get('run_id')
        require(run_id and binding.get('lease'), 'RUNTIME_BINDING_REQUIRED', '内部结果须绑定真实Worker运行')
        old = db.execute("SELECT 1 FROM records WHERE kind='run_outcome' AND id=?", (run_id,)).fetchone()
        payload = cmd.model_dump()
        if old:
            saved = app.store.get('run_outcome', run_id, family)
            require(saved['payload_digest']==digest(payload), 'IDEMPOTENCY_CONFLICT', '同运行内部结果不可改写')
            return saved
        from .human_help import relevant as human_requests
        require(not any(r['status']=='pending' for r in human_requests(app,family,binding))
                or cmd.outcome in {'waiting','needs_input'},'HUMAN_HELP_PENDING',
                '仍有本任务人工处理缺口；登记不代表解决，请保留waiting/needs_input、欠项及恢复条件')
        from .responsibility_review import guard_outcome
        guard_outcome(app,family)
        if not cmd.refs:
            cmd.refs = [OutcomeRef.model_validate(ref) for ref in outcome_references(app, family, binding)]
        require(cmd.refs, 'OUTCOME_SCOPE', '本次任务没有可引用的真实记录')
        records = [(r.kind, app.store.get(r.kind, r.id, family)) for r in cmd.refs]
        for ref, (_, rec) in zip(cmd.refs, records):
            require(ref.version==rec['version'], 'EVIDENCE_SUPERSEDED', '内部结果引用必须是当前真实记录')
        scope = binding.get('reply_scope', {})
        if binding.get('steerable') and not binding.get('recovery_only'):
            require(all(app.store.get('message',mid,family).get('interpreted') for mid in scope),
                    'INTAKE_INTERPRETATION_PENDING','合并回合的全部来信须完成解释后再记录结果。')
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
        outstanding_refs=[]
        message_scope=set(scope)
        if recovery and job.get('message_id'):message_scope.add(job['message_id'])
        for mid in message_scope:
            message=app.store.get('message',mid,family)
            if message['interpreted']:
                pending=[i for i in message['intents'] if i['requires_reply'] and not i['reply_operation_refs']
                                and (mid,i['intent_id']) not in settling_pairs
                                and (recovery or '*' in scope.get(mid,[]) or i['intent_id'] in scope.get(mid,[]))
                                and (not recovery or i.get('partial_reply_operation_refs'))]
                outstanding += pending
                outstanding_refs += [{'message_id':mid,'intent_id':i['intent_id'],
                    'message_version':message['version'],'text':i['text']} for i in pending]
        managed_check=any(k=='event' and r.get('service_action_id') and r.get('action_check',{}).get('origin_run_id')==run_id for k,r in records)
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
                        (k in {'report','delivery_accounting'} and r.get('origin_run_id')==run_id)
                         for k,r in records) or settlements or managed_check, 'OUTCOME_EVIDENCE_MISSING', '已行动必须引用本次实际交付或持久变更')
            if job['kind']=='step':
                effect_runs={run_id}
                if recovery:
                    effect_runs.update(op.get('run_id') for op in app.store.list('operation',family)
                                       if op.get('send_capability',{}).get('job_id')==job['id'])
                require(any(k=='report' and job['event_id'] in r.get('event_ids',[])
                            and r.get('origin_run_id') in effect_runs for k,r in records) or managed_check,
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
        from .acceptance_handoff import require_handoff
        require_handoff(app, family, binding)
        from .jobs import job_operation_observation
        observed=job_operation_observation(app,family,binding['job_id'])
        from .scheduling import planning_input_observation
        from .request_responsibility import acknowledgements
        scope_facts = ({'effective_control_scopes': control_scope_snapshot(app, family),
                        'resume_condition_authority': 'model_input_recovery_note_not_control_or_send_permission'}
                       if app.strict_runtime else {})
        settled_refs=apply_request_settlements(app,family,settlements)
        from .service_wait import observation, read_coverage
        return app.store.put('run_outcome', run_id, family, {**payload,
            'refs':[ref.model_dump() for ref in cmd.refs], 'refs_origin':'model' if payload['refs'] else 'host_current_records',
            'observed_service_inputs':observation(app,family),
            'observed_reply_inputs':read_coverage(app,family,binding), 'run_id':run_id,
            'outstanding_intent_refs':outstanding_refs,
            'actual_run_operation_refs':[{'kind':'operation','id':r['id'],'version':r['version'],'stage':r['stage']}
                for r in app.store.list('operation',family) if r.get('run_id')==run_id],
            **scope_facts,
            'observed_job_operations':observed,
            'observed_planning_inputs':planning_input_observation(app,family,job),
            'delegation_acknowledgements':acknowledgements(app,family,run_id),
            'settled_delegated_request_refs':settled_refs,
            'awaiting_operation_ids':[rec['id'] for kind,rec in records if kind=='operation' and rec.get('stage') in {'unknown','accepted','dispatching'}],
            'job_id':binding['job_id'], 'payload_digest':digest(payload), 'recorded_at':app.clock.iso()})


def outcome_references(app, family, binding):
    """Derive evidence, never infer completion or invent an operation."""
    keys={('message',mid) for mid in binding.get('reply_scope',{})}
    job=app.store.get('job',binding['job_id'],family)
    if job.get('message_id'):keys.add(('message',job['message_id']))
    if job.get('event_id'):keys.add(('event',job['event_id']))
    for kind in ('operation','report','delivery_accounting'):
        keys.update((kind,row['id']) for row in app.store.list(kind,family)
                    if row.get('run_id',row.get('origin_run_id'))==binding.get('run_id'))
    for kind in ('control','pending'):
        keys.update((kind,row['id']) for row in app.store.list(kind,family)
                    if row.get('source_message_id') in binding.get('reply_scope',{}))
    from .human_help import relevant
    keys.update(('human_request',row['id']) for row in relevant(app,family,binding))
    return [{'kind':kind,'id':rid,'version':app.store.get(kind,rid,family)['version']}
            for kind,rid in sorted(keys)]

SPECS = {'service.record_outcome': ({'service'}, ServiceOutcome,
          '本轮最后调用：先完成所需发送/报告并核对成功回执，再记录内部结果；成功即结束业务写入，不能随后补发。发送被拒应先按错误纠正或如实保存未送达欠项，不能声明已确认。等待须附欠项与恢复条件；不发送、不结案、不代替报告，恢复说明不改变权限')}
HANDLERS = {'service.record_outcome': record_outcome}
