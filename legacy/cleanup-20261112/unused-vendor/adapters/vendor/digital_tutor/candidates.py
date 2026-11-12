"""Narrow host-confirmed simulation sources; model text cannot authorize new goals."""
import json
from datetime import timezone
from typing import Literal
from pydantic import Field
from .domain import Strict, ID, require, timestamp, BusinessError
from .storage import digest


class TextPart(Strict):
    part_id: ID
    media_type: Literal['text'] = 'text'
    purpose: str = Field(min_length=1)


class CandidateSource(Strict):
    source_id: ID
    source_kind: Literal['makeup_rule','periodic_text_delivery','service_learning_goal']
    simulation: Literal[True]
    confirmed: Literal[True]
    enabled: bool = Field(strict=True)
    rule_version: ID
    active_from: str
    active_until: str
    due_at: str
    due_semantics: Literal['appointment','deadline'] = 'appointment'
    service_action_id: ID | None = None
    lesson_days: list[int] = Field(default_factory=list)
    service_cycle_end_at: str | None = None
    trigger_counting_policy: str | None = None
    # Makeup rule uses an explicit bound batch/day, not a model-proposed lesson.
    batch_id: ID | None = None
    day: int | None = Field(default=None,strict=True,ge=1,le=240)
    required_coverage_at: str | None = None
    # Periodic contract is a confirmed scope, not an LLM-created obligation.
    contract_id: ID | None = None
    period_start: str | None = None
    period_end: str | None = None
    parts: list[TextPart] = Field(default_factory=list)
    required_stage: Literal['sent','delivered'] = 'sent'
    completion_condition: str | None = None
    instructions: str | None = None


def configure_source(app, token, args):
    """Host administration only; deliberately absent from the tool registry."""
    source=CandidateSource.model_validate(args)
    for field in ('active_from','active_until','due_at'):
        timestamp(getattr(source,field))
    require(timestamp(source.active_from)<=timestamp(source.active_until),'INVALID_SOURCE','Invalid active interval')
    if source.service_action_id:
        from .action_completion import RULES
        require(source.service_action_id in RULES,'INVALID_SOURCE','未知主动动作')
    if source.source_kind=='service_learning_goal':
        from .action_completion import RULES
        require(source.service_action_id and RULES[source.service_action_id]['rule']=='L','INVALID_SOURCE','学情目标必须使用L类动作')
        require(source.batch_id and source.required_coverage_at and source.contract_id and source.lesson_days and not source.parts,'INVALID_SOURCE','须绑定原批次和明确课节范围')
        require(all(type(d) is int and 1<=d<=240 for d in source.lesson_days) and len(set(source.lesson_days))==len(source.lesson_days),'INVALID_SOURCE','课节须唯一且有效')
        require(bool(source.completion_condition),'INVALID_SOURCE','目标须有明确完成条件')
        timestamp(source.required_coverage_at)
    if source.service_action_id in {'I01','I02','I03','I04','I05'}:
        from .action_completion import RULES
        rule=RULES[source.service_action_id]
        require(source.source_kind=='periodic_text_delivery','INVALID_SOURCE','互动须使用已确认的服务合同')
        require({p.part_id for p in source.parts}==set(rule['required_slots']),'INVALID_SOURCE','互动交付槽位须完整保留')
        require(bool(source.completion_condition),'INVALID_SOURCE','须保留动作完成条件')
        if rule['cycle_required']:
            require(bool(source.service_cycle_end_at),'INVALID_SOURCE','轮换互动须有本轮结束时间')
            require(timestamp(source.service_cycle_end_at)>timestamp(source.due_at),'INVALID_SOURCE','互动结束须晚于邀请时间')
            require(timestamp(source.active_until)<=timestamp(source.service_cycle_end_at),'INVALID_SOURCE','邀请窗口不能超出本轮结束时间')
    if source.source_kind=='makeup_rule':
        require(source.batch_id and source.day and source.required_coverage_at and not source.parts,
                'INVALID_SOURCE','Makeup requires batch/day/coverage')
        timestamp(source.required_coverage_at)
    elif source.source_kind!='service_learning_goal':
        require(source.contract_id and source.period_start and source.period_end and source.parts,
                'INVALID_SOURCE','Periodic delivery needs confirmed period and parts')
        require(timestamp(source.period_start)<timestamp(source.period_end),'INVALID_SOURCE','Invalid period')
        require(len({p.part_id for p in source.parts})==len(source.parts),'INVALID_SOURCE','Duplicate part')
    with app.store.transaction():
        family,_=app.auth(token,{'admin'})
        key='candidate-source:'+digest([family,source.source_id])
        old=next((s for s in app.store.list('candidate_source',family) if s['id']==key),None)
        value=source.model_dump()
        if old and {k:old.get(k) for k in value}==value:return old
        saved=app.store.put('candidate_source',key,family,value,old['version'] if old else None)
        app.store.bump(family)
        return saved


def makeup_identity(family,course_id,course_version,lessons):
    return {'kind':'makeup','family_id':family,'course_id':course_id,
            'course_version':course_version,'lesson_refs':sorted(lessons)}


def source_facts(app,family,source):
    """Fingerprint original inputs, including gaps; never depend on audit versions."""
    bindings=app.store.list('course_binding',family)
    if source['source_kind'] in {'family_makeup_request','family_service_request'}:
        message=next((m for m in app.store.list('message',family)
                      if m['id']==source.get('source_message_id')),None)
        from .family_candidates import message_content
        return [source,message_content(message) if message else None,bindings]
    if source['source_kind']=='service_learning_goal':
        uid=app.store.family(family)['uid']
        rows=[dict(r) for r in app.store.connection.execute('SELECT * FROM learning_days WHERE batch_id=? AND uid=? ORDER BY day',(source.get('batch_id'),uid)) if r['day'] in source.get('lesson_days',[])]
        imported=app.store.connection.execute('SELECT metadata FROM imports WHERE batch_id=?',(source.get('batch_id'),)).fetchone()
        return [source,rows,imported[0] if imported else None,[b for b in bindings if b['batch_id']==source.get('batch_id')]]
    if source['source_kind']=='makeup_rule':
        uid=app.store.family(family)['uid']
        row=app.store.connection.execute('SELECT * FROM learning_days WHERE batch_id=? AND uid=? AND day=?',
            (source.get('batch_id'),uid,source.get('day'))).fetchone()
        imported=app.store.connection.execute('SELECT metadata FROM imports WHERE batch_id=?',
                                              (source.get('batch_id'),)).fetchone()
        return [source,dict(row) if row else None,imported[0] if imported else None,
                [b for b in bindings if b['batch_id']==source.get('batch_id')]]
    return [source]


def derive(app,family,source):
    fingerprint=digest(source_facts(app,family,source))
    try:
        value=_derive(app,family,source)
    except (BusinessError,ValueError,TypeError,KeyError):
        value={'source_kind':source.get('source_kind'),'source_refs':[],
               'status':'blocked','gaps':['source_invalid_or_incomplete'],'goal_key':None,
               'completion':None,'due_at':source.get('due_at'),'delivery_parts':[]}
    return {**value,'completion_condition':source.get('completion_condition'), 'source_fingerprint':fingerprint}


def _derive(app,family,source):
    """Read original rows each time; missing coverage never means an unmet obligation."""
    if source['source_kind']=='family_service_request':
        from .service_requests import derive_service_request
        return derive_service_request(app,family,source)
    if source['source_kind']=='family_makeup_request':
        from .family_candidates import derive_family_request
        return derive_family_request(app,family,source)
    source_ref={'kind':'candidate_source','id':source['id'],'version':source['version']}
    common={'source_kind':source['source_kind'],'source_refs':[source_ref],
            'rule_version':source['rule_version'],'status':'blocked','gaps':[],
            'goal_key':None,'completion':None,'due_at':source['due_at'],'delivery_parts':[],
            'original_deadline':source['due_at'],
            'requested_time':{'precision':'datetime','value':source['due_at'],
                              'source_text':source['due_at'],'meaning':'confirmed_contract_deadline'}}
    if not source['enabled']:
        return {**common,'gaps':['source_disabled']}
    if not timestamp(source['active_from'])<=app.clock.now()<=timestamp(source['active_until']):
        return {**common,'gaps':['source_not_active']}
    if source['source_kind']=='service_learning_goal':
        from .lab_sources import derive_goal
        return derive_goal(app,family,source,common)
    if source['source_kind']=='periodic_text_delivery':
        identity={'kind':'periodic_text_delivery','family_id':family,'contract_id':source['contract_id'],
                  'period_start':timestamp(source['period_start']).astimezone(timezone.utc).isoformat(),
                  'period_end':timestamp(source['period_end']).astimezone(timezone.utc).isoformat()}
        completion={'kind':'delivery','lesson_refs':[],'required_part_ids':[p['part_id'] for p in source['parts']],
                    'required_stage':source['required_stage']}
        return {**common,'status':'ready','goal_key':'delivery:'+digest(identity),'source_identity':identity,
                'goal_text':action_goal(source),
                'completion':completion,'delivery_parts':source['parts'],
                'coverage':{'period_start':source['period_start'],'period_end':source['period_end']},
                'source_fingerprint':digest(source)}
    uid=app.store.family(family)['uid']
    row=app.store.connection.execute('SELECT * FROM learning_days WHERE batch_id=? AND uid=? AND day=?',
                                   (source['batch_id'],uid,source['day'])).fetchone()
    imported=app.store.connection.execute('SELECT metadata FROM imports WHERE batch_id=?',(source['batch_id'],)).fetchone()
    bindings=[b for b in app.store.list('course_binding',family) if b['batch_id']==source['batch_id']]
    if not row or not imported or len(bindings)!=1:
        return {**common,'gaps':['learning_source_or_confirmed_binding_missing']}
    row=dict(row);meta=json.loads(imported[0]);binding=bindings[0]
    require(binding.get('source_kind')=='trusted_host_confirmation','SOURCE_UNCONFIRMED','Course identity is not confirmed')
    coverage=[meta.get('coverage_through'),binding.get('coverage_through')]
    required=timestamp(source['required_coverage_at'])
    if not all(value and timestamp(value)>=required for value in coverage):
        return {**common,'gaps':['coverage_incomplete'],'coverage':coverage}
    lessons=[entry['lesson_ref'] for entry in binding.get('lesson_map') or [] if entry['day']==source['day']]
    if len(lessons)!=1 or not binding.get('course_id') or not binding.get('course_version'):
        return {**common,'gaps':['confirmed_lesson_mapping_missing']}
    if row['completion_status']!='incomplete':
        return {**common,'gaps':['no_confirmed_incomplete_fact'],
                'source_completion_status':row['completion_status']}
    completion={'kind':'goal','lesson_refs':lessons,'required_part_ids':[],'required_stage':'sent'}
    identity=makeup_identity(family,binding['course_id'],binding['course_version'],lessons)
    return {**common,'status':'ready','goal_key':'makeup:'+digest(identity),'source_identity':identity,
            'goal_text':'补读已确认原课节 '+lessons[0],'completion':completion,
            'source_refs':[source_ref,{'kind':'course_binding','id':binding['id'],'version':binding['version']}],
            'coverage':{'source_through':meta['coverage_through'],'binding_through':binding['coverage_through']},
            'source_row':{'batch_id':row['batch_id'],'day':row['day'],'completion_status':row['completion_status'],
                          'evidence_locator':row['evidence_locator']},
            'source_fingerprint':digest([source,row,meta,binding])}


def action_goal(source):
    action=source.get('service_action_id')
    if action in {'S02','S04'}:
        return ('S04 连续学习鼓励' if action=='S04' else 'S02 当课完成鼓励')+'，完成原要求的鼓励与孩子口信。'
    if action:
        from .action_completion import RULES
        return action+' '+RULES[action]['criterion']+('。仅邀请或收到感谢不算互动完成。' if RULES[action]['rule']=='I' else '')
    return '履行已确认文字反馈合同 '+source['contract_id']


def candidate_view(app,family,source):
    value=derive(app,family,source)
    value.update(source_id=source['id'],source_version=source['version'],existing_event_id=None)
    if value['status']!='ready':return value
    identity=value['source_identity'];events=app.store.list('event',family)
    matches=[e for e in events if e.get('source_identity')==identity]
    # A legacy key/lesson alone is not evidence of course identity. Keep the gap.
    collisions=[e for e in events if e.get('source_identity')!=identity and (
        e['goal_key']==value['goal_key'] or (not e.get('source_identity') and
        identity['kind']=='makeup' and e['completion']['kind']=='goal' and
        set(e['completion']['lesson_refs'])==set(identity['lesson_refs'])))]
    if collisions or len(matches)>1:
        return {**value,'status':'blocked','gaps':['original_goal_identity_ambiguous']}
    if not matches:return value
    match=matches[0];value['existing_event_id']=match['id']
    if source['source_kind']=='family_makeup_request':
        value['requested_due_at']=value['due_at'];value['due_at']=match['due_at']
    if match['status'] in {'done','cancelled'}:
        value.update(status='blocked',gaps=['original_goal_already_terminal'])
    elif (match['completion']!=value['completion'] or match['due_at']!=value['due_at']
          or match.get('delivery_parts',[])!=value['delivery_parts']):
        value.update(status='blocked',gaps=['original_contract_conflict_requires_review'])
    return value


def snapshot_sources(app,family):
    # Include blocked sources too: a gap filled by an out-of-band source update
    # must invalidate an old snapshot even when family.version was not bumped.
    return {source['id']:digest(candidate_view(app,family,source))
            for source in app.store.list('candidate_source',family)}


def refresh_candidates(app,family):
    """Caller owns transaction; run before binding the Planner snapshot."""
    changed=False
    sources=app.store.list('candidate_source',family)
    by_id={source['id']:source for source in sources}
    for source in sources:
        key='candidate:'+digest([family,source['id']]);value=candidate_view(app,family,source)
        previous=next((c for c in app.store.list('candidate',family) if c['id']==key),None)
        if previous and {k:v for k,v in previous.items() if k not in {'id','version'}}==value:continue
        app.store.put('candidate',key,family,value,previous['version'] if previous else None);changed=True
    for previous in app.store.list('candidate',family):
        if previous['source_id'] not in by_id and previous.get('gaps')!=['source_removed']:
            app.store.put('candidate',previous['id'],family,{**previous,'status':'blocked','gaps':['source_removed']},previous['version'])
            changed=True
    if changed:app.store.bump(family)
    return app.store.list('candidate',family)


def validate_daily_candidates(app,family,binding,cmd):
    """Strict submission cannot manufacture a source, contract or alternate goal key."""
    if not app.strict_runtime:return
    require(binding.get('candidate_source_states')==snapshot_sources(app,family),
            'STALE_CANDIDATE','Source facts or applicability changed after the bound snapshot')
    candidates={c['id']:c for c in app.store.list('candidate',family) if c['status']=='ready'}
    if binding.get('planning_reason')=='admission':
        candidates={k:c for k,c in candidates.items() if c.get('pending_id')==binding['pending_id']}
    if binding.get('planning_reason')=='source_refresh':
        candidates={k:c for k,c in candidates.items() if k in binding.get('candidate_versions',{})}
    bound=binding.get('candidate_versions',{})
    require(bound=={key:c['version'] for key,c in candidates.items()},'STALE_CANDIDATE','Candidate snapshot changed')
    decisions={d.candidate_id:d for d in cmd.candidate_decisions}
    require(set(decisions)==set(candidates) and len(decisions)==len(cmd.candidate_decisions),
            'CANDIDATE_COVERAGE_INCOMPLETE',
            'Exactly one decision per ready candidate; source gaps are read-only. '+json.dumps({
                'expected_ids':sorted(candidates),
                'missing_ids':sorted(set(candidates)-set(decisions)),
                'unexpected_ids':sorted(set(decisions)-set(candidates))},ensure_ascii=False))
    new={item.client_ref:item for item in cmd.new_events}
    for candidate in candidates.values():
        source=app.store.get('candidate_source',candidate['source_id'],family)
        current=candidate_view(app,family,source)
        require(current['status']=='ready' and current.get('source_fingerprint')==candidate.get('source_fingerprint'),
                'STALE_CANDIDATE','Underlying source changed after candidate snapshot')
        decision=decisions[candidate['id']]
        require(decision.expected_version==candidate['version'],'STALE_CANDIDATE','Wrong candidate version')
        if decision.disposition=='created':
            require(not candidate['existing_event_id'],'DUPLICATE_GOAL','Existing original goal must be linked')
            item=new.get(decision.event_ref)
            require(item and item.candidate_id==candidate['id'],'CANDIDATE_SOURCE_MISMATCH','created决定的event_ref必须填写对应new_events.client_ref，且该项candidate_id须等于本决定candidate_id；请核对这两个字段，不改变原来源合同')
        elif decision.disposition=='linked':
            if decision.event_ref in new:
                target=new[decision.event_ref]
                require(not candidate['existing_event_id'],'DUPLICATE_GOAL','Existing original goal cannot be replaced by a new Event')
                require(target.goal_key==candidate['goal_key'] and target.completion.model_dump()==candidate['completion']
                        and target.due_at==candidate['due_at'] and [p.model_dump() for p in target.delivery_parts]==candidate['delivery_parts'],
                        'CANDIDATE_SOURCE_MISMATCH','Cannot merge distinct goal contracts')
            else:
                require(decision.event_ref==candidate['existing_event_id'] and decision.event_ref,
                        'CANDIDATE_SOURCE_MISMATCH','Only canonical original goal can be linked')
        else:
            require(decision.event_ref is None,'CANDIDATE_SOURCE_MISMATCH','Deferred candidate cannot link an Event')
    for item in cmd.new_events:
        candidate=candidates.get(item.candidate_id)
        require(candidate is not None,'CANDIDATE_SOURCE_MISSING','New Event requires a trusted candidate')
        require((item.completion_condition or '').strip()==(candidate.get('completion_condition') or '').strip(),
                'COMPLETION_CONDITION_MISMATCH','完成说明按来源继承；允许为空，不自行增加或清除')
        decision=decisions[item.candidate_id]
        require(decision.disposition=='created' and decision.event_ref==item.client_ref,
                'CANDIDATE_SOURCE_MISMATCH','New Event must match candidate decision')
        require(item.goal_text==candidate['goal_text'] and item.goal_key==candidate['goal_key'] and item.completion.model_dump()==candidate['completion']
                and item.due_at==candidate['due_at'] and [p.model_dump() for p in item.delivery_parts]==candidate['delivery_parts'],
                'CANDIDATE_CONTRACT_MISMATCH','Goal, completion, parts and deadline are fixed by source')
    # A service proposal can only use its host-verified source and corresponding decision.
    by_pending={c['pending_id']:c for c in candidates.values() if c.get('pending_id')}
    for decision in cmd.pending_decisions:
        candidate=by_pending.get(decision.input_id)
        if candidate:
            trusted=decisions[candidate['id']]
            require(decision.disposition==trusted.disposition and decision.event_ref==trusted.event_ref,
                    'PENDING_CANDIDATE_MISMATCH','Pending and candidate decisions must agree')
        else:
            require(decision.disposition=='retained','PENDING_SOURCE_UNCONFIRMED',
                    'Unconfirmed or ambiguous input remains pending')


def event_source_contract(app,family,candidate_id):
    candidate=app.store.get('candidate',candidate_id,family)
    return {'source_identity':candidate['source_identity'],
            'candidate_ref':{'id':candidate['id'],'version':candidate['version']},
            'creation_source_refs':candidate['source_refs'],
            'original_deadline':candidate.get('original_deadline'),
            'requested_time':candidate.get('requested_time'),
            'completion_condition':candidate.get('completion_condition')}


def save_candidate_decisions(app,family,cmd,plan_id,mapping):
    if not app.strict_runtime:return
    for decision in cmd.candidate_decisions:
        key='candidate-decision:'+digest([family,cmd.business_date,decision.candidate_id,cmd.request_id])
        candidate=app.store.get('candidate',decision.candidate_id,family)
        source=app.store.get('candidate_source',candidate['source_id'],family)
        value=candidate_view(app,family,source)
        if {k:v for k,v in candidate.items() if k not in {'id','version'}}!=value:
            app.store.put('candidate',candidate['id'],family,value,candidate['version'])
        app.store.put('candidate_decision',key,family,{**decision.model_dump(),'plan_id':plan_id,
                      'event_id':mapping.get(decision.event_ref,decision.event_ref),'business_date':cmd.business_date})
        if source.get('rule_version','').startswith('lab-all-actions-'):
            from .lab_sources import mark_considered
            mark_considered(app,family,candidate,source)


def daily_payload(app,cmd):
    payload=cmd.model_dump()
    if payload.get("first_lesson_check_at") is None:payload.pop("first_lesson_check_at",None)
    # Empty schema extensions have the original H command meaning, including
    # historical strict Worker receipts. Any nonempty extension remains part
    # of the signed request; it cannot be stripped to replay an old receipt.
    if not payload['candidate_decisions'] and all(
            item['candidate_id'] is None and not item['delivery_parts']
            for item in payload['new_events']):
        payload.pop('candidate_decisions')
        for item in payload['new_events']:
            item.pop('candidate_id');item.pop('delivery_parts')
    from .report_contract import compact_new_defaults
    return compact_new_defaults(payload)
