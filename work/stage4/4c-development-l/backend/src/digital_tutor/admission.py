"""Bounded intraday planning; durable acceptance is committed with its source decision."""
from datetime import timedelta
from .domain import AdmissionCommit, SHANGHAI, require, timestamp
from .storage import digest
from .report_contract import message_source_digest
from .planning_records import create_events, current_plan, save_plan, validate_step, apply_pending_decision


def grant_accepted_requests(app, family, cmd, mapping):
    from .scheduling import step_key
    grants = []
    for decision in cmd.pending_decisions:
        if decision.disposition not in {'created','linked'}:
            continue
        pending = app.store.get('pending',decision.input_id,family)
        if not pending.get('planning_requested'):
            continue
        eid = mapping.get(decision.event_ref,decision.event_ref)
        event = app.event(family,eid)
        validate_step(event)
        require(event['status'] in {'pending','in_progress'} and event.get('next_action'),
                'ADMISSION_NOT_EXECUTABLE', '正式承接定时服务需可执行下一步，缺条件则保留待安排')
        step = event['next_action']
        require(step.get('execute_at'), 'EXECUTION_TIME_REQUIRED', '正式承接需明确本步骤业务执行时间')
        now = app.clock.now()
        start = timestamp(step['execute_at']) if step.get('execute_at') else now
        from .service_requests import normalize_requested_time
        requested = normalize_requested_time(pending.get('requested_time') or {})
        meaning = requested.get('meaning')
        wanted_start = requested.get('start') or (requested.get('value') if meaning == 'schedule' and requested.get('precision') == 'datetime' else None)
        wanted_end = (requested.get('end') if meaning in {'before','window'} else None) or (
            requested.get('value') if meaning == 'completion_deadline' and requested.get('precision') == 'datetime' else None)
        # An at-time is a not-before arrangement, not an invented deadline.
        # Existing Event deadlines remain binding even when this request is at.
        hard_ends=[timestamp(value) for value in (wanted_end,event.get('due_at')) if value]
        wanted_end=min(hard_ends).isoformat() if hard_ends else None
        if wanted_start:
            require(step.get('execute_at') and start >= timestamp(wanted_start), 'SCHEDULE_SOURCE_MISMATCH', '不得提前履行约定时间')
            if meaning in {'at','schedule'}:
                require(start == timestamp(wanted_start), 'SCHEDULE_SOURCE_MISMATCH', '替代时间需有效改约，不能单向通知改变')
        if meaning == 'schedule' and requested.get('precision') == 'date':
            require(start.astimezone(SHANGHAI).date().isoformat() == requested['value'], 'SCHEDULE_SOURCE_MISMATCH', '保留原约定日期')
        if wanted_end:
            require(start <= timestamp(wanted_end) and now <= timestamp(wanted_end), 'DEADLINE_PASSED', '不能承接已过期或排到截止以后')
        local = start.astimezone(SHANGHAI)
        require(app.service_start <= local.hour < app.service_end, 'WINDOW_CLOSED', '常规执行时间须在服务窗口')
        require(start >= now, 'SCHEDULE_IN_PAST', '不能补一个过去的排期冒充按时接纳')
        end = local.replace(hour=app.service_end,minute=0,second=0,microsecond=0)
        if wanted_end:
            end = min(end,timestamp(wanted_end))
        candidate = next((c for c in app.store.list('candidate',family) if c.get('pending_id') == pending['id']), None)
        require(candidate and candidate['status']=='ready','CANDIDATE_SOURCE_MISSING','承接须有当前来源候选')
        require(not event.get('update_pending'),'REPORTS_PENDING','原事项有未消费进展')
        message = app.store.get('message',pending['source_message_id'],family)
        actions = ['deliver_feedback','verify_operation','clarify_existing'] if event['completion']['kind']=='delivery' else ['remind_makeup','verify_learning','clarify_existing']
        require(step['action_kind'] in actions, 'PROGRESS_SCOPE_VIOLATION', '步骤超出原服务类型')
        old = event.get('accepted_commitment')
        reusing = bool(old and old.get('status') == 'active')
        if reusing:
            require(old['authorized_step_key']==step_key(event) and
                    timestamp(old['valid_from'])==start and timestamp(old['valid_until'])==end and
                    old['requirement_revision']==event['requirement_revision'],
                    'COMMITMENT_CONFLICT', '原事项已有不同约定，走原事项有据改约')
        if step['action_kind'] in {'deliver_feedback','remind_makeup','clarify_existing'}:
            from .send_policy import ContactPolicy, action_paused
            require(not action_paused(app,family,step['action_kind'],eid),'CONTROL_ACTIVE','暂停期间不能承诺对应主动动作')
        if not reusing and step['action_kind'] in {'deliver_feedback','remind_makeup','clarify_existing'}:
            day = local.date().isoformat()
            from .contact_capacity import committed_contacts
            require(committed_contacts(app,family,day,exclude_event=eid) < getattr(app,'contact_policy',ContactPolicy()).proactive_per_day,
                    'CONTACT_LIMIT','已承接的当日服务占满模拟联系额度，不能静默挤掉旧约定')
            if step['action_kind']=='remind_makeup':
                from .contact_capacity import committed_reminders
                require(all(count < getattr(app,'contact_policy',ContactPolicy()).reminders_per_lesson_week
                            for count in committed_reminders(app,family,day,event['completion']['lesson_refs'],eid).values()),
                        'CONTACT_LIMIT','原课节周提醒额度不足，不能承诺必被拒绝的主动动作')
        if reusing:
            aliases = event.get('accepted_request_refs', [])
            if not any(r['pending_id']==pending['id'] for r in aliases):
                aliases.append({'pending_id':pending['id'],'commit_request_id':cmd.request_id,
                                'source_message_ref':{'id':message['id'],'version':message['version']}})
                event['accepted_request_refs']=aliases
                app.store.put('event',eid,family,event,event['version'])
            grants.append(old['id'])
            continue
        grant = {'id':'commitment:'+digest([family,pending['id'],pending.get('decision_revision',1)]),
                 'status':'active','source_pending_id':pending['id'],
                 'source_message_ref':{'id':message['id'],'version':message['version']},
                 'source_message_digest':message_source_digest(message),
                 'commit_request_id':cmd.request_id,'candidate_ref':{'id':candidate['id'],'version':candidate['version']},
                 'requirement_revision':event['requirement_revision'],'authorized_step_key':step_key(event),
                 'authorized_target_refs':event['completion']['lesson_refs'] or event['completion']['required_part_ids'],
                 'authorized_action_kinds':actions,'valid_from':start.isoformat(),'valid_until':end.isoformat(),
                 'schedule_revision':1}
        event['accepted_commitment'] = grant
        app.store.put('event',eid,family,event,event['version'])
        grants.append(grant['id'])
    return grants


def enqueue_request(app, family, pending):
    from .jobs import JobQueue
    revision = pending.get('decision_revision',1)
    JobQueue(app).enqueue(family,'admission:'+digest([family,pending['id'],revision]),
        {'kind':'daily','planning_reason':'admission','pending_id':pending['id'],'decision_revision':revision})


def commit_admission(app, token, args):
    cmd = AdmissionCommit.model_validate(args)
    def action(family,binding):
        app.check_planner_snapshot(binding,cmd.expected_family_version)
        app.check_version(family,cmd.expected_family_version)
        require(binding.get('planning_reason') == 'admission' and binding.get('pending_id') == cmd.pending_id,
                'PLANNING_SCOPE','只接纳宿主绑定的新需求')
        require(app.mode != 'passive','PASSIVE_MODE','被动模式不能接纳主动安排')
        require(cmd.business_date == app.clock.now().date().isoformat(),'INVALID_BUSINESS_DATE','仅修订当前日期视图')
        require(app.service_start <= app.clock.now().hour < app.service_end,'WINDOW_CLOSED','当前不在接纳窗口')
        require(not app.store.connection.execute('SELECT 1 FROM report_links WHERE family_id=? AND resolved=0 LIMIT 1',(family,)).fetchone(),
                'REPORTS_PENDING','先消费原报告，不绕门禁接单')
        pending = app.store.get('pending',cmd.pending_id,family)
        require(pending['version'] == binding['pending_version'] and pending['disposition']=='pending' and pending.get('await_decision'),
                'ADMISSION_NOT_PENDING','需求已决定或版本改变')
        plan = current_plan(app,family,cmd.business_date)
        revision = plan.get('revision',1) if plan else 0
        require(cmd.expected_plan_revision == revision == binding['plan_revision'],'STALE_PLAN','安排已变化，须新快照')
        require(len(cmd.pending_decisions)==1 and cmd.pending_decisions[0].input_id==cmd.pending_id,
                'PLANNING_SCOPE','只处理绑定需求，不整体重排')
        from .candidates import validate_daily_candidates, save_candidate_decisions
        validate_daily_candidates(app,family,binding,cmd)
        allowed = {c['existing_event_id'] for c in app.store.list('candidate',family)
                   if c.get('pending_id')==cmd.pending_id and c.get('existing_event_id')}
        require(all(u.event_id in allowed for u in cmd.event_updates),'PLANNING_SCOPE','不能修改无关事项')
        require(len({u.event_id for u in cmd.event_updates})==len(cmd.event_updates),'INVALID_SCHEMA','事项更新重复')
        mapping = create_events(app,family,cmd.new_events)
        for update in cmd.event_updates:
            require(set(update.changes.model_fields_set) <= {'next_action','waiting','status'}, 'PLANNING_SCOPE','接纳不改原截止或完成标准')
            require(update.changes.status not in {'done','cancelled'},'PLANNING_SCOPE','接纳不代替原进展结案')
            app.update_event(family,update,binding)
        refs = [mapping.get(r,r) for r in cmd.plan_refs]
        require(len(refs)==len(set(refs)) and set(refs) <= allowed | set(mapping.values()),'PLANNING_SCOPE','不得追加无关成员')
        decision = cmd.pending_decisions[0]
        accepted = decision.disposition in {'created','linked'}
        target = mapping.get(decision.event_ref,decision.event_ref)
        require(set(refs)==({target} if accepted else set()),'PLANNING_SCOPE','接纳成员必须与真实决定一致')
        require(accepted or not cmd.new_events and not cmd.event_updates,'PLANNING_SCOPE','未接纳不得改正式事项')
        for eid in refs: validate_step(app.event(family,eid))
        apply_pending_decision(app,family,decision,mapping,cmd.request_id)
        saved = save_plan(app,family,cmd,refs,'admission')
        save_candidate_decisions(app,family,cmd,saved['id'],mapping)
        grants = grant_accepted_requests(app,family,cmd,mapping)
        return {'plan':saved,'event_ids':mapping,'pending_id':cmd.pending_id,
                'accepted_commitment_ids':grants,'decision':decision.disposition}
    from .candidates import daily_payload
    return app.write(token, {'planner_daily'}, 'commit_admission', daily_payload(app,cmd), action)
