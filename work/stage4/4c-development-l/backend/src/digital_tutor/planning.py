"""Planning transactions and immutable responsibility boundaries."""
from .domain import DailyCommit, ProgressCommit, require
from .storage import new_id


class PlanningMixin:
    def completion_met(self, family, event, refs, report=None):
        from .report_contract import resolve_refs
        evidence = resolve_refs(self, family, refs, report)
        rule = event['completion']
        if rule['kind'] == 'goal':
            completed = {e.get('lesson_ref') for kind, e in evidence if kind == 'evidence' and e.get('kind') == 'lesson_completed' and e.get('trusted') and e.get('valid', True) and self.evidence_matches_goal(event,e)}
            return set(rule['lesson_refs']).issubset(completed)
        required = set(rule['required_part_ids'])
        confirmed = set()
        family_rec = self.store.family(family)
        for kind, op in evidence:
            if kind != 'operation' or op.get('event_id') != event['id']:
                continue
            if op.get('requirement_revision') != event['requirement_revision'] or op.get('recipient') != family_rec['recipient']:
                continue
            if op.get('stage') == 'delivered' or (rule['required_stage'] == 'sent' and op.get('stage') == 'sent'):
                confirmed.add(op.get('part_id'))
        return required.issubset(confirmed)

    def evidence_matches_goal(self, event, evidence):
        if not evidence.get('source_origin'):
            return True
        identity = event.get('source_identity') or {}
        source = evidence.get('source_snapshot') or {}
        if not identity:
            return not self.strict_runtime
        return identity.get('course_id') == source.get('course_id') and identity.get('course_version') == source.get('course_version')

    def update_event(self, family, update, binding, report=None):
        event = self.event(family, update.event_id)
        require(event['version'] == update.expected_version == binding['event_versions'].get(event['id']), 'STALE_EVENT', '事项与规划快照版本不一致')
        refs = update.evidence_refs
        from .report_contract import resolve_refs, require_assessment
        records = resolve_refs(self, family, refs, report)
        changes = update.changes.model_dump(exclude_unset=True)
        require(bool(refs) or not changes, 'EVIDENCE_MISSING', '事项变更必须有依据')
        proposed = changes.pop('proposed_due_at', None)
        accept = changes.pop('accept_change_id', None)
        require(not (proposed and accept), 'INVALID_CHANGE', '提议与接受不能同次修改')
        if proposed:
            event['proposed_change'] = {'change_id': new_id('change'), 'old_due_at': event['due_at'],
                'proposed_due_at': proposed, 'proposed_at': self.clock.iso(), 'status': 'proposed'}
        if accept:
            change = event.get('proposed_change')
            require(change and change['change_id'] == accept and change['status'] == 'proposed', 'INVALID_CHANGE', '无适用待确认改约')
            accepted = any(kind == 'evidence' and e.get('trusted') and e.get('valid', True) and e.get('kind') == 'accept_change' and e.get('change_id') == accept and e.get('event_id') == event['id'] for kind, e in records)
            require(accepted, 'EVIDENCE_MISSING', '改约需要关联本提议的有效接受证据')
            event['deadline_history'].append({'due_at': event['due_at'], 'replaced_at': self.clock.iso(), 'evidence_refs': [r.model_dump() for r in refs]})
            event['due_at'] = change['proposed_due_at']
            event['proposed_change'] = {**change, 'status': 'accepted', 'accepted_at': self.clock.iso()}
        old_status = event['status']
        new_status = changes.get('status', old_status)
        allowed = {'pending': {'pending','in_progress','waiting','done','cancelled'},
                   'in_progress': {'in_progress','waiting','done','cancelled'},
                   'waiting': {'waiting','in_progress','done','cancelled'},
                   'done': {'done','waiting','in_progress'}, 'cancelled': {'cancelled'}}
        require(new_status in allowed[old_status], 'INVALID_TRANSITION', '状态迁移不允许')
        if old_status == 'done' and new_status != 'done':
            require(any(k == 'evidence' and e.get('trusted') and e.get('valid', True) and e.get('kind') == 'correction' and e.get('event_id') == event['id'] for k,e in records), 'EVIDENCE_MISSING', '重开需权威更正')
        if old_status == 'done' and new_status != 'done':
            require(not any(k=='evidence' and e.get('source_origin') and e.get('kind')=='correction'
                    and e.get('corrected_completion_status')=='completed' for k,e in records),
                    'COMPLETION_STILL_MET','源仍确认完成，不能仅因版本变化重开')
        if new_status == 'done' and old_status != 'done':
            require_assessment(self, family, event, report)
            require(self.completion_met(family, event, refs, report), 'COMPLETION_NOT_MET', '未满足原完成标准')
            event['remaining_work'] = []
            event['completion_history'].append({'at': self.clock.iso(), 'evidence_refs': [r.model_dump() for r in refs],
                'service_assessments': (report or {}).get('payload',{}).get('completion_assessments',[])})
        if new_status == 'cancelled' and old_status != 'cancelled':
            require(any(k == 'evidence' and e.get('trusted') and e.get('valid', True) and e.get('kind') == 'cancel' and e.get('event_id') == event['id'] for k,e in records), 'EVIDENCE_MISSING', '取消需有效依据')
        event.update(changes)
        if old_status == 'done' and new_status != 'done':
            event['remaining_work'] = event['completion']['lesson_refs'] or event['completion']['required_part_ids']
        if new_status in {'done', 'cancelled'}:
            event.update(next_action=None, waiting=None)
        require((event['status'] == 'waiting') == (event['waiting'] is not None), 'INVALID_WAITING', '等待状态与等待条件不一致')
        if event['next_action']:
            step = event['next_action']
            scope = set(event['completion']['lesson_refs'] + event['completion']['required_part_ids'])
            require(set(step['target_refs']).issubset(scope), 'PROGRESS_SCOPE_VIOLATION', '下一步超出原目标范围')
            if step['action_kind'] == 'deliver_feedback':
                require(event['completion']['kind'] == 'delivery', 'PROGRESS_SCOPE_VIOLATION', '目标型事项不能改成独立反馈')
        event['evidence_refs'] += [r.model_dump() for r in refs if r.model_dump() not in event['evidence_refs']]
        saved = self.store.put('event', event['id'], family, event, event['version'])
        return saved

    def commit_daily(self, token, args):
        cmd = DailyCommit.model_validate(args)
        def action(family, binding):
            from .planning_records import create_events, save_plan, validate_step, apply_pending_decision
            from .scheduling import baseline_committed
            from .candidates import validate_daily_candidates, save_candidate_decisions
            self.check_planner_snapshot(binding, cmd.expected_family_version)
            self.check_version(family, cmd.expected_family_version)
            require(binding.get('planning_reason','baseline') == 'baseline', 'PLANNING_SCOPE', '日内任务不能提交全量基线')
            require(cmd.business_date == self.clock.now().date().isoformat(), 'INVALID_BUSINESS_DATE', '只能提交当前业务日')
            require(self.clock.now().hour >= 8, 'DAILY_TOO_EARLY', '每日规划不能早于08:00')
            require(not baseline_committed(self,family,cmd.business_date), 'DAILY_ALREADY_COMMITTED', '当天基线已成功提交')
            require(not self.store.connection.execute('SELECT 1 FROM report_links WHERE family_id=? AND resolved=0 LIMIT 1',(family,)).fetchone(), 'REPORTS_PENDING', '先消费报告再规划')
            validate_daily_candidates(self, family, binding, cmd)
            mapping = create_events(self, family, cmd.new_events)
            require(len({u.event_id for u in cmd.event_updates}) == len(cmd.event_updates), 'INVALID_SCHEMA', '同事项不能更新两次')
            for update in cmd.event_updates:
                self.update_event(family, update, binding)
            refs = [mapping.get(r,r) for r in cmd.plan_refs]
            require(len(refs) == len(set(refs)), 'INVALID_SCHEMA', '计划成员重复')
            for eid in refs:
                event = self.event(family,eid)
                require(event['status'] not in {'done','cancelled'}, 'INVALID_PLAN_MEMBER', '不能选入已结束事项')
                if self.strict_runtime: validate_step(event)
            pendings = {p['id'] for p in self.store.list('pending',family) if p['disposition']=='pending'}
            require({p.input_id for p in cmd.pending_decisions} == pendings and len(cmd.pending_decisions)==len(pendings), 'INPUT_COVERAGE_INCOMPLETE', '待规划输入必须逐一决定')
            for decision in cmd.pending_decisions:
                apply_pending_decision(self,family,decision,mapping,cmd.request_id)
            plan = save_plan(self,family,cmd,refs,'baseline')
            save_candidate_decisions(self,family,cmd,plan['id'],mapping)
            # Explicit family requests accepted by baseline have the same durable responsibility.
            from .admission import grant_accepted_requests
            grants = grant_accepted_requests(self,family,cmd,mapping)
            return {'plan':plan,'event_ids':mapping,'accepted_commitment_ids':grants}
        from .candidates import daily_payload
        return self.write(token, {'planner_daily'}, 'commit_daily', daily_payload(self,cmd), action)

    def commit_admission(self, token, args):
        from .admission import commit_admission
        return commit_admission(self, token, args)

    def commit_progress(self, token, args):
        cmd = ProgressCommit.model_validate(args)

        def action(family,binding):
            require(binding.get('report_id')==cmd.report_id and binding.get('revision')==cmd.revision, 'PROGRESS_SCOPE_VIOLATION','报告不是此运行绑定版本')
            self.check_planner_snapshot(binding,cmd.expected_family_version)
            self.check_version(family,cmd.expected_family_version)
            key=self.report_key(family,cmd.report_id,cmd.revision)
            report=self.store.get('report',key,family)
            require(report['resolution'] is None,'REPORT_CONSUMED','报告已处理或被替代')
            ids=[u.event_id for u in cmd.event_updates]
            require(set(ids)==set(report['event_ids']) and len(ids)==len(set(ids)),'PROGRESS_SCOPE_VIOLATION','必须精确更新全部关联原事项')
            # New input can invalidate the report's old evidence; obtain a revised report.
            from .report_contract import resolve_refs
            resolve_refs(self,family,report['payload']['evidence_refs'],report)
            events=[self.update_event(family,u,binding,report) for u in cmd.event_updates]
            if self.strict_runtime:
                from .planning_records import validate_step
                selected={i['event_id'] for p in self.store.list('plan',family) if p['business_date']==self.clock.now().date().isoformat() for i in p['items']}
                for event in events:
                    if event['status'] not in {'done','cancelled'} and (event['id'] in selected or event.get('accepted_commitment')):
                        validate_step(event)
            report['resolution']='applied'
            report['commit_request_id']=cmd.request_id
            self.store.put('report',key,family,report,report['version'])
            self.store.connection.execute('UPDATE report_links SET resolved=1 WHERE report_key=?',(key,))
            from .request_responsibility import settle_completed, progress_steps
            settle_completed(self,family,events,report)
            progress_steps(self,family,events,report)
            # The Worker alone terminates its lease after the native run exits.
            # Plan membership/rank are immutable; the item view joins the current Event.
            return {'events':[self.event(family,e['id']) for e in events], 'report_id':cmd.report_id,'revision':cmd.revision}
        from .report_contract import compact_new_defaults
        return self.write(token,{'planner_progress'},'commit_progress',compact_new_defaults(cmd.model_dump()),action)
