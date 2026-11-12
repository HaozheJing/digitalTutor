"""Atomic planning records shared by baseline and bounded admission commits."""
from .domain import require, timestamp
from .storage import new_id


def validate_step(event):
    require(event['status'] == 'waiting' or event.get('next_action'),
            'PLAN_STEP_REQUIRED', '选入或承接的事项必须有明确下一步，或有据等待')
    if event['status'] == 'waiting':
        require(event.get('waiting') and event['waiting'].get('resume_condition'),
                'INVALID_WAITING', '等待必须有恢复条件')
    if event.get('next_action') and event['next_action'].get('execute_at'):
        timestamp(event['next_action']['execute_at'])


def create_events(app, family, items):
    from .candidates import event_source_contract
    mapping = {}
    require(len({x.client_ref for x in items}) == len(items), 'INVALID_SCHEMA', '新事项引用重复')
    for item in items:
        require(not app.store.connection.execute('SELECT 1 FROM goals WHERE family_id=? AND goal_key=?',
                (family, item.goal_key)).fetchone(), 'DUPLICATE_GOAL', '相同目标沿用原事项')
        eid = new_id('event')
        value = item.model_dump(exclude={'client_ref'} if app.strict_runtime else {'client_ref','candidate_id','delivery_parts'})
        for name in ('service_action_id','service_cycle_end_at','service_slot_parts'):
            if value.get(name) is None:value.pop(name,None)
        value.update(uid=app.store.family(family)['uid'], requirement_revision=1,
                     remaining_work=item.completion.lesson_refs or item.completion.required_part_ids,
                     proposed_change=None, deadline_history=[], completion_history=[], evidence_refs=[], service_hint=None)
        if app.strict_runtime:
            value.update(event_source_contract(app, family, item.candidate_id))
            value['completion_contract_version'] = 1
        if item.next_action:
            scope = set(item.completion.lesson_refs + item.completion.required_part_ids)
            require(set(item.next_action.target_refs).issubset(scope), 'PROGRESS_SCOPE_VIOLATION', '步骤超出目标')
            require(item.next_action.action_kind != 'deliver_feedback' or item.completion.kind == 'delivery',
                    'PROGRESS_SCOPE_VIOLATION', '目标型事项不能改成独立反馈')
        from .first_lesson_check import validate_plan
        validate_plan(app,family,value)
        saved=app.store.put('event', eid, family, value)
        source=next((s for s in app.store.list('candidate_source',family) if any(r.get('kind')=='candidate_source' and r.get('id')==s['id'] for r in value.get('creation_source_refs',[]))),None)
        action_id=(source or {}).get('service_action_id') or item.service_action_id
        if source and source.get('service_action_id'):
            require(item.service_action_id in (None,action_id),'ACTION_RULE_SCOPE','动作须与来源一致')
        if action_id:
            from .action_completion import bind_contract
            slots=item.service_slot_parts
            cycle=item.service_cycle_end_at
            if source and action_id in {'S02','S04'} and len(source.get('parts',[]))==1:
                from .action_completion import RULES
                slots={s:source['parts'][0]['part_id'] for s in RULES[action_id]['required_slots']}
                require(item.service_slot_parts in (None,slots),'ACTION_RULE_SCOPE','鼓励须沿用单条消息交付，逐项核验内容')
            elif source and action_id=='S10' and source.get('rule_version')=='lab-all-actions-v2' and [p['part_id'] for p in source.get('parts',[])]==['weekly_package']:
                from .action_completion import RULES
                slots={s:'weekly_package' for s in RULES[action_id]['required_slots']}
                require(item.service_slot_parts in (None,slots),'ACTION_RULE_SCOPE','请省略service_slot_parts，由宿主自动绑定。若明确填写，只允许weekly_review、next_schedule、next_preview、applicable_arrangement四个键，值均为weekly_package；不可改名、增删。')
            elif source and ((source.get('service_action_id') or '').startswith('I') or source.get('rule_version','').startswith('lab-all-actions-')):
                from .action_completion import RULES
                slots={s:s for s in RULES[action_id]['required_slots']}
                require(item.service_slot_parts in (None,slots),'ACTION_RULE_SCOPE','互动槽位须沿用原来源')
                cycle=source.get('service_cycle_end_at')
                require(item.service_cycle_end_at in (None,cycle),'ACTION_RULE_SCOPE','互动周期须沿用宿主来源，不可延长')
            elif source and source.get('service_action_id') and len(source.get('parts',[]))==1:
                from .action_completion import RULES
                slots={s:source['parts'][0]['part_id'] for s in RULES[action_id]['required_slots']}
            saved=bind_contract(app,family,saved,action_id,cycle_end_at=cycle,slot_parts=slots,source_batch_id=(source or {}).get('batch_id'))
            if source and source.get('trigger_counting_policy'):
                app.store.put('action_trigger_policy','trigger:'+eid,family,{'event_id':eid,'requirement_revision':1,'host_verified':True,'source_ref':{'kind':'candidate_source','id':source['id'],'version':source['version']},'definition':source['trigger_counting_policy']})
        app.store.connection.execute('INSERT INTO goals VALUES(?,?,?)', (family, item.goal_key, eid))
        mapping[item.client_ref] = eid
    return mapping


def current_plan(app, family, date):
    return next((p for p in app.store.list('plan', family) if p['business_date'] == date), None)


def save_plan(app, family, cmd, event_ids, reason):
    old = current_plan(app, family, cmd.business_date)
    prior = [item['event_id'] for item in (old or {}).get('items', [])]
    ids = (event_ids + [eid for eid in prior if eid not in event_ids]) if reason == 'baseline' else (
        prior + [eid for eid in event_ids if eid not in prior])
    pid = old['id'] if old else new_id('plan')
    revision = old.get('revision', 1) + 1 if old else 1
    authorities = {i['event_id']: i.get('authority','baseline') for i in (old or {}).get('items', [])}
    for eid in event_ids:
        if reason == 'baseline' or eid not in authorities:
            authorities[eid] = reason
    value = {**(old or {}), 'business_date': cmd.business_date,
             'items': [{'event_id': eid, 'rank': i + 1, **({'authority': authorities[eid]} if app.strict_runtime else {})} for i, eid in enumerate(ids)],
             'created_at': (old or {}).get('created_at', app.clock.iso()),
             'baseline_committed': reason == 'baseline' or bool(old and old.get('baseline_committed', True)),
             'revision': revision,
             'revisions': (old or {}).get('revisions', []) + [{
                 'revision': revision, 'reason': reason, 'request_id': cmd.request_id,
                 'event_ids': event_ids, 'at': app.clock.iso()}]}
    saved = app.store.put('plan', pid, family, value, old['version'] if old else None)
    if not old:
        app.store.connection.execute('INSERT INTO plans VALUES(?,?,?)', (family, cmd.business_date, pid))
    return saved


def apply_pending_decision(app, family, decision, mapping, request_id):
    entry = app.store.get('pending', decision.input_id, family)
    eid = mapping.get(decision.event_ref, decision.event_ref)
    if decision.disposition in {'created', 'linked'}:
        require(eid, 'INVALID_SCHEMA', '采用需求必须关联事项')
        app.event(family, eid)
    else:
        require(eid is None, 'INVALID_SCHEMA', '保留或不采用时不关联事项')
    entry.update(disposition='pending' if decision.disposition == 'retained' else decision.disposition,
                 event_id=eid, decision_reason=decision.reason, decision_request_id=request_id,
                 await_decision=False, decision_state=decision.disposition,
                 decision_revision=entry.get('decision_revision', 1))
    return app.store.put('pending', entry['id'], family, entry, entry['version'])

