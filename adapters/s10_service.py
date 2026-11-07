"""Saturday weekly report: participation bands, never ability/SABCD inference."""
import datetime as dt
RULE='weekly-learning-review'

def review(snapshot):
    week=snapshot['weekly_learning']['current_week']
    lessons=week['lessons'];scheduled=week['scheduled'];completed=week['completed']
    known=week['coverage_complete'] and scheduled>0 and len(lessons)==scheduled and all(x['status'] in ('completed','absent','in_progress','not_started') for x in lessons)
    band='unknown' if not known else 'complete' if completed==scheduled else 'zero' if completed==0 else 'partial'
    return {'as_of':snapshot['as_of'],'band':band,'scheduled':scheduled,'completed':completed,
            'missing_lessons':[x for x in lessons if x['status']!='completed'],'lessons':lessons,
            'basis':'本周应学课程截至当前的实际完课；不是能力分层或正式SABCD',
            'highlight_preview':{'status':'not_confirmed','note':'未提供已确认待发送的高光预告，不承诺未来高光或奖励'},
            'appointment':{'status':'invitation_only','confirmed':False,'note':'暂无真人排班/预约确认接口；可邀请家长提供方便时间，回复后转人工确认'},
            'entry':{'status':'not_provided','note':'PDF为本周复习资料，不是课程入口或实时学情链接'}}

def decision(c,state,snapshot):
    day=state['as_of'][:10]
    if dt.date.fromisoformat(day).weekday()!=5:return None
    value=review(snapshot);week=snapshot['weekly_learning']['current_week'];key=state['run_id']+':S10:'+week['start'];ident='proactive:'+state['run_id']+':S10:'+day
    status='eligible' if value['band']!='unknown' else 'unknown'
    if c.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?',(key,)).fetchone():status='already_delivered'
    return {'id':ident,'candidate_id':ident,'day':day,'rule':RULE,'skill':RULE,'title':'周六本周总结','scope':week['start'],'status':status,'reason':'每周总结适用于各完课分支；未知学情不当作零完课','dedupe_key':key,'purpose':'本周总结与对应PDF','evidence':{'weekly_review':value,'overdue_lessons':value['missing_lessons']},'priority':90,'contacts_user':True,'delivery_format':'text','time_window':{'earliest':day+'T09:00:00+08:00','latest':day+'T23:59:00+08:00'}}

def ensure_fixed(c,state):
    """Host creates the ordinary versioned S10 task once in automatic mode."""
    if not state.get('auto_highlights') or dt.date.fromisoformat(state['as_of'][:10]).weekday()!=5:return None
    import test_mode as tm,service_workflow as wf
    item=decision(c,state,tm.snapshot())
    if not item or item['status']=='already_delivered' or state['as_of']>item['time_window']['latest']:return None
    if c.execute('SELECT 1 FROM service_task_versions WHERE uid=? AND dedupe_key=?',(state['uid'],item['dedupe_key'])).fetchone():return None
    if wf.planning_paused(c,state,RULE,item['time_window']['earliest']):return None
    contract=next((x for x in wf.catalog(c,state) if x['skill']==RULE),None)
    if not contract:return None
    return wf.add(c,state,item,contract,item['time_window']['earliest'],'用户授权每周六全量总结，由宿主固定创建',{'kind':'host_fixed_weekly'},item['dedupe_key'])


def appointment_context(day):
    """Only a sent zero-completion S10 supplies a pending appointment invitation."""
    import json,sqlite3
    from contextlib import closing
    import sql_tools as s,test_mode as tm
    state=tm.state()
    if not state:return None
    with closing(sqlite3.connect(s.DB)) as c:
        if not all(c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(x,)).fetchone() for x in ['proactive_receipts','proactive_candidates','deliveries']):return None
        rows=c.execute('SELECT pc.body,d.action_key,d.created_at FROM proactive_receipts pr JOIN proactive_candidates pc ON pc.id=pr.candidate_id JOIN deliveries d ON d.action_key=pr.delivery_key WHERE pc.rule=? AND d.status=? AND pc.day<=? ORDER BY d.created_at DESC',(RULE,'sent',day)).fetchall()
        for body,key,at in rows:
            item=json.loads(body)
            if not item.get('dedupe_key','').startswith(state['run_id']+':'):continue
            if (dt.date.fromisoformat(day)-dt.date.fromisoformat(at[:10])).days>7:continue
            if item.get('evidence',{}).get('weekly_review',{}).get('band')!='zero':continue
            return {'status':'invited_awaiting_family_time','delivery_key':key,'sent_at':at,'confirmed':False,
                    'reply_guidance':'本周零完课总结已邀请家长提供沟通时间。如果本轮原话接受沟通或给出方便时段，用request_human_confirmation关联当前event_id，原样记录家长时段及待真人确认原因；不要先回复预约成功。其他问题正常处理，不凭关键词自动认定预约。'}
    return None
