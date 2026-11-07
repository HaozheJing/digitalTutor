"""Weekly learning evidence and send-time S07 validation; wakeups remain unconditional."""
import datetime as dt

RULE = 'rebuild-reading-rhythm'


def weekly_learning(snapshot, calendar):
    day = dt.date.fromisoformat(snapshot['as_of'][:10])
    monday = day - dt.timedelta(days=day.weekday())
    rows = snapshot['lessons_as_of']
    # A real conversation snapshot can have no contemporaneous enrollment.
    # Missing learning data is unknown, not zero completion.
    start = min((e['course_start_date'] for e in snapshot['enrollments']), default=None)
    def block(begin, end):
        dates = [(begin + dt.timedelta(days=i)).isoformat() for i in range((end-begin).days+1)]
        expected = [c for c in calendar if c['date'] in dates and c.get('is_course_day')]
        actual = [r for r in rows if r['scheduled_date'] in dates]
        covered = start is not None and all(d < start or any(c['date']==d for c in calendar) for d in dates)
        covered = covered and len(actual)==len(expected) and len({r['lesson_id'] for r in actual})==len(actual)
        covered = covered and all(sum(r['scheduled_date']==c['date'] and r['lesson_no']==c['lesson_no'] for r in actual)==1 for c in expected)
        covered = covered and all(r['status'] in ('completed','absent','not_started','in_progress') for r in actual)
        return {'start':str(begin),'end':str(end),'coverage_complete':covered,
                'scheduled':len(expected),'completed':sum(r['status']=='completed' for r in actual) if covered else None,
                'lessons':actual}
    return {'as_of':snapshot['as_of'],'counting_basis':'课程所属自然周（周一至周日），按当前可见完课状态统计；补课归原课所属周',
            'previous_week':block(monday-dt.timedelta(days=7),monday-dt.timedelta(days=1)),
            'current_week':block(monday,day)}


def eligibility(summary):
    previous=summary['previous_week'];current=summary['current_week']
    # Either known negative is sufficient even if the other week is unknown.
    if previous['coverage_complete'] and previous['completed']>=4:
        return 'not_eligible','上周已完成至少4节，本周无需S07督学'
    if current['coverage_complete'] and current['completed']>0:
        return 'not_eligible','本周已有完课，无需S07督学'
    if not previous['coverage_complete'] or not current['coverage_complete']:
        return 'unknown','周学情覆盖不完整，不能把缺失记录当作0完课'
    return 'eligible','上周完课少于4节且本周截至当前0完课'


def candidate_id(run,day):
    return 'proactive:'+run+':S07:'+day


def decision(connection,state,snapshot):
    day=state['as_of'][:10]
    if dt.date.fromisoformat(day).weekday()!=2:return None
    summary=snapshot['weekly_learning'];status,reason=eligibility(summary)
    scope=summary['current_week']['start'];dedupe=state['run_id']+':S07:'+scope
    if connection.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?',(dedupe,)).fetchone():status='already_delivered'
    ident=candidate_id(state['run_id'],day)
    return {'id':ident,'candidate_id':ident,'day':day,'rule':RULE,'skill':RULE,'title':'周三阅读习惯检查',
            'scope':scope,'status':status,'reason':reason,'dedupe_key':dedupe,
            'purpose':'智能体先查周学情，再结合课程价值与家庭安排设计督学建议',
            'evidence':{'weekly_learning':summary,'overdue_lessons':[r for r in snapshot['lessons_as_of'] if r['status']!='completed']},
            'priority':65,'contacts_user':True,'delivery_format':'text',
            'time_window':{'earliest':day+'T19:00:00+08:00','latest':day+'T23:59:00+08:00'}}
