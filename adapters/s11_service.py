"""A return after at least five overdue lessons, anchored to a completion event."""
import datetime as dt
import hashlib
RULE='welcome-back-to-reading'


def decisions(c,state,snapshot,raw):
    import completion_events as events
    all_events=events.records(state,raw,c)
    day=state['as_of'][:10];result=[]
    receipts={r[0] for r in c.execute('SELECT dedupe_key FROM proactive_receipts')}
    for registration in raw['enrollments']:
        lessons=[l for l in raw['learning_daily'] if events.enrollment(raw,l)['id']==registration['id']]
        known=len({l['id'] for l in lessons})==len(lessons) and len({l['lesson_no'] for l in lessons})==len(lessons)
        history=[e for e in all_events if e['enrollment_id']==registration['id'] and e['uid']==state['uid']]
        completed={};active=False;previous_completion=None
        for event in history:
            stamp=event['completed_at'];current=next((l for l in lessons if l['id']==event['lesson_id']),None)
            if not current:continue
            # Past course days only. Today's unfinished lesson is not overdue yet.
            past=[l for l in lessons if l['date']<stamp[:10]]
            calendar=[x for x in raw['course_calendar'] if registration['course_start_date']<=x['date']<stamp[:10] and (x.get('enrollment_id')==registration['id'] or len(raw['enrollments'])==1)]
            coverage=known and bool(calendar) and all(type(x.get('new_lesson_count')) is int and x['new_lesson_count']==sum(l['date']==x['date'] for l in past) for x in calendar) and all(any(x['date']==l['date'] for x in calendar) for l in past)
            before=[l for l in past if l['id'] not in completed]
            qualifies=coverage and len(before)>=5
            # A later threshold crossing during daily catch-up is still the same
            # return. Re-arm only after an intervening scheduled day without any
            # completion; gaps made only of rest days do not mean the child left.
            resumed = previous_completion is None or any(
                previous_completion[:10] < x['date'] < stamp[:10] and x.get('new_lesson_count',0)>0
                for x in calendar)
            first=qualifies and not active and resumed
            previous_completion=stamp
            if not coverage:active=True  # Unknown history cannot manufacture a new return.
            elif first:active=True
            completed[event['lesson_id']]=stamp
            after=[l for l in past if l['id'] not in completed]
            if coverage and len(after)<5:active=False
            due=(dt.datetime.fromisoformat(stamp)+dt.timedelta(minutes=30)).isoformat(timespec='seconds')
            if day not in (stamp[:10],due[:10]):continue
            key=state['run_id']+':S11:'+registration['id']+':'+event['event_id']
            ident='proactive:'+state['run_id']+':S11:'+hashlib.sha256((registration['id']+':'+event['event_id']).encode()).hexdigest()[:24]
            status='already_delivered' if key in receipts else 'unknown' if not coverage else 'eligible' if first else 'not_eligible'
            visible=next((l for l in snapshot['lessons_as_of'] if l['lesson_id']==event['lesson_id']),None)
            if not visible or not visible.get('completed_at'):status='unknown'
            missing=[l for l in snapshot['lessons_as_of'] if any(x['id']==l['lesson_id'] for x in lessons) and l['overdue']]
            result.append({'id':ident,'candidate_id':ident,'day':due[:10],'rule':RULE,'skill':RULE,'title':'恢复阅读欢迎','scope':event['event_id'],'status':status,
                'reason':'恢复前缺课至少5节，首次真实完课后30分钟欢迎回来' if first else '本轮恢复已出现完课，或未达到缺课门槛' if coverage else '历史课表或完课事实不足，无法确定恢复门槛',
                'dedupe_key':key,'purpose':'看见恢复阅读这一步，不追加补课压力','priority':95,'contacts_user':True,'delivery_format':'text',
                'time_window':{'earliest':due,'latest':due[:10]+'T23:59:59+08:00'},
                'evidence':{'event_id':event['event_id'],'completed_at':stamp,'enrollment_id':registration['id'],'lesson':visible,
                    'return_to_learning':{'threshold':5,'before_missing_count':len(before) if coverage else None,'before_missing_lessons':[{'lesson_id':l['id'],'lesson_no':l['lesson_no'],'scheduled_date':l['date']} for l in before] if coverage else [],'current_missing_count':len(missing),'current_missing_lessons':missing,'is_first_return_completion':first,'coverage_complete':coverage}}})
    return __import__('service_hours').limit_candidates(result)


def ensure(c,state):
    """Use the existing event inbox, versioned task, case, token and receipt paths."""
    import test_mode as tm,service_workflow as wf,service_content,service_ledger as ledger,completion_planner as cp
    for item in decisions(c,state,tm.snapshot(),tm.fixture_data(state,include_events=False)):
        if item['status']!='eligible' or state['as_of']>item['time_window']['latest']:continue
        if c.execute('SELECT 1 FROM service_tasks WHERE run_id=? AND candidate_id=?',(state['run_id'],item['id'])).fetchone():continue
        enrollment=item['evidence']['enrollment_id']
        if c.execute("SELECT 1 FROM service_case_constraints WHERE run_id=? AND uid=? AND enrollment_id=? AND status IN ('paused','stopped')",(state['run_id'],state['uid'],enrollment)).fetchone():continue
        if c.execute('SELECT 1 FROM contact_constraints WHERE day=?',(item['day'],)).fetchone():continue
        if wf.enabled(state):
            if wf.planning_paused(c,state,RULE,item['time_window']['earliest']):continue
            contract={'kind':'signal','skill':RULE,'skill_version':service_content.bundle_digest(RULE)}
            wf.add(c,state,item,contract,item['time_window']['earliest'],'真实恢复完课后30分钟，由宿主安排一次欢迎',{'kind':'host_completion','event_id':item['evidence']['event_id']},item['evidence']['event_id'])
        else:
            event={'uid':state['uid'],'enrollment_id':enrollment,'event_id':item['evidence']['event_id']}
            cid=ledger.case_for(c,state,event,'S11',[]);tid='service-ledger-'+ledger.ident(state['run_id'],item['dedupe_key'])
            c.execute('INSERT OR REPLACE INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',(item['id'],item['day'],RULE,item['scope'],__import__('sql_tools').encoded(item),item['status'],__import__('sql_tools').now()))
            cp.insert(c,state,tid,'signal',RULE,item['time_window']['earliest'],item['time_window']['latest'],item['id'],item['reason'])
            c.execute('INSERT OR IGNORE INTO service_case_tasks VALUES(?,?,?,?)',(tid,cid,event['event_id'],'S11'))
