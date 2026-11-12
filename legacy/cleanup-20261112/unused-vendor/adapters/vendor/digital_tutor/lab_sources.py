"""Experiment-only learning candidates and bounded intraday source admission."""
import json
from .domain import require,timestamp
from .storage import digest

def derive_goal(app,family,s,common):
    from .candidates import makeup_identity,action_goal
    bindings=[b for b in app.store.list('course_binding',family) if b.get('batch_id')==s['batch_id'] and b.get('source_kind')=='trusted_host_confirmation']
    imported=app.store.connection.execute('SELECT metadata FROM imports WHERE batch_id=?',(s['batch_id'],)).fetchone()
    if len(bindings)!=1 or not imported:return {**common,'gaps':['confirmed_course_binding_missing']}
    b=bindings[0];meta=json.loads(imported[0]);coverage=[meta.get('coverage_through'),b.get('coverage_through')]
    if not all(x and timestamp(s['required_coverage_at'])<=timestamp(x)<=app.clock.now() for x in coverage):return {**common,'gaps':['coverage_incomplete_or_future']}
    rows=[dict(r) for r in app.store.connection.execute('SELECT * FROM learning_days WHERE batch_id=? AND uid=? ORDER BY day',(s['batch_id'],app.store.family(family)['uid'])) if r['day'] in s['lesson_days']]
    if len(rows)!=len(s['lesson_days']) or any(r['completion_status'] not in {'completed','incomplete'} for r in rows):return {**common,'gaps':['learning_status_unknown']}
    if all(r['completion_status']=='completed' for r in rows):return {**common,'gaps':['learning_goal_already_completed']}
    mapping={m['day']:m['lesson_ref'] for m in b.get('lesson_map') or []}
    if not set(s['lesson_days'])<=mapping.keys():return {**common,'gaps':['confirmed_lesson_mapping_missing']}
    lessons=sorted(mapping[d] for d in s['lesson_days']);identity=makeup_identity(family,b['course_id'],b['course_version'],lessons)
    return {**common,'status':'ready','goal_key':'makeup:'+digest(identity),'source_identity':identity,
      'goal_text':action_goal(s),'completion':{'kind':'goal','lesson_refs':lessons,'required_part_ids':[],'required_stage':'sent'},
      'source_refs':common['source_refs']+[{'kind':'course_binding','id':b['id'],'version':b['version']}],
      'coverage':{'source_through':coverage[0],'binding_through':coverage[1]},'source_rows':rows}

def signature(app,c,s):
    return digest([c.get('source_fingerprint'),app.clock.iso()[:10],app.clock.now()>=timestamp(s['due_at'])])

def mark_considered(app,family,c,s):
    key='source-considered:'+c['id'];old=next((r for r in app.store.list('lab_source_considered',family) if r['id']==key),None)
    app.store.put('lab_source_considered',key,family,{'candidate_id':c['id'],'signature':signature(app,c,s)},old['version'] if old else None)

def pending_candidates(app,family):
    from .candidates import refresh_candidates
    sources={s['id']:s for s in app.store.list('candidate_source',family)}
    considered={r['candidate_id']:r['signature'] for r in app.store.list('lab_source_considered',family)}
    return [c for c in refresh_candidates(app,family) if c['status']=='ready' and not c.get('existing_event_id') and sources[c['source_id']].get('rule_version','').startswith('lab-all-actions-') and considered.get(c['id'])!=signature(app,c,sources[c['source_id']])]

def enqueue_refresh(app,family,queue):
    from .scheduling import baseline_committed
    if not app.strict_runtime or not baseline_committed(app,family,app.clock.iso()[:10]) or not app.service_start<=app.clock.now().hour<app.service_end:return 0
    if any(j['kind']=='daily' and j.get('planning_reason')=='source_refresh' and j['status'] in {'ready','leased','waiting'} for j in app.store.list('job',family)):return 0
    rows=pending_candidates(app,family)
    if not rows:return 0
    key=digest([(c['id'],c['source_fingerprint']) for c in rows]+[app.clock.iso()[:13]])
    return queue.enqueue(family,'source-refresh:'+key,{'kind':'daily','planning_reason':'source_refresh','business_date':app.clock.iso()[:10]})

def commit_refresh(app,family,binding,cmd):
    from .candidates import validate_daily_candidates,save_candidate_decisions
    from .planning_records import create_events,save_plan,validate_step,current_plan
    require(app.store.family(family)['mock']==1,'REAL_CHANNEL_DISABLED','仅实验来源可追加计划')
    require(not cmd.event_updates and not cmd.pending_decisions,'PLANNING_SCOPE','来源追加不能改旧事项或处理家庭请求')
    require(cmd.business_date==app.clock.iso()[:10] and app.service_start<=app.clock.now().hour<app.service_end,'WINDOW_CLOSED','只追加当前服务窗口的计划')
    plan=current_plan(app,family,cmd.business_date)
    require(plan and plan.get('revision',1)==binding['plan_revision'],'STALE_PLAN','原计划已改变')
    require(not any(r['resolution'] is None for r in app.store.list('report',family)),'REPORTS_PENDING','先消费原报告')
    validate_daily_candidates(app,family,binding,cmd)
    mapping=create_events(app,family,cmd.new_events);refs=[mapping.get(x,x) for x in cmd.plan_refs]
    require(len(refs)==len(set(refs)) and set(refs)==set(mapping.values()),'PLANNING_SCOPE','来源追加只纳入本次新事项，不能删除或重排旧计划')
    for eid in refs:validate_step(app.event(family,eid))
    saved=save_plan(app,family,cmd,refs,'source_refresh');save_candidate_decisions(app,family,cmd,saved['id'],mapping)
    key=binding.get('job_id') or cmd.request_id;old=next((r for r in app.store.list('lab_refresh_receipt',family) if r['id']==key),None)
    app.store.put('lab_refresh_receipt',key,family,{'request_id':cmd.request_id,'plan_id':saved['id']},old['version'] if old else None)
    return {'plan':saved,'event_ids':mapping,'planning_reason':'source_refresh'}
