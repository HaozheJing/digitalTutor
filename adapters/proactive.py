"""Deterministic business signals, SQL-backed candidates and isolated delivery."""
import calendar
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import time
from contextlib import closing
from zoneinfo import ZoneInfo
import sql_tools as s

TZ = ZoneInfo('Asia/Shanghai')
# Explicit experiment contact policy; editable in proactive_settings, not model writable.
DEFAULT_POLICY = {'daily_limit': 2, 'min_gap_minutes': 60}
# name, title, signal, window start, priority, purpose
RULES = [
 ('celebrate-important-day','生日关怀','birthday','09:30',100,'表达生日祝福，结合已有阅读足迹，不附加学习要求'),
 ('care-calendar-day','节日与考试关怀','calendar','09:30',85,'根据家庭明确适用的节日或考试日期表达关怀'),
 ('celebrate-lesson-completion','当日完课鼓励','completed','19:00',30,'确认今日学习参与，给予具体而克制的鼓励'),
 ('highlight-course-start','首日完课纪念','first_day','19:00',80,'确认开课首日完成事实，支持家庭开始阅读'),
 ('celebrate-learning-streak','连续三天学习鼓励','streak3','19:00',50,'鼓励已经发生的连续学习，不宣称习惯已养成'),
 ('highlight-reading-milestone','连续21天里程碑','streak21','19:00',90,'纪念21个自然日的连续完课事实，不承诺勋章已发'),
 ('highlight-seven-day-completion','七日五天完课成就','seven','19:00',70,'在明确七日窗口中核实完成五个课程日'),
 ('highlight-second-week-attendance','第二周全勤','week2','19:00',75,'核对班期第二周全部应上课程完成事实'),
 ('highlight-book-completion','完书纪念','book','19:00',85,'根据全书逐课完成与书架点亮事实纪念完书'),
 ('highlight-first-perfect-answer','首次题组全对','perfect','19:00',80,'核对题目、作答及首次范围，保留提示和重试条件'),
 ('highlight-quiz-ranking','周答题榜第一名','rank','19:00',80,'仅依据已发布的真实正确率榜单确认第一名'),
 ('highlight-ranking-progress','榜单晋档','tier','19:00',65,'根据同口径前后榜单档位确认晋档'),
 ('highlight-expressive-answer','每周精彩表达','expression','19:00',60,'使用审核通过的孩子原话及课程语境记录表达'),
 ('plan-weekend-makeup','周五补读邀请','weekend','20:00',45,'列出本周未完成课节，给出可选择的补读建议'),
 ('close-week-reading-gap','本周最后一课提醒','last_gap','18:00',40,'仅在本周只剩今天一节时给出轻量收尾提示'),
 ('weekly-learning-review','周六本周总结','weekly','09:00',90,'核对本周完整事实，说明已完成及仍需处理的部分'),
 ('review-learning-progress','月末学习回顾','monthly','19:00',95,'依据整月覆盖完整的数据提供阶段回顾'),
 ('invite-reading-interaction','周互动','rotation','19:00',35,'结合本周真实所学，自主选择一个互动角度，发出低负担邀请'),
 ('prepare-growth-review-package','决策期成长回顾','decision','19:00',85,'根据已确认的服务节点整理可核实成长资料，不施加续费压力'),
]

def now():
    import test_mode
    return test_mode.clock()

def initialize():
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS proactive_facts(kind TEXT,scope TEXT,day TEXT,body TEXT,observed_at TEXT,valid_until TEXT,source TEXT,PRIMARY KEY(kind,scope,day));
        CREATE TABLE IF NOT EXISTS proactive_candidates(id TEXT PRIMARY KEY,day TEXT,rule TEXT,scope TEXT,body TEXT,status TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS proactive_receipts(dedupe_key TEXT PRIMARY KEY,candidate_id TEXT,delivery_key TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS proactive_pending(id TEXT PRIMARY KEY,candidate_ids TEXT,retry_at TEXT,expires_at TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS proactive_settings(key TEXT PRIMARY KEY,body TEXT);
        ''')
        c.execute('INSERT OR IGNORE INTO proactive_settings VALUES(?,?)',('contact',s.encoded(DEFAULT_POLICY)))

def rows(c,sql,args=()):
    return [dict(zip([x[0] for x in q.description],r)) for q in [c.execute(sql,args)] for r in q.fetchall()]

def facts(c,day):
    result={}
    for r in rows(c,'SELECT * FROM proactive_facts WHERE day<=? AND valid_until>=?',(day,day)):
        try:
            body=json.loads(r['body'])
            if not isinstance(body,dict):continue
            # Future observations cannot be used as evidence.
            if dt.datetime.fromisoformat(r['observed_at']).astimezone(TZ).date().isoformat()>day:continue
            result.setdefault(r['kind'],[]).append({**r,'body':body})
        except (ValueError,TypeError):continue
    return result

def integer(v):
    return type(v) is int and v>=0

def learning(c,day,fs):
    data={}
    for f in fs.get('learning_day',[]):
        b=f['body'];expected=b.get('expected');done=b.get('completed')
        if b.get('coverage_complete') is True and integer(expected) and integer(done) and done<=expected:
            data[f['day']]={'day':f['day'],'expected':expected,'completed':done,'lesson_titles':b.get('lesson_titles',[]),'completed_at':b.get('completed_at'),'missing_lesson_ids':b.get('missing_lesson_ids',[])}
    # Existing lesson binding alone does not prove historical curriculum coverage.
    actual=rows(c,'SELECT title,completed FROM lessons WHERE business_date=?',(day,))
    today=None
    if actual and all(x['completed'] in (0,1) for x in actual):
        today={'day':day,'expected':len(actual),'completed':sum(x['completed'] for x in actual),'lesson_titles':[x['title'] for x in actual]}
    if day in data:
        if actual and (today is None or (data[day]['expected'],data[day]['completed'])!=(today['expected'],today['completed'])):
            # Conflicting feeds cannot manufacture a completion or streak.
            data.pop(day);today=None
        else:today=data[day]
    return data,today

def interval(data,start,end):
    a=dt.date.fromisoformat(start);b=dt.date.fromisoformat(end)
    days=[(a+dt.timedelta(days=i)).isoformat() for i in range((b-a).days+1)]
    return [data[d] for d in days] if days and all(d in data for d in days) else None

def full(x):
    return x is not None and x['expected']>0 and x['completed']==x['expected']

def evaluate(c,day):
    """Return every rule's decision, retaining unknown separately from negative."""
    import test_mode
    if test_mode.state():
        return __import__('service_hours').limit_candidates(test_mode.decisions(c,day))
    date=dt.date.fromisoformat(day);fs=facts(c,day);data,today=learning(c,day,fs)
    monday=(date-dt.timedelta(days=date.weekday())).isoformat()
    sunday=(dt.date.fromisoformat(monday)+dt.timedelta(days=6)).isoformat()
    week=interval(data,monday,sunday)
    decisions=[]
    for name,title,kind,start,priority,purpose in RULES:
        matches=[];known=False
        def hit(scope,evidence):matches.append((str(scope),evidence))
        if kind=='birthday':
            people=rows(c,'SELECT birthday,name FROM family WHERE birthday IS NOT NULL')
            for person in people:
                try:
                    birthday=dt.date.fromisoformat(person['birthday']);known=True
                    if (birthday.month,birthday.day)==(date.month,date.day):hit(str(date.year),{'birthday':person['birthday'],'name':person['name']})
                except ValueError:pass
        elif kind=='calendar':
            known=bool(fs.get('calendar'))
            for f in fs.get('calendar',[]):
                b=f['body']
                if f['day']==day and b.get('applicable') is True and b.get('kind') in ['holiday','exam'] and b.get('title'):
                    hit(f['scope'],b)
        elif kind in ['completed','first_day']:
            known=today is not None
            if kind=='completed':
                enrollments=fs.get('enrollment',[]);known=known and bool(enrollments)
                for f in enrollments:
                    try:
                        start_date=dt.date.fromisoformat(f['body']['start_date'])
                        if today and today['expected']>1:
                            known=False  # Daily aggregates cannot prove per-lesson event times.
                        elif f['body'].get('active') is True and 0<=(date-start_date).days<7 and full(today):
                            hit(day,{'learning':today,'completed_at':today.get('completed_at')})
                    except (KeyError,ValueError):known=False
            if kind=='first_day':
                enrollments=fs.get('enrollment',[]);known=bool(enrollments)
                for f in enrollments:
                    first=f['body'].get('first_lesson',{})
                    if f['body'].get('start_date')==day and f['body'].get('active') is True:
                        if first.get('id') and first.get('completed') is True and first.get('completed_at'):
                            actual=rows(c,'SELECT completed,business_date FROM lessons WHERE id=?',(first['id'],))
                            if any(x['completed']!=1 or x['business_date']!=day for x in actual):
                                known=False
                            else:hit(f['scope'],{'enrollment':f['body'],'first_lesson':first,'completed_at':first['completed_at']})
                        else:known=False
        elif kind in ['streak3','streak21']:
            n=3 if kind=='streak3' else 21
            begin=date-dt.timedelta(days=n-1);block=interval(data,begin.isoformat(),day);known=block is not None
            if block and all(full(x) for x in block):
                # Stable streak anchor; prevent a new reward every day after threshold.
                anchor=begin
                while full(data.get((anchor-dt.timedelta(days=1)).isoformat())):anchor-=dt.timedelta(days=1)
                prev=data.get((anchor-dt.timedelta(days=1)).isoformat())
                enrolled=any(f['body'].get('start_date')==anchor.isoformat() and f['body'].get('active') is True for f in fs.get('enrollment',[]))
                if prev is not None or enrolled:hit(anchor.isoformat(),{'from':anchor.isoformat(),'through':day,'completed_natural_days':(date-anchor).days+1,'days':block,'completed_at':block[-1].get('completed_at')})
                else:known=False
        elif kind=='seven':
            known=bool(fs.get('seven_day_window'))
            for f in fs.get('seven_day_window',[]):
                b=f['body']
                try:
                    begin=dt.date.fromisoformat(b['start']);end=dt.date.fromisoformat(b['end'])
                    block=interval(data,b['start'],min(day,b['end']))
                    if (end-begin).days==6 and b['start']<=day<=b['end'] and block and sum(full(x) for x in block)>=5:hit(f['scope'],{'window':b,'days':block})
                except (KeyError,ValueError):known=False
        elif kind=='week2':
            known=bool(fs.get('enrollment'))
            for f in fs.get('enrollment',[]):
                try:
                    start_date=dt.date.fromisoformat(f['body']['start_date']);end=start_date+dt.timedelta(days=13)
                    block=interval(data,(start_date+dt.timedelta(days=7)).isoformat(),end.isoformat())
                    if date==end and block and any(x['expected'] for x in block) and all(x['expected']==x['completed'] for x in block):hit(f['scope'],{'from':block[0]['day'],'through':block[-1]['day'],'days':block})
                except (KeyError,ValueError):known=False
        elif kind in ['weekend','last_gap','weekly','monthly']:
            block=interval(data,monday,day) if kind in ('weekend','last_gap','weekly') else week;known=block is not None
            if kind=='monthly':
                block=interval(data,date.replace(day=1).isoformat(),day);known=block is not None
                if date.day==calendar.monthrange(date.year,date.month)[1] and block and sum(x['expected'] for x in block)>0:hit(day[:7],{'days':block})
            elif block and sum(x['expected'] for x in block)>0:
                missing=[x for x in block if x['expected']>x['completed']]
                if kind=='weekly' and date.weekday()==5:hit(monday,{'days':block})
                if kind=='weekend' and date.weekday()==4 and any(x['day']<=day for x in missing):hit(monday,{'days':block,'missing':[x for x in missing if x['day']<=day]})
                if kind=='last_gap' and date.weekday()==4 and len(missing)==1 and missing[0]['day']==day and missing[0]['expected']-missing[0]['completed']==1:hit(monday,{'days':block,'missing':missing})
        else:
            source_kind={'book':'book','perfect':'quiz','rank':'ranking','tier':'ranking','expression':'expression','rotation':'rotation','decision':'decision'}[kind]
            values=fs.get(source_kind,[]);known=bool(values)
            for f in values:
                b=f['body'];match=False;scope=f['scope']
                if kind=='book':
                    lessons=b.get('lessons',[])
                    match=(b.get('scope_confirmed') is True and b.get('coverage_complete') is True and b.get('shelf_lit') is True and b.get('completed_on')==day and bool(lessons) and all(isinstance(x,dict) and x.get('completed') is True for x in lessons) and bool(b.get('title')))
                elif kind=='perfect':
                    attempts=b.get('attempts',[])
                    valid=bool(attempts) and all(isinstance(x,dict) and integer(x.get('total')) and x['total']>0 and integer(x.get('correct')) and x['correct']<=x['total'] and isinstance(x.get('question_answers'),list) and len(x['question_answers'])==x['total'] and all(isinstance(q,dict) and q.get('question') and q.get('answer') and type(q.get('correct')) is bool for q in x['question_answers']) and sum(q['correct'] for q in x['question_answers'])==x['correct'] and x.get('day') for x in attempts)
                    perfect=[x for x in attempts if x['correct']==x['total']] if valid else []
                    match=b.get('history_complete') is True and bool(perfect) and min(x['day'] for x in perfect)==day
                elif kind=='rank':
                    match=b.get('final') is True and b.get('ranking_type')=='quiz_accuracy' and type(b.get('rank')) is int and b.get('rank')==1 and b.get('published_on')==day
                elif kind=='tier':
                    match=(b.get('final') is True and b.get('same_metric') is True and b.get('higher_tier_is_better') is True and integer(b.get('previous_tier')) and integer(b.get('current_tier')) and b['current_tier']>b['previous_tier'] and b.get('published_on')==day)
                elif kind=='expression':
                    match=b.get('teacher_selected') is True and b.get('transcript_verified') is True and bool(b.get('quote')) and bool(b.get('lesson_context')) and monday<=f['day']<=day
                    scope=monday
                elif kind=='rotation':
                    # Retain the fact kind and weekly dedupe scope; the model
                    # chooses an angle without a configured rotation form.
                    match=(date.weekday()==4 and b.get('week_start')==monday and bool(b.get('lesson_material')))
                    scope=monday
                elif kind=='decision':
                    match=b.get('confirmed') is True and b.get('review_on')==day and bool(b.get('learning_evidence'))
                if match:hit(scope,b)
        # One explicit negative/unknown decision makes missing interfaces visible.
        if not matches:
            decisions.append({'rule':name,'title':title,'status':'not_eligible' if known else 'unknown','reason':'条件未满足' if known else '缺少完整、有效的业务证据'})
        for scope,evidence in matches:
            earliest=day+'T'+start+':00+08:00'
            if kind in ('completed','streak3','first_day'):
                try:
                    completed_at=dt.datetime.fromisoformat(evidence['completed_at'])
                    if completed_at.tzinfo is None or completed_at.astimezone(TZ).date()!=date:raise ValueError()
                    earliest=(completed_at+dt.timedelta(minutes=0 if kind=='first_day' else 30)).astimezone(TZ).isoformat()
                except (KeyError,TypeError,ValueError):
                    decisions.append({'rule':name,'title':title,'status':'unknown','reason':'缺少可核验完课时间，无法计算事件触发时刻'})
                    continue
            ident='proactive:'+name+':'+scope+':'+day
            dedupe=name+':'+(monday if kind=='streak3' else scope)
            status='eligible'
            if c.execute('SELECT 1 FROM proactive_receipts WHERE dedupe_key=?',(dedupe,)).fetchone():status='already_delivered'
            decisions.append({'id':ident,'candidate_id':ident,'rule':name,'title':title,'scope':scope,'day':day,'status':status,'purpose':purpose,'skill':name,'priority':priority,'evidence':evidence,'dedupe_key':dedupe,'contacts_user':True,'delivery_format':'text','time_window':{'earliest':earliest,'latest':day+'T23:59:00+08:00' if kind in ('completed','streak3') else day+'T21:00:00+08:00'},'completion':'仅交付本次文字服务，不等于学情改善或图卡已制作'})
    # Avoid multiple praise messages about the same learning event.
    achievements={'highlight-course-start','celebrate-learning-streak','highlight-reading-milestone','highlight-seven-day-completion','highlight-second-week-attendance','highlight-book-completion'}
    eligible={x['rule'] for x in decisions if x['status'] in ('eligible','already_delivered')}
    for x in decisions:
        if x['status']!='eligible':continue
        if (x['rule']=='celebrate-lesson-completion' and eligible&achievements) or (x['rule']=='celebrate-learning-streak' and 'highlight-reading-milestone' in eligible):
            x['status']='merged_into_higher_priority';x['reason']='同一学习事实由更具体的成就覆盖'
    return __import__('service_hours').limit_candidates(decisions)

def refresh(day):
    s.validate_day(day)
    with closing(sqlite3.connect(s.DB)) as c,c:
        decisions=evaluate(c,day)
        # Historical candidates stay auditable, but stale ones cannot remain eligible.
        c.execute("UPDATE proactive_candidates SET status='stale' WHERE day=?",(day,))
        for x in decisions:
            if 'candidate_id' not in x:continue
            # Completion checks are re-evaluated as learning changes. Once a
            # receipt exists, retain the exact reminder facts used at delivery
            # so the shared per-lesson frequency ledger does not drift.
            if x.get('evidence',{}).get('event_id'):
                sent=c.execute('''SELECT pc.body FROM proactive_candidates pc JOIN proactive_receipts pr
                    ON pr.candidate_id=pc.id WHERE pc.id=?''',(x['id'],)).fetchone()
                if sent:
                    x['evidence']=json.loads(sent[0])['evidence']
            c.execute('INSERT OR REPLACE INTO proactive_candidates VALUES(?,?,?,?,?,?,?)',(x['id'],day,x['rule'],x['scope'],s.encoded(x),x['status'],s.now()))
            if x['status']=='eligible':
                c.execute('INSERT OR IGNORE INTO plan_candidates VALUES(?,?,?,?,?,?)',(x['id'],day,x['skill'],x['purpose'],x['time_window']['earliest'],'proactive_rules'))
        return decisions

def check(candidate_id,at=None):
    at=at or now()
    row=s.read('SELECT * FROM proactive_candidates WHERE id=?',(candidate_id,))
    if not row:return {'status':'unknown','reason':'候选不存在'}
    item=json.loads(row[0]['body'])
    if item['day']!=at.date().isoformat():return {'status':'expired'}
    current=next((x for x in refresh(item['day']) if x.get('id')==candidate_id),None)
    if not current:return {'status':'not_eligible','reason':'触发依据已改变'}
    if current['status']!='eligible':return current
    if at<dt.datetime.fromisoformat(current['time_window']['earliest']):return {**current,'status':'not_due'}
    if at>dt.datetime.fromisoformat(current['time_window']['latest']):return {**current,'status':'expired'}
    return current

def reminder_lessons(item):
    """Stable lesson IDs only; dates or titles cannot stand in for a lesson."""
    if item.get('rule') not in ('remind-historical-makeup','support-zero-completion',
            'prevent-lesson-skipping','rebuild-reading-rhythm',
            'plan-weekend-makeup','close-week-reading-gap'):
        return set()
    evidence=item.get('evidence',{})
    if not isinstance(evidence,dict):return None
    missing=evidence.get('overdue_lessons',evidence.get('missing',[]))
    if not isinstance(missing,list) or not missing:return None
    result=set()
    for row in missing:
        if not isinstance(row,dict):return None
        ids=[row['lesson_id']] if row.get('lesson_id') else row.get('missing_lesson_ids',[])
        if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or not x for x in ids):return None
        result.update(ids)
    return result


def contact_history(c,selected,at):
    history=rows(c,"SELECT action_key,created_at FROM deliveries WHERE action_key NOT LIKE 'reply:%' AND status<>'superseded'")
    if selected and all(x['rule']=='weekly-learning-review' for x in selected) and c.execute("SELECT 1 FROM sqlite_master WHERE name='weekly_material_deliveries'").fetchone():
        state=__import__('test_mode').state()
        keys={x[0] for x in c.execute('SELECT action_key FROM weekly_material_deliveries WHERE run_id=? AND uid=?',(state['run_id'],state['uid']))}
        history=[x for x in history if x['action_key'] not in keys or x['created_at'][:10]!=at.date().isoformat()]
    return history


def contact_spacing(c,selected,at):
    """Read-only spacing facts shared by planning, dispatch and final sending."""
    setting=c.execute("SELECT body FROM proactive_settings WHERE key='contact'").fetchone()
    minutes=json.loads(setting[0])['min_gap_minutes'] if setting else DEFAULT_POLICY['min_gap_minutes']
    exempt=bool(selected) and all(x['rule']=='invite-reading-interaction' for x in selected)
    history=contact_history(c,selected,at)
    last=max((dt.datetime.fromisoformat(x['created_at']).astimezone(TZ) for x in history),default=None)
    retry=last+dt.timedelta(minutes=minutes) if last and not exempt else None
    return {'exempt':exempt,'min_gap_minutes':minutes,'last_contact_at':last.isoformat() if last else None,
            'not_before':retry.isoformat() if retry else None,
            'reason_code':'interaction_gap_exempt' if exempt else 'contact_min_gap',
            'reason':('单独周互动豁免联系间隔' if exempt else
                      f'联系间隔{minutes}分钟：上次主动联系 {last.isoformat()}，最早可发送 {retry.isoformat()}' if retry else '没有需等待的主动联系记录')}


def contact_deferral_id(candidate_ids,retry):
    return 'contact-'+hashlib.sha256(s.encoded([sorted(candidate_ids),retry]).encode()).hexdigest()[:20]


def contact_gate(c,selected,at,manual_test=False):
    """Shared contact limits for ordinary service and existing highlight cards."""
    if not __import__('service_hours').is_open(__import__('service_hours').now(c)):return {'status':'skipped','reason':'每日22:00至次日06:30停止自动外发；主动事项不顺延。'}
    day=at.date().isoformat();candidate_ids=[x['id'] for x in selected]
    earliest=max(dt.datetime.fromisoformat(x['time_window']['earliest']) for x in selected)
    latest=min(dt.datetime.fromisoformat(x['time_window']['latest']) for x in selected)
    if at>latest:return {'status':'expired'}
    if c.execute('SELECT 1 FROM contact_constraints WHERE day=?',(day,)).fetchone():return {'status':'skipped','reason':'用户要求当天暂停主动联系'}
    gate=c.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone()
    gate=json.loads(gate[0]) if gate else {}
    waiting=c.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched')").fetchone()
    contact=json.loads(c.execute("SELECT body FROM proactive_settings WHERE key='contact'").fetchone()[0])
    history=contact_history(c,selected,at)
    spacing=contact_spacing(c,selected,at)
    # Only standalone S01/S02 are exempt; combining praise with other actions
    # must not turn optional outreach into an exempt message.
    exempt=all(x['rule']=='celebrate-lesson-completion' for x in selected)
    optional=0
    companion_files=__import__('weekly_materials').companion_file_keys(c,day)
    for item in history:
        if item['action_key'] in companion_files:continue
        if dt.datetime.fromisoformat(item['created_at']).astimezone(TZ).date()!=at.date():continue
        rules=rows(c,'SELECT pc.rule FROM proactive_receipts pr JOIN proactive_candidates pc ON pc.id=pr.candidate_id WHERE pr.delivery_key=?',(item['action_key'],))
        if ':S01:' in item['action_key'] or (rules and all(x['rule']=='celebrate-lesson-completion' for x in rules)):continue
        optional+=1
    if not exempt and optional>=contact['daily_limit']:return {'status':'skipped','reason':'当日非必需联系额度已用完'}
    retry=earliest
    causes=[]
    if earliest>at:causes.append({'reason_code':'candidate_window','reason':'候选窗口尚未开始，最早 '+earliest.isoformat()})
    if spacing['not_before']:
        retry=max(retry,dt.datetime.fromisoformat(spacing['not_before']))
        if dt.datetime.fromisoformat(spacing['not_before'])>at:causes.append(spacing)
    import test_mode
    testing=bool(test_mode.state())
    # Quiet-window timestamps always use wall time, never the 2025 business clock.
    busy=gate.get('active') or waiting or gate.get('until',0)>time.time()
    if busy and not (testing and manual_test):
        causes.append({'reason_code':'dialogue_busy','reason':'当前有待处理消息、正在进行的对话或对话静默窗口'})
        if testing:
            # Translate remaining real quiet time into business-clock duration.
            # Comparing a 2026 wall-clock instant to a 2025 fixture expires every task.
            retry=max(retry,at+dt.timedelta(seconds=max(1,gate.get('until',0)-time.time())))
        else:
            retry=max(retry,at+dt.timedelta(seconds=121),dt.datetime.fromtimestamp(gate.get('until',0),TZ))
    if retry>latest:return {'status':'skipped','reason':'；'.join(x['reason'] for x in causes)+'；可联系时间已超出任务有效期','causes':causes,'retry_at':retry.isoformat()}
    if retry>at:
        key='resume-'+hashlib.sha256('|'.join(sorted(candidate_ids)).encode()).hexdigest()[:20]
        c.execute('INSERT OR REPLACE INTO proactive_pending VALUES(?,?,?,?,?)',(key,s.encoded(candidate_ids),retry.isoformat(),latest.isoformat(),'pending'))
        return {'status':'deferred','retry_at':retry.isoformat(),'reason':'；'.join(x['reason'] for x in causes)+'；到点重新核查，不复用旧正文','causes':causes,'deferral_id':contact_deferral_id(candidate_ids,retry.isoformat())}
    return None

def deliver(candidate_ids,body,at=None,manual_test=False,generated_asset_id=None):
    """Re-evaluate evidence, pause, chat gate, frequency and dedupe in one transaction."""
    at=at or now();day=at.date().isoformat()
    if not body.strip() or not candidate_ids or len(candidate_ids)>3 or len(set(candidate_ids))!=len(candidate_ids):
        return {'status':'invalid','reason':'需要1至3个不同候选及非空正文'}
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        known=rows(c,'SELECT * FROM proactive_candidates WHERE id IN ('+','.join('?' for _ in candidate_ids)+')',candidate_ids)
        if len(known)!=len(candidate_ids):return {'status':'unknown','reason':'候选不存在'}
        if any(x['day']!=day for x in known):return {'status':'expired'}
        old=[json.loads(x['body']) for x in known]
        if any(x.get('delivery_owner')=='host_highlights' for x in old):
            return {'status':'blocked','reason':'已有高光由宿主直接发送接口原文和原图，不调用模型改写或重复交付'}
        from message_text import parts
        try:parts(body)
        except ValueError as error:return {'status':'needs_revision','reason':str(error)}
        receipts=[c.execute('SELECT delivery_key FROM proactive_receipts WHERE dedupe_key=?',(x['dedupe_key'],)).fetchone() for x in old]
        pending_key='resume-'+hashlib.sha256('|'.join(sorted(candidate_ids)).encode()).hexdigest()[:20]
        if all(receipts):
            c.execute("UPDATE proactive_pending SET status='done' WHERE id=?",(pending_key,))
            return {'status':'accepted','duplicate':True}
        if any(receipts):return {'status':'recheck','reason':'部分候选已交付，请重新组织未交付部分'}
        current={x.get('id'):x for x in evaluate(c,day)}
        selected=[current.get(x) for x in candidate_ids]
        if any(x and x['rule']=='support-zero-completion' and x['status']=='unknown' for x in selected):
            return {'status':'blocked','reason':'S05学情覆盖不完整，不能将数据缺失当作零完课'}
        if any(not x or x['status']!='eligible' for x in selected):return {'status':'skipped','reason':'触发条件已变化，请重新检查'}
        if any(x['rule']=='weekly-learning-review' for x in selected):
            gate=__import__('weekly_materials').s10_delivery_gate(c)
            if gate:return gate
        if any(x['rule']=='plan-weekend-makeup' for x in selected):
            reason=__import__('s09_service').review_body(body)
            if reason:return {'status':'needs_revision','reason':reason}
        from festival_service import image_for_delivery
        generated_url=image_for_delivery(c,candidate_ids,generated_asset_id)
        reminder_keys=[reminder_lessons(x) for x in selected]
        if any(x is None for x in reminder_keys):
            return {'status':'blocked','reason':'缺少稳定缺课课节ID，无法核验同课每周最多两次提醒'}
        reminder_keys=set().union(*reminder_keys)
        if reminder_keys:
            monday=(at.date()-dt.timedelta(days=at.weekday())).isoformat()
            historical=rows(c,'''SELECT pr.delivery_key,pc.body FROM proactive_receipts pr
              JOIN proactive_candidates pc ON pc.id=pr.candidate_id
              WHERE pc.day>=? AND pc.day<=?''',(monday,day))
            counts={key:set() for key in reminder_keys}
            for item in historical:
                previous=reminder_lessons(json.loads(item['body']))
                if previous is None:
                    return {'status':'blocked','reason':'历史缺课提醒缺少课节依据，无法确定共享次数'}
                for key in reminder_keys & previous:counts[key].add(item['delivery_key'])
            if any(len(v)>=2 for v in counts.values()):
                return {'status':'skipped','reason':'同一缺课本周已提醒两次，不因换动作重复触达'}
        gate=contact_gate(c,selected,at,manual_test)
        if gate:return gate
        testing=bool(__import__('test_mode').state())
        key='active:'+hashlib.sha256('|'.join(sorted(x['dedupe_key'] for x in selected)).encode()).hexdigest()[:24]
        key=__import__('message_hooks').draft_key(c,key)
        __import__('service_workflow').bind_delivery(c,key)
        c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,body,'queued' if testing else 'mock_sent',at.isoformat(),'test_wechat_pending' if testing else 'mock_channel'))
        if testing:
            import wechat_test
            wechat_test.queue_text(c,key,body)
            wechat_test.queue_images(c,key,([generated_url] if generated_url else [])+[x['evidence']['img_url'] for x in selected if x.get('evidence',{}).get('img_url')])
        for x in selected:
            c.execute('INSERT INTO proactive_receipts VALUES(?,?,?,?)',(x['dedupe_key'],x['id'],key,at.isoformat()))
            c.execute("UPDATE proactive_candidates SET status='delivered' WHERE id=?",(x['id'],))
        c.execute("UPDATE proactive_pending SET status='done' WHERE id=?",(pending_key,))
        return {'status':'accepted','action_key':key,'candidate_ids':candidate_ids,'body':body,'delivery_format':'text_and_generated_image' if generated_url else 'text'}

def pending():
    current=now()
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute("UPDATE proactive_pending SET status='expired' WHERE status IN ('pending','scheduled') AND expires_at<?",(current.isoformat(),))
        return rows(c,"SELECT * FROM proactive_pending WHERE status='pending' AND retry_at<=?",(current.isoformat(),))

def task(candidate_ids):
    import business
    return ('你是这个家庭的阅读辅导老师。先通过service_skill读取service-policy、tutor-main与proactive-service，用service_catalog定位具体动作，处理以下已安排的主动事项。'
            '先刷新daily_context，并逐个调用check_proactive核验；只处理仍eligible的事项。需要联系时加载相应动作Skill和reply-style，'
            '组织一条适合家庭的消息，通过deliver_proactive交付；条件变化或过期可以不发送，不为说明跳过而发消息。'
            '\n候选引用：'+s.encoded([business.token(x) for x in candidate_ids]))

if __name__=='__main__':
    initialize()
    command=sys.argv[1]
    if command=='pending':result=pending()
    elif command=='scheduled':
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE proactive_pending SET status='scheduled' WHERE id=?",(sys.argv[2],))
        result={'ok':True}
    elif command=='recover':
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("UPDATE proactive_pending SET status='pending' WHERE status='scheduled'")
        result={'ok':True}
    elif command=='task':result={'prompt':task(json.loads(sys.argv[2]))}
    else:result=refresh(sys.argv[2])
    print(s.encoded(result))
