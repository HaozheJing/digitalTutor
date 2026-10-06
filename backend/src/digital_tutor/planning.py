"""Planning transactions and immutable responsibility boundaries."""
from .domain import DailyCommit, ProgressCommit, require
from .storage import new_id


class PlanningMixin:
    def completion_met(self, family, event, refs):
        evidence = self.validate_refs(family, refs)
        rule = event['completion']
        if rule['kind'] == 'goal':
            completed = {e.get('lesson_ref') for kind, e in evidence if kind == 'evidence' and e.get('kind') == 'lesson_completed' and e.get('trusted') and e.get('valid', True)}
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

    def update_event(self, family, update, binding):
        event = self.event(family, update.event_id)
        require(event['version'] == update.expected_version == binding['event_versions'].get(event['id']), 'STALE_EVENT', '事项与规划快照版本不一致')
        refs = update.evidence_refs
        records = self.validate_refs(family, refs)
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
        if new_status == 'done' and old_status != 'done':
            require(self.completion_met(family, event, refs), 'COMPLETION_NOT_MET', '未满足原完成标准')
            event['remaining_work'] = []
            event['completion_history'].append({'at': self.clock.iso(), 'evidence_refs': [r.model_dump() for r in refs]})
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
            self.check_planner_snapshot(binding, cmd.expected_family_version)
            self.check_version(family, cmd.expected_family_version)
            require(cmd.business_date == self.clock.now().date().isoformat(), 'INVALID_BUSINESS_DATE', '只能提交当前业务日')
            require(self.clock.now().hour >= 8, 'DAILY_TOO_EARLY', '每日规划不能早于08:00')
            db = self.store.connection
            require(not db.execute('SELECT 1 FROM plans WHERE family_id=? AND business_date=?', (family, cmd.business_date)).fetchone(), 'DAILY_ALREADY_COMMITTED', '当天计划已经成功提交')
            require(not db.execute('SELECT 1 FROM report_links WHERE family_id=? AND resolved=0 LIMIT 1', (family,)).fetchone(), 'REPORTS_PENDING', '首版先处理未消费报告，再提交daily')
            require(len({x.client_ref for x in cmd.new_events}) == len(cmd.new_events), 'INVALID_SCHEMA', '新事项引用重复')
            mapping = {}
            for item in cmd.new_events:
                require(not db.execute('SELECT 1 FROM goals WHERE family_id=? AND goal_key=?', (family,item.goal_key)).fetchone(), 'DUPLICATE_GOAL', '相同目标沿用原事项')
                eid = new_id('event')
                value = item.model_dump(exclude={'client_ref'})
                value.update(uid=self.store.family(family)['uid'], requirement_revision=1,
                    remaining_work=item.completion.lesson_refs or item.completion.required_part_ids,
                    proposed_change=None, deadline_history=[], completion_history=[], evidence_refs=[], service_hint=None)
                if item.next_action:
                    scope = set(item.completion.lesson_refs + item.completion.required_part_ids)
                    require(set(item.next_action.target_refs).issubset(scope), 'PROGRESS_SCOPE_VIOLATION', '步骤超出目标')
                    require(item.next_action.action_kind != 'deliver_feedback' or item.completion.kind == 'delivery', 'PROGRESS_SCOPE_VIOLATION', '目标型事项不能改成独立反馈')
                self.store.put('event', eid, family, value)
                db.execute('INSERT INTO goals VALUES(?,?,?)', (family,item.goal_key,eid))
                mapping[item.client_ref] = eid
            require(len({u.event_id for u in cmd.event_updates}) == len(cmd.event_updates), 'INVALID_SCHEMA', '同事项不能更新两次')
            for update in cmd.event_updates:
                self.update_event(family, update, binding)
            refs = [mapping.get(r,r) for r in cmd.plan_refs]
            require(len(refs) == len(set(refs)), 'INVALID_SCHEMA', '计划成员重复')
            for eid in refs:
                require(self.event(family,eid)['status'] not in {'done','cancelled'}, 'INVALID_PLAN_MEMBER', '不把已结束事项加入主动范围')
            pendings = {p['id']: p for p in self.store.list('pending',family) if p['disposition']=='pending'}
            require({p.input_id for p in cmd.pending_decisions} == set(pendings) and len(cmd.pending_decisions)==len(pendings), 'INPUT_COVERAGE_INCOMPLETE', '待规划输入必须逐一决定或保留')
            for decision in cmd.pending_decisions:
                entry = pendings[decision.input_id]
                eid = mapping.get(decision.event_ref, decision.event_ref)
                if decision.disposition in {'created','linked'}:
                    require(bool(eid), 'INVALID_SCHEMA', '采用需求必须关联事项')
                    self.event(family,eid)
                else:
                    require(eid is None, 'INVALID_SCHEMA', '保留或不采用时不关联事项')
                entry.update(disposition='pending' if decision.disposition=='retained' else decision.disposition,
                             event_id=eid, decision_reason=decision.reason)
                self.store.put('pending',entry['id'],family,entry,entry['version'])
            pid = new_id('plan')
            plan = self.store.put('plan',pid,family,{'business_date':cmd.business_date,
                'items':[{'event_id':eid,'rank':i+1} for i,eid in enumerate(refs)], 'created_at':self.clock.iso()})
            db.execute('INSERT INTO plans VALUES(?,?,?)',(family,cmd.business_date,pid))
            return {'plan':plan,'event_ids':mapping}
        return self.write(token, {'planner_daily'}, 'commit_daily', cmd.model_dump(), action)

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
            self.validate_refs(family,report['payload']['evidence_refs'])
            events=[self.update_event(family,u,binding) for u in cmd.event_updates]
            report['resolution']='applied'
            report['commit_request_id']=cmd.request_id
            self.store.put('report',key,family,report,report['version'])
            self.store.connection.execute('UPDATE report_links SET resolved=1 WHERE report_key=?',(key,))
            # The Worker alone terminates its lease after the native run exits.
            # Plan membership/rank are immutable; the item view joins the current Event.
            return {'events':[self.event(family,e['id']) for e in events], 'report_id':cmd.report_id,'revision':cmd.revision}
        return self.write(token,{'planner_progress'},'commit_progress',cmd.model_dump(),action)
