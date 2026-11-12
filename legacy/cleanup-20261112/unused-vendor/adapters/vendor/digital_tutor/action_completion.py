"""Versioned service completion checks; no generation, sending, or vendor overrides.

Host configuration supplies timing/source applicability. Models may add cited
semantic opinions, never learning facts, receipts, policy, or a complete flag.
"""
from datetime import timedelta
import json
from pathlib import Path
from typing import Literal
from pydantic import Field
from .domain import Strict, ID, Positive, require, timestamp, BusinessError
from .storage import digest

RULES=json.loads((Path(__file__).resolve().parents[3]/'agent/config/action-completion-rules.json').read_text(encoding='utf-8'))
ROLES={'service','admin'}
RESPONSE_SLOTS={'I01':['specific_response','parent_summary'],'I02':['feedback','optional_parent_invitation'],'I03':['specific_praise','improvement_tip'],'I04':['course_response']}


class Check(Strict):
    request_id: ID
    event_id: ID
    expected_version: Positive


class Citation(Strict):
    kind: Literal['message','operation']
    id: ID
    version: Positive
    excerpt: str = Field(min_length=2,max_length=4000)


class Assessment(Check):
    aspect: Literal['child_expression','delivery_content']
    slots: list[ID] = Field(default_factory=list)
    supports: bool
    subject: Literal['child','parent_relay_child','parent','teacher','uncertain']
    reason: str = Field(min_length=5,max_length=2000)
    citations: list[Citation] = Field(min_length=1,max_length=12)


SPECS={
 'service.check_and_advance':(ROLES,Check,'核验已绑定动作的原课节/孩子表达/交付，保存依据和唯一下次执行时间。不发送，不接受完成结论。使用最新事项version及新request_id。'),
 'service.record_assessment':({'service','admin'},Assessment,'为已绑定动作补充有原文引用的局部语义判断，不是完成声明。child_expression只用本轮入站原文；delivery_content按规则slots逐项引用本事项实际操作。程序核来源和范围，语义支持仍是模型判断。'),
}


def lookup(app,family,kind,rid):
    return next((r for r in app.store.list(kind,family) if r['id']==rid),None)


def policy(app,family):
    return lookup(app,family,'action_policy','action-policy:'+family) or {}


def configure_policy(app,token,*,review_seconds,stale_after_seconds,retry_seconds,max_failures,owner,classification):
    """Trusted host only; deliberately absent from model tools."""
    require(all(type(x) is int and x>0 for x in [review_seconds,stale_after_seconds,retry_seconds,max_failures]),'INVALID_CONFIG','复核、时效、重试和失败阈值须显式正整数')
    require(bool(owner) and classification in {'simulation','confirmed'},'INVALID_CONFIG','须显式责任人及配置性质')
    with app.store.transaction():
        family,_=app.auth(token,{'admin'});rid='action-policy:'+family;old=lookup(app,family,'action_policy',rid)
        return app.store.put('action_policy',rid,family,{'review_seconds':review_seconds,'stale_after_seconds':stale_after_seconds,'retry_seconds':retry_seconds,'max_failures':max_failures,'owner':owner,'classification':classification},old['version'] if old else None)


def bind_contract(app,family,event,action_id,*,cycle_end_at=None,slot_parts=None,source_batch_id=None):
    """Called by committed planning or trusted bootstrap, inside its transaction.

    Mandatory slots come from maintained rules, never from the caller. Missing
    delivery mappings remain visible gaps. An event cannot change its action.
    """
    require(action_id in RULES,'ACTION_RULE_UNKNOWN','未知服务动作')
    rule=RULES[action_id];kind=event['completion']['kind']
    require((rule['rule']=='L')==(kind=='goal'),'ACTION_RULE_SCOPE','学习动作必须原课节目标，互动/交付动作必须原交付合同')
    if cycle_end_at:timestamp(cycle_end_at)
    parts=slot_parts or {x:x for x in rule['required_slots']}
    require(set(parts)==set(rule['required_slots']),'ACTION_RULE_SCOPE','必需交付槽位不可增删')
    rid='action-contract:'+event['id'];old=lookup(app,family,'action_contract',rid)
    for part in event.get('delivery_parts',[]):
        if part['part_id'] not in parts.values():parts['original:'+part['part_id']]=part['part_id']
    value={'event_id':event['id'],'action_id':action_id,'rule_version':rule['rule_version'],'requirement_revision':event['requirement_revision'],'cycle_end_at':cycle_end_at,'opened_at':app.clock.iso(),'slot_parts':parts,'source_batch_id':source_batch_id,'invitation_closed':False,'cycle_result':None,'failures':0}
    if action_id=='S10' and set(parts.values())=={'weekly_package'}:
        value.update(asset_slots={'next_schedule':'image'},require_ready_media=True)
    require(old is None,'ACTION_ALREADY_BOUND','沿用已绑定动作，不能覆盖或重开')
    app.store.put('action_contract',rid,family,value)
    updated=app.store.put('event',event['id'],family,{**event,'service_action_id':action_id,'next_execute_at':app.clock.iso()},event['version'])
    return updated


def _contract(app,family,event):
    c=lookup(app,family,'action_contract','action-contract:'+event['id'])
    require(c is not None,'ACTION_NOT_CONFIGURED','事项未绑定动作核验合同；由规划或宿主绑定原责任，不另建目标')
    require(c['requirement_revision']==event['requirement_revision'],'ACTION_CONTRACT_STALE','完成要求变化，需受控重新绑定')
    require(c['rule_version']==RULES[c['action_id']]['rule_version'],'ACTION_RULE_STALE','动作规则版本变化，需迁移合同')
    return c


def _body(record):
    body=record.get('body') or record.get('request',{}).get('body') or record.get('payload',{}).get('body') or ''
    media=record.get('media') or {}
    # V2 card text is a delivered, readable companion to the image, not an OCR
    # claim or production prompt. Failed/legacy drafts are not new evidence.
    if media.get('presentation_version')==2 and media.get('kind')=='image' and media.get('status')=='ready':
        body+='\n'+media.get('content','')
    return body


def _cited(app,family,cite):
    r=app.store.get(cite['kind'],cite['id'],family)
    require(r['version']==cite['version'],'STALE_EVENT','原文或操作版本已经变化')
    require(timestamp(r['occurred_at'])<=app.clock.now(),'SOURCE_TIME_INVALID','未来原文不能用于当前判断')
    require(cite['excerpt'].strip() and cite['excerpt'] in _body(r),'SOURCE_EXCERPT_INVALID','引用须为现存原文连续片段')
    return r


def require_response_expression(app,family,event,part_id):
    """Do not send this interaction's feedback before its own child expression."""
    aid=event.get('service_action_id')
    if aid not in RESPONSE_SLOTS:return
    c=_contract(app,family,event)
    if part_id not in {c['slot_parts'][s] for s in RESPONSE_SLOTS[aid]}:return
    invite_slot={'I01':'invitation','I02':'questions','I03':'demonstration','I04':'life_question'}[aid]
    invites=[o for o in app.store.list('operation',family) if o.get('event_id')==event['id'] and o.get('requirement_revision')==event['requirement_revision'] and o.get('part_id')==c['slot_parts'][invite_slot] and o.get('stage') in {'sent','delivered','read'}]
    earliest=min((timestamp(o['occurred_at']) for o in invites),default=None)
    reviewed={}
    for j in sorted(assessments(app,family,event,c),key=lambda j:j['sequence']):
        if j['aspect']=='child_expression':
            for cite in j['citations']:reviewed[cite['id']]=j
    valid=[]
    for mid,j in reviewed.items():
        m=app.store.get('message',mid,family)
        if j['supports'] and earliest and not m.get('historical_reference') and timestamp(m['occurred_at'])>=max(earliest,timestamp(c['opened_at'])):
            valid.append(mid)
    require(bool(valid),'INTERACTION_RESPONSE_NOT_READY','本轮邀请后尚无已核实的相关孩子表达。先等待；不要用旧答题提前回应或总结。')


def record_assessment(app,token,args):
    cmd=Assessment.model_validate(args)
    def apply(family,binding):
        event=app.event(family,cmd.event_id);require(event['version']==cmd.expected_version,'STALE_EVENT','事项版本变化')
        c=_contract(app,family,event);rule=RULES[c['action_id']]
        records=[_cited(app,family,x.model_dump()) for x in cmd.citations]
        if cmd.aspect=='child_expression':
            require(rule['rule']=='I' and not cmd.slots,'ASSESSMENT_SCOPE','仅互动动作判断孩子表达，不指定交付槽位')
            require(all(x.kind=='message' and r['direction']=='inbound' and timestamp(r['occurred_at'])>=timestamp(c['opened_at']) for x,r in zip(cmd.citations,records)),'ASSESSMENT_SCOPE','表达须为本轮到达的原入站')
            if cmd.supports:require(cmd.subject in {'child','parent_relay_child'},'ASSESSMENT_SUBJECT','家长自己的意见不能冒充孩子表达')
        else:
            require(bool(cmd.slots) and set(cmd.slots)<=set(c['slot_parts']),'ASSESSMENT_SCOPE','只能判断本动作的必需交付槽位')
            require(cmd.subject=='teacher','ASSESSMENT_SUBJECT','交付内容评价主体须为老师')
            require(all(x.kind=='operation' for x in cmd.citations),'ASSESSMENT_SCOPE','交付引用须为实际操作')
            if all(r.get('event_id')==event['id'] and r.get('requirement_revision')==event['requirement_revision'] for r in records):
                shared = c['action_id'] in {'S02','S04'} and set(cmd.slots)<=set(rule['required_slots']) and all(r.get('part_id') in set(c['slot_parts'].values()) for r in records)
                require(shared or all(any(r.get('part_id')==c['slot_parts'][slot] for r in records) for slot in cmd.slots),'ASSESSMENT_SCOPE','每个槽位须引用对应实际操作；鼓励可逐项核验同一条实际消息')
            else:
                # A late inbound reply uses the original native reply capability,
                # never a new proactive event send. Link it without rewriting ops.
                require(rule['rule']=='I' and set(cmd.slots)<=set(RESPONSE_SLOTS[c['action_id']]),'ASSESSMENT_SCOPE','普通回复仅接续本主题欠回应')
                expressions={x['id'] for j in assessments(app,family,event,c) if j['aspect']=='child_expression' and j['supports'] for x in j['citations']}
                for r in records:
                    replies={x['message_id'] for x in r.get('reply_to',[])}&expressions
                    require(not r.get('event_id') and replies and r.get('recipient')==app.store.family(family)['recipient'] and all(timestamp(r['occurred_at'])>=timestamp(app.store.get('message',mid,family)['occurred_at']) for mid in replies),'ASSESSMENT_SCOPE','回复须实际回应已核验孩子表达的同一消息及家庭')
                    for slot in cmd.slots:
                        lid='action-reply:'+digest([event['id'],slot,r['id']]);oldlink=lookup(app,family,'action_reply_link',lid)
                        app.store.put('action_reply_link',lid,family,{'event_id':event['id'],'requirement_revision':event['requirement_revision'],'slot':slot,'operation_id':r['id'],'operation_version':r['version']},oldlink['version'] if oldlink else None)
        identity=[family,event['id'],cmd.aspect,sorted(cmd.slots)]
        if cmd.aspect=='child_expression':identity.append(sorted(x.id for x in cmd.citations))
        key='action-assessment:'+digest(identity)
        old=lookup(app,family,'action_assessment',key)
        saved=app.store.put('action_assessment',key,family,{**cmd.model_dump(exclude={'request_id','expected_version'}),'requirement_revision':event['requirement_revision'],'rule_version':c['rule_version'],'at':app.clock.iso(),'sequence':app.store.family(family)['version'],'classification':'model_semantic_judgment_not_deterministic_fact'},old['version'] if old else None)
        return saved
    return app.write(token,{'service','admin'},'action_assessment',cmd.model_dump(),apply)


def assessments(app,family,event,c):
    result=[]
    for r in app.store.list('action_assessment',family):
        if r['event_id']!=event['id'] or r['requirement_revision']!=event['requirement_revision'] or r['rule_version']!=c['rule_version']:continue
        try:
            for cite in r['citations']:_cited(app,family,cite)
        except BusinessError:continue
        result.append(r)
    return result


def learning_facts(app,family,event,c,p):
    """One fixed, uid/batch-scoped query; no model SQL or supplied completion."""
    targets=event['completion']['lesson_refs'];identity=event.get('source_identity') or {}
    bindings=[b for b in app.store.list('course_binding',family) if b.get('course_id')==identity.get('course_id') and b.get('course_version')==identity.get('course_version') and b.get('course_id') and (not c['source_batch_id'] or b['batch_id']==c['source_batch_id'])]
    result=[]
    uid=app.store.family(family)['uid']
    for lesson in targets:
        observations=[]
        for b in bindings:
            days=[m['day'] for m in b.get('lesson_map') or [] if m['lesson_ref']==lesson]
            if len(days)!=1:continue
            row=app.store.connection.execute('SELECT d.*, i.metadata FROM learning_days d JOIN imports i ON i.batch_id=d.batch_id WHERE d.batch_id=? AND d.uid=? AND d.day=?',(b['batch_id'],uid,days[0])).fetchone()
            if row is None:continue
            row=dict(row);meta=json.loads(row.pop('metadata'));through=meta.get('coverage_through')
            valid=(through and b.get('coverage_through') and meta.get('source_timezone')=='Asia/Shanghai' and b.get('source_timezone')=='Asia/Shanghai' and b.get('source_kind')=='trusted_host_confirmation' and p.get('stale_after_seconds'))
            reason='coverage_or_policy_unknown';status='unknown'
            if valid:
                covered=min(timestamp(through),timestamp(b['coverage_through']))
                age=(app.clock.now()-covered).total_seconds()
                if 0<=age<=p['stale_after_seconds'] and not meta.get('conflicts') and not meta.get('superseded_by_batch'):
                    status=row['completion_status'];reason='current_bound_source'
                else:reason='stale_future_conflicting_or_superseded_source'
            # Learning date is not completion occurrence time. Only explicit source field can establish timeliness.
            completed_at=(meta.get('completed_at_by_uid_day') or {}).get(uid+':'+str(days[0]))
            observations.append({'batch_id':b['batch_id'],'binding_id':b['id'],'binding_version':b['version'],'day':days[0],'source_row':row['evidence_locator'],'source_sha256':meta.get('source_sha256'),'source_coverage':through,'status':status,'reason':reason,'completed_at':completed_at})
        statuses={x['status'] for x in observations}
        status=next(iter(statuses)) if len(statuses)==1 and 'unknown' not in statuses else 'unknown'
        result.append({'lesson_ref':lesson,'status':status,'observations':observations})
    return result


def evaluate(app,family,event,c):
    rule=RULES[c['action_id']];p=policy(app,family);judgments=assessments(app,family,event,c)
    rows=learning_facts(app,family,event,c,p) if rule['rule']=='L' else []
    completed=[r['lesson_ref'] for r in rows if r['status']=='completed'];remaining=[r['lesson_ref'] for r in rows if r['status']=='incomplete'];unknown=[r['lesson_ref'] for r in rows if r['status'] not in {'completed','incomplete'}]
    operations=[o for o in app.store.list('operation',family) if o.get('event_id')==event['id'] and o.get('requirement_revision')==event['requirement_revision'] and o.get('recipient')==app.store.family(family)['recipient']]
    for link in app.store.list('action_reply_link',family):
        if link['event_id']!=event['id'] or link['requirement_revision']!=event['requirement_revision']:continue
        op=lookup(app,family,'operation',link['operation_id'])
        if op and op['version']==link['operation_version'] and op.get('recipient')==app.store.family(family)['recipient']:
            operations.append({**op,'part_id':c['slot_parts'][link['slot']]})
    missing=[];delivered=[];needs=[]
    slots=dict(c['slot_parts'])
    # Original contract parts can never be erased by a less demanding action ID.
    for part in event['completion']['required_part_ids']:
        if part not in slots.values():slots['original:'+part]=part
    for slot,part in slots.items():
        ops=[o for o in operations if o.get('part_id')==part and (o['stage']=='delivered' or (event['completion']['required_stage']=='sent' and o['stage']=='sent'))]
        shared_fallback = c['action_id'] in {'S02','S04'} and slot in rule['required_slots']
        if shared_fallback:
            ops=[o for o in operations if o.get('part_id') in set(c['slot_parts'].values()) and (o['stage']=='delivered' or (event['completion']['required_stage']=='sent' and o['stage']=='sent'))]
        if not ops:missing.append({'slot':slot,'reason':'delivery_unconfirmed'});continue
        relevant=sorted([j for j in judgments if j['aspect']=='delivery_content' and slot in j['slots'] and any(x['id'] in {o['id'] for o in ops} for x in j['citations'])],key=lambda j:j['sequence'])
        quality=bool(relevant and relevant[-1]['supports'])
        if shared_fallback:
            latest_by_operation={x['id']:j for j in relevant for x in j['citations'] if x['id'] in {o['id'] for o in ops}}
            quality=any(j['supports'] for j in latest_by_operation.values())
            unreviewed=[o for o in ops if o['id'] not in latest_by_operation]
            if not quality and not unreviewed:
                missing.append({'slot':slot,'reason':'delivery_unconfirmed'});continue
            if not quality:ops=unreviewed
        if not quality:
            needs.append({'slot':slot,'reason':'needs_content_judgment','operation_ids':[o['id'] for o in ops]});continue
        asset_kind={**rule['asset_slots'],**c.get('asset_slots',{})}.get(slot)
        if asset_kind:
            assets=[a for a in app.store.list('action_artifact',family) if a.get('event_id')==event['id'] and a.get('requirement_revision')==event['requirement_revision'] and a.get('slot')==slot and a.get('kind')==asset_kind and a.get('host_verified') is True and a.get('operation_id') in {o['id'] for o in ops} and a.get('sha256')]
            if asset_kind=='badge':assets=[a for a in assets if a.get('issuance_receipt')]
            if asset_kind=='video':assets=[a for a in assets if type(a.get('duration_seconds')) in {int,float} and 0<a['duration_seconds']<=60 and a.get('consent_ref')]
            from .media_generation import content_artifact_valid
            if asset_kind in {'audio','image'}:
                assets=[a for a in assets if (content_artifact_valid(app,family,a,ops) if a.get('representation')=='content_only' else asset_kind=='image' or type(a.get('duration_seconds')) in {int,float} and 0<a['duration_seconds']<=60)]
            if c.get('require_ready_media'):assets=[a for a in assets if a.get('representation')!='content_only']
            if not assets:missing.append({'slot':slot,'reason':'actual_artifact_or_issuance_missing'});continue
        delivered.append(slot)
    reviewed_by_message={}
    for j in sorted(judgments,key=lambda j:j['sequence']):
        if j['aspect']=='child_expression':
            for x in j['citations']:reviewed_by_message[x['id']]=j
    expression=list({j['id']:j for j in reviewed_by_message.values() if j['supports']}.values())
    earliest=None
    if rule['rule']=='I':
        invite_slot={'I01':'invitation','I02':'questions','I03':'demonstration','I04':'life_question'}[c['action_id']]
        invitations=[o for o in operations if o.get('part_id')==c['slot_parts'][invite_slot] and o['stage'] in {'sent','delivered'}]
        earliest=min((timestamp(o['occurred_at']) for o in invitations),default=None)
        expression=[j for j in expression if earliest and all(timestamp(app.store.get('message',x['id'],family)['occurred_at'])>=earliest for x in j['citations'])]
        if expression:
            expressed_at=min(timestamp(app.store.get('message',x['id'],family)['occurred_at']) for j in expression for x in j['citations'])
            response_slots=RESPONSE_SLOTS[c['action_id']]
            for slot in response_slots:
                if slot in delivered and not any(o.get('part_id')==c['slot_parts'][slot] and timestamp(o['occurred_at'])>=expressed_at for o in operations):
                    delivered.remove(slot);missing.append({'slot':slot,'reason':'response_must_follow_expression'})
    messages=[m for m in app.store.list('message',family) if m['direction']=='inbound' and earliest is not None and timestamp(m['occurred_at'])>=max(earliest,timestamp(c['opened_at']))]
    if rule['rule']=='I' and not expression:
        reviewed={x['id'] for j in judgments if j['aspect']=='child_expression' for x in j['citations']}
        unreviewed=[m for m in messages if m['id'] not in reviewed]
        if unreviewed:needs.append({'reason':'needs_child_expression_judgment','messages':[{'id':m['id'],'version':m['version'],'body':m['body']} for m in unreviewed]})
        else:remaining.append('child_expression')
    fact='unknown' if unknown or needs else 'incomplete' if remaining or missing else 'completed'
    if rule['rule']=='L' and not rows:fact='unknown';unknown=['lesson_scope_missing']
    times=[]
    if rule['rule']=='L':times=[o['completed_at'] for r in rows for o in r['observations'] if r['status']=='completed']
    elif rule['rule']=='I':times=[app.store.get('message',x['id'],family)['occurred_at'] for j in expression for x in j['citations']]
    boundary=c.get('cycle_end_at') or event.get('due_at')
    timeliness='not_applicable' if not boundary else 'unknown'
    if boundary and fact=='completed' and times and all(times):timeliness='on_time' if max(timestamp(t) for t in times)<=timestamp(boundary) else 'late'
    return {'fact':fact,'completed':completed+delivered,'remaining':remaining+missing,'unknown':unknown,'needs_judgment':needs,'timeliness':timeliness,'source_rows':rows,'semantic_judgments':[{'id':j['id'],'version':j['version'],'classification':j['classification']} for j in judgments],'message_scope_checked':not any(x.get('reason')=='needs_child_expression_judgment' for x in needs),'expression_received':bool(expression),'checked_at':app.clock.iso(),'event_version':event['version'],'requirement_revision':event['requirement_revision'],'rule_version':rule['rule_version']}


def input_signature(app,family,event):
    kinds=('message','control','operation','course_binding','action_assessment','action_artifact','action_policy','action_handoff','action_trigger_policy','action_reply_link','lab_contact_policy')
    records={k:[(r['id'],r['version']) for r in app.store.list(k,family)] for k in kinds}
    c=lookup(app,family,'action_contract','action-contract:'+event['id']) or {}
    records['scope']=['task-semantics-v22',event['requirement_revision'],event['completion'],{k:c.get(k) for k in ['action_id','requirement_revision','rule_version','slot_parts','cycle_end_at','source_batch_id']}]
    uid=app.store.family(family)['uid']
    records['learning']=[tuple(r) for r in app.store.connection.execute('SELECT d.batch_id,d.day,d.completion_status,d.evidence_locator,i.metadata FROM learning_days d JOIN imports i ON i.batch_id=d.batch_id WHERE d.uid=? ORDER BY d.batch_id,d.day',(uid,))]
    return digest(records)


def configuration_gaps(app,family,event,c,result):
    rule=RULES[c['action_id']];p=policy(app,family)
    configuration=[]
    if not p and result['fact']!='completed':configuration.append('review_staleness_retry_policy')
    if rule['cycle_required'] and not c.get('cycle_end_at'):configuration.append('cycle_end_at')
    # These unresolved counting/trigger policies cannot be invented by a model.
    # They do not invalidate a completed learning goal, but block new contact.
    if c['action_id'] in {'S04','S05','H04','H06'} and not (c['action_id']=='S05' and result['fact']=='completed'):
        confirmed=[x for x in app.store.list('action_trigger_policy',family) if x.get('event_id')==event['id'] and x.get('requirement_revision')==event['requirement_revision'] and x.get('host_verified') is True and x.get('source_ref') and x.get('definition')]
        if not confirmed:configuration.append('trigger_counting_policy')
    return configuration


def contact_decision(app,family,event,c,result,configuration,exclude_operation=None):
    rule=RULES[c['action_id']];now=app.clock.now()
    contact='eligible_for_service_decision';reason='requires_current_plan_and_send_checks'
    from .send_policy import action_paused,ContactPolicy,daily_contact_available
    paused=any(action_paused(app,family,a,event['id']) for a in ['remind_makeup','deliver_feedback','clarify_existing'])
    from .delivery_retry import charged as is_charged
    charged=[o for o in app.store.list('operation',family) if o['id']!=exclude_operation and o.get('event_id') and is_charged(app,o)]
    quota=getattr(app,'contact_policy',ContactPolicy())
    limited=not daily_contact_available(app,family,sum(o.get('business_date',o.get('occurred_at','')[:10])==now.date().isoformat() for o in charged))
    week=(now.date()-timedelta(days=now.weekday())).isoformat()
    weekly=[o for o in charged if o.get('business_date',o.get('occurred_at','')[:10])>=week]
    if rule['rule']=='L':
        limited=limited or any(sum(lesson in o.get('lesson_refs',[]) for o in weekly)>=quota.reminders_per_lesson_week for lesson in event['completion']['lesson_refs'])
    if rule['rule']=='I':
        interaction_ids={e['id'] for e in app.store.list('event',family) if e.get('service_action_id') in {'I01','I02','I03','I04'}}
        contacted={o.get('event_id') for o in weekly if o.get('event_id') in interaction_ids}
        limited=limited or (event['id'] not in contacted and len(contacted)>=2)
    if c['action_id'] in {'S01','S06','S08'}:
        limited=limited or any(o.get('event_id')==event['id'] for o in charged)
    if paused:contact='prohibited';reason='pause_active'
    elif limited:contact='prohibited';reason='contact_limit'
    elif event.get('due_at') and timestamp(event['due_at'])>now and not deadline_only(app,family,event):contact='prohibited';reason='existing_agreement_wait'
    if result['fact']=='unknown' or configuration:contact='prohibited';reason='facts_or_configuration_unverified'
    if any(h.get('event_id')==event['id'] and h.get('accepted') is True and h.get('owner') and h.get('next_execute_at') for h in app.store.list('action_handoff',family)):
        contact='prohibited';reason='human_accepted'
    return contact,reason,paused


def deadline_only(app,family,event):
    """A host-confirmed delivery deadline is not a not-before appointment."""
    identity=event.get('source_identity') or {}
    return identity.get('kind')=='periodic_text_delivery' and any(
        s.get('contract_id')==identity.get('contract_id') and s.get('due_semantics')=='deadline'
        for s in app.store.list('candidate_source',family))


def _advance(app,family,event,origin_run_id=None):
    c=_contract(app,family,event);rule=RULES[c['action_id']];p=policy(app,family);now=app.clock.now();result=evaluate(app,family,event,c)
    configuration=configuration_gaps(app,family,event,c,result)
    result['configuration_gaps']=configuration
    boundary=c.get('cycle_end_at');expired=bool(boundary and timestamp(boundary)<=now)
    contact,reason,paused=contact_decision(app,family,event,c,result,configuration)
    disposition='continue';closed=c.get('invitation_closed',False)
    if expired:closed=True
    if expired and c.get('cycle_result') is None:c['cycle_result']='met_on_time' if result['fact']=='completed' and result['timeliness']=='on_time' else 'not_met' if result['fact']=='incomplete' else 'pending_verification'
    if expired and result['fact']=='completed' and result['timeliness']=='on_time':c['cycle_result']='met_on_time' # late arrival with explicit timely occurrence
    if event['status']=='cancelled':closed=True;disposition='ended';contact='prohibited';reason='cancelled'
    elif closed:disposition='ended';contact='prohibited';reason='cycle_ended'
    if result['fact']=='completed' and not configuration and event['status']!='cancelled':
        disposition='completed';contact='not_needed' if not paused else 'prohibited';reason='completed' if not paused else 'pause_active'
        event.update(status='done',next_action=None,waiting=None,remaining_work=[])
        if not event['completion_history'] or event['completion_history'][-1].get('result',{}).get('fact')!='completed':
            event['completion_history'].append({'at':app.clock.iso(),'source':'service.check_and_advance','result':result})
    else:
        if event['status']=='done':
            # A corrected source invalidates the old conclusion, not its history.
            # Retain responsibility without granting a fresh proactive invitation.
            event.update(status='waiting',next_action=None);closed=True
            contact='prohibited';reason='completion_evidence_changed'
        if result['fact']=='unknown':disposition='waiting' if not closed else 'ended'
        event['remaining_work']=result['remaining']+result['unknown']+[x.get('slot') or x['reason'] for x in result['needs_judgment']]
    # Ending an invitation is not success and cannot erase owed responses.
    residual=bool(event['status']!='cancelled' and (result['fact']!='completed' or configuration)) or bool(rule['rule']=='I' and result['expression_received'] and (result['remaining'] or result['needs_judgment']))
    handoffs=[h for h in app.store.list('action_handoff',family) if h.get('event_id')==event['id'] and h.get('accepted') is True and h.get('owner') and h.get('next_execute_at')]
    owner=handoffs[-1]['owner'] if handoffs else p.get('owner','service-operations')
    if handoffs:contact='prohibited';reason='human_accepted';disposition='human_handling'
    c['failures']=c.get('failures',0)+1 if result['fact']=='unknown' else 0
    artifact_gaps=[x['slot'] for x in result['remaining'] if isinstance(x,dict) and x.get('reason')=='actual_artifact_or_issuance_missing']
    if c['failures']>=p.get('max_failures',10**9) or configuration or artifact_gaps:
        disposition='human_handling' if residual and not closed else disposition
        key='action-attention:'+event['id'];old=lookup(app,family,'action_attention',key)
        app.store.put('action_attention',key,family,{'event_id':event['id'],'owner':owner,'reason':'configuration_missing' if configuration else 'artifact_missing' if artifact_gaps else 'repeated_unknown','gaps':configuration+artifact_gaps,'next_review_event':'source_or_configuration_update','status':'open'},old['version'] if old else None)
    else:
        attention=lookup(app,family,'action_attention','action-attention:'+event['id'])
        if attention and attention.get('status')=='open':app.store.put('action_attention',attention['id'],family,{**attention,'status':'resolved'},attention['version'])
    future=[]
    if residual and not handoffs and p:
        future.append(now+timedelta(seconds=p['retry_seconds'] if result['fact']=='unknown' else p['review_seconds']))
        for t in [event.get('due_at'),boundary]:
            if t and timestamp(t)>now:future.append(timestamp(t))
    next_at=min(future).isoformat() if future else None
    result.update(disposition=disposition,contact={'decision':contact,'reason':reason,'grants_send_capability':False,'next_action':'核验不赋予新权限，也不拒绝当前已授权步骤；eligible时可按当前步骤调用发送工具，由发送门禁最终核验。' if contact=='eligible_for_service_decision' else '按decision和reason继续等待或停止联系。'},next_execute_at=next_at,residual_responsibility=residual,owner=owner,resume_event='source_message_receipt_or_configuration_update' if residual else None,cycle_result=c.get('cycle_result'),invitation_closed=closed,origin_run_id=origin_run_id)
    c.update(invitation_closed=closed,last_input_signature=input_signature(app,family,event),last_checked_at=app.clock.iso())
    app.store.put('action_contract',c['id'],family,c,c['version'])
    # Re-evaluation is not a send grant. Only restore an existing delivery step;
    # the scheduler and send checks still enforce plan, time, pause and receipts.
    delivery_due=any(isinstance(x,dict) and x.get('reason')=='delivery_unconfirmed' for x in result['remaining'])
    if (event['status']=='waiting' and contact=='eligible_for_service_decision' and not closed
            and disposition=='continue' and result['fact']=='incomplete' and delivery_due
            and (event.get('next_action') or {}).get('action_kind')=='deliver_feedback'):
        event.update(status='pending',waiting=None)
    event.update(next_execute_at=next_at,action_check=result)
    if event.get('waiting'):
        explanations={'contact_limit':'当前有效联系额度不足，待额度恢复后重新核验。','pause_active':'当前处于暂停联系范围，待约定解除后重新核验。','existing_agreement_wait':'尚未到约定联系时间。','facts_or_configuration_unverified':'等待核实资料或已发送内容；当前不追加发送。','requires_current_plan_and_send_checks':'联系限制已解除，等待核实剩余责任及可执行步骤。','cycle_ended':'本轮邀请已结束，仅保留应答责任。','human_accepted':'已由老师承接处理。'}
        waiting={**event['waiting'],'review_at':None}
        if reason in explanations:waiting.update(reason=explanations[reason],resume_condition='按当前核验结果继续；收到相关新信息或到下次复查时间时重新核验。')
        event['waiting']=waiting
    saved=app.store.put('event',event['id'],family,event,event['version']);result['saved_event_version']=saved['version']
    return result


def check_and_advance(app,token,args):
    cmd=Check.model_validate(args)
    def apply(family,binding):
        event=app.event(family,cmd.event_id)
        require(event['version']==cmd.expected_version,'STALE_EVENT','事项已变化，请重新读取')
        require(not event['update_pending'],'REPORTS_PENDING','先消费原进度报告，不能覆盖并发更新')
        if binding.get('event_id'):require(binding['event_id']==event['id'],'ACTION_SCOPE','只能推进当前任务绑定事项')
        return _advance(app,family,event,binding.get('run_id'))
    return app.write(token,ROLES,'check_and_advance',cmd.model_dump(),apply)


def sweep(app):
    """Internal checks independent of today's contact plan; no new model loop."""
    checked=0
    with app.store.transaction() as db:
        for row in db.execute('SELECT id FROM families').fetchall():
            family=row['id']
            if any(j['status']=='leased' for j in app.store.list('job',family)):continue
            for event in app.store.list('event',family):
                c=lookup(app,family,'action_contract','action-contract:'+event['id'])
                if not c:continue
                current=app.event(family,event['id'])
                if current['update_pending']:continue
                changed=c.get('last_input_signature')!=input_signature(app,family,current)
                due=current.get('next_execute_at') and timestamp(current['next_execute_at'])<=app.clock.now()
                stranded=not current.get('next_execute_at') and current['status'] not in {'done','cancelled'} and not current.get('action_check')
                if changed or due or stranded:
                    try:
                        result=_advance(app,family,current)
                    except BusinessError as exc:
                        key='action-attention:'+current['id'];old=lookup(app,family,'action_attention',key)
                        app.store.put('action_attention',key,family,{'event_id':current['id'],'owner':policy(app,family).get('owner','service-operations'),'reason':exc.code,'status':'open','next_review_event':'contract_or_configuration_update'},old['version'] if old else None)
                        app.store.put('action_contract',c['id'],family,{**c,'last_input_signature':input_signature(app,family,current)},c['version'])
                        app.store.put('event',current['id'],family,{**current,'next_execute_at':None,'action_check':{'fact':'unknown','disposition':'human_handling','contact':{'decision':'prohibited','reason':exc.code,'send_permission':False},'owner':policy(app,family).get('owner','service-operations'),'resume_event':'contract_or_configuration_update'}},current['version'])
                        app.store.bump(family);checked+=1
                        continue
                    if result['needs_judgment']:
                        # Original queue/Worker/Hermes, internal-only and once per input.
                        from .jobs import JobQueue
                        signature=digest(['task-semantics-v22',current['id'],current['requirement_revision'],result['needs_judgment'],[(o['id'],o['version']) for o in app.store.list('operation',family) if any(o['id'] in n.get('operation_ids',[]) for n in result['needs_judgment'])]])
                        JobQueue(app).enqueue(family,'action-review:'+digest([current['id'],signature]),{'kind':'review','event_id':current['id'],'event_version':result['saved_event_version'],'contact_allowed':False,'action_completion_review':True,'action_input_signature':signature})
                    from .action_focus import missing_invitation_step
                    if missing_invitation_step(app,family,current) and not result['invitation_closed']:
                        from .jobs import JobQueue
                        signature=digest([current['id'],current['requirement_revision'],current.get('next_action')])
                        JobQueue(app).enqueue(family,'action-plan-review:'+signature,{'kind':'review','event_id':current['id'],'event_version':result['saved_event_version'],'contact_allowed':False,'action_plan_review':True})
                    app.store.bump(family);checked+=1
    return checked


def validate_before_send(app,family,event,exclude_operation=None):
    c=lookup(app,family,'action_contract','action-contract:'+event['id'])
    if not c:return
    c=_contract(app,family,event)
    expired=c.get('cycle_end_at') and timestamp(c['cycle_end_at'])<=app.clock.now()
    require(not c.get('invitation_closed') and not expired,'ACTION_CYCLE_ENDED','本轮主动邀请已结束，欠回应走原消息回复责任')
    result=evaluate(app,family,event,c)
    require(result['fact']!='completed','ACTION_ALREADY_COMPLETED','目标或交付已完成，禁止旧任务重复发送')
    require(result['fact']!='unknown','ACTION_SOURCE_UNKNOWN','学情或语义事实未核实，不能据旧核验结果发送')
    contact,reason,_=contact_decision(app,family,event,c,result,configuration_gaps(app,family,event,c,result),exclude_operation)
    require(contact!='prohibited','ACTION_CONTACT_BLOCKED',reason)


HANDLERS={'service.check_and_advance':check_and_advance,'service.record_assessment':record_assessment}
