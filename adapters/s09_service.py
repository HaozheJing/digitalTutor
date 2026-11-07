"""Friday 20:00 S09 eligibility; the fixed weekly PDF has an independent host path."""
import datetime as dt
RULE='plan-weekend-makeup'
def eligibility(summary,day):
    if dt.date.fromisoformat(day).weekday()!=4:return 'not_eligible','非周五'
    current=summary['current_week']
    if not current['coverage_complete']:return 'unknown','本周课表或学情不完整，不能当成零完课'
    if not current['scheduled']:return 'not_eligible','本周无应学课程'
    if any(x['status']!='completed' for x in current['lessons']):return 'eligible','本周有至少一节未完课，包括有应学课程的零完课情况'
    return 'not_eligible','本周应学课程已全部完成'
def decision(c,state,snapshot):
    day=state['as_of'][:10]
    if dt.date.fromisoformat(day).weekday()!=4:return None
    summary=snapshot['weekly_learning'];status,reason=eligibility(summary,day);scope=summary['current_week']['start']
    makeup=makeup_scope(snapshot)
    if makeup['status']=='unknown':status,reason='unknown',makeup['reason']
    key=state['run_id']+':S09:'+scope
    if c.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?',(key,)).fetchone():status='already_delivered'
    ident='proactive:'+state['run_id']+':S09:'+day
    return {'id':ident,'candidate_id':ident,'day':day,'rule':RULE,'skill':RULE,'title':'周五周末补读邀请','scope':scope,'status':status,'reason':reason,'dedupe_key':key,'purpose':'结合本周实际缺课与家庭安排，邀请接上阅读','evidence':{'weekly_learning':summary,'overdue_lessons':makeup.get('ordered_lessons',[])},'priority':45,'contacts_user':True,'delivery_format':'text','time_window':{'earliest':day+'T20:00:00+08:00','latest':day+'T23:59:00+08:00'}}


def makeup_scope(snapshot):
    """Select known incomplete lessons, never infer missing records or durations."""
    day=snapshot['as_of'][:10]
    week=snapshot['weekly_learning']['current_week']
    seen={};unknown=[]
    for row in snapshot.get('lessons_as_of',[]):
        ident=row.get('lesson_id');date=row.get('scheduled_date')
        if not ident or not date or date>day:continue
        item={k:row.get(k) for k in ('lesson_id','lesson_no','scheduled_date','status','title')}
        if ident in seen and seen[ident]!=item:
            return {'status':'unknown','reason':'同一课节存在冲突状态'}
        seen[ident]=item
    pending=[]
    for row in seen.values():
        if row['status']=='completed':continue
        if row['status'] not in ('absent','not_started','in_progress'):
            unknown.append(row['lesson_id']);continue
        pending.append(row)
    pending.sort(key=lambda x:(x['scheduled_date'],x['lesson_no'] or 0,x['lesson_id']))
    current=[x for x in pending if x['scheduled_date']>=week['start']]
    historical=[x for x in pending if x['scheduled_date']<week['start']]
    return {'status':'known_records' if week['coverage_complete'] and not unknown else 'incomplete',
            'as_of':snapshot['as_of'],'current_week_missing':current,'historical_missing':historical,
            'current_week_count':len(current),'historical_count':len(historical),'total_known_missing':len(pending),
            'ordered_lessons':pending,'unknown_lesson_ids':unknown,
            'duration_basis':None,'note':'只整理本次已查实记录；未读到的课不补造，时间戳间隔不是课程时长。'}


def review_body(body, snapshot=None):
    """No duration or fractional progress is supplied by this learning contract."""
    import re
    text='\n'.join(__import__('message_text').parts(body))
    if re.search(r'(?:[0-9零〇一二两三四五六七八九十百半几数多]+(?:来|多|余)?\s*(?:分钟|小时|刻钟)|半个小时|一会儿就|很快就.*(?:完|好))',text):
        return '本次资料没有可靠课程时长或剩余时长。请删除分钟、小时等时长推测，只按已核实课次商量时段。'
    if re.search(r'(?:一半|半节|半课|只剩一点|剩(?:下)?一点|快读完|快学完|快上完|读了.*[0-9]+%)',text):
        return '已开始不代表读到一半或只剩一点。本次没有进度比例；请改成已开始、接着读，不推测剩余量。'
    return None
