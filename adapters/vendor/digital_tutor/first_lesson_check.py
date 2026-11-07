"""Opt-in isolated H01: a durable 19:00 check, followed by guarded delivery."""
import json
from contextlib import nullcontext
from .domain import require,timestamp,BusinessError
from .storage import digest

KIND='lab_first_lesson_check'

def record(app,family):
 return next(iter(app.store.list(KIND,family)),None)

def schedule(app,family,course_date,batch_id):
 require(app.store.family(family)['mock']==1,'REAL_CHANNEL_DISABLED','首课定时检查仅用于隔离实验')
 old=record(app,family)
 if old:return old
 with (nullcontext() if app.store.connection.in_transaction else app.store.transaction()):
  return app.store.put(KIND,'first-lesson:'+course_date,family,{'course_date':course_date,'batch_id':batch_id,'scheduled_at':course_date+'T19:00:00+08:00','status':'scheduled','checked_at':None,'result':None,'policy':'h01-evening-v1','created_at':app.clock.iso()})

def observation(app,family,check):
 rows=[dict(r) for r in app.store.connection.execute('SELECT * FROM learning_days WHERE batch_id=? AND uid=? AND day=1',(check['batch_id'],app.store.family(family)['uid']))]
 imported=app.store.connection.execute('SELECT metadata FROM imports WHERE batch_id=?',(check['batch_id'],)).fetchone()
 meta=json.loads(imported[0]) if imported else {}
 row=rows[0] if len(rows)==1 else None
 evidence={'row':row,'coverage_through':meta.get('coverage_through'),'source_sha256':meta.get('source_sha256'),'completion_time_known':meta.get('completion_time_known',False),'test_override':meta.get('test_override')}
 if not row or row.get('source_learning_date')!=check['course_date'] or not row.get('evidence_locator'):
  return 'unknown','缺少可核实的首日首课记录',evidence
 try:
  coverage=timestamp(meta.get('coverage_through'));valid=coverage<=app.clock.now() and coverage.astimezone(app.clock.now().tzinfo).date().isoformat()==check['course_date']
 except (ValueError,TypeError,AttributeError,BusinessError):valid=False
 if not valid:return 'unknown','学情覆盖时间缺失、过期或晚于当前时间',evidence
 if row.get('completion_status')=='completed':return 'completed','已核实首日首课完成',evidence
 if row.get('completion_status')=='incomplete':return 'incomplete','首课尚未完成，不生成、不发送高光',evidence
 return 'unknown','首课完成状态不明确，不生成、不发送高光',evidence

def run_due(app,family):
 with app.store.transaction():
  check=record(app,family)
  if not check or check['status']!='scheduled' or app.clock.now()<timestamp(check['scheduled_at']):return check
  if app.clock.iso()[:10]!=check['course_date']:
   result,reason,evidence='unknown','已错过首日检查，不以次日数据冒充首日完成',{}
  else:result,reason,evidence=observation(app,family,check)
  return app.store.put(KIND,check['id'],family,{**check,'status':'checked','checked_at':app.clock.iso(),'result':result,'reason':reason,'evidence':evidence,'evidence_sha256':digest(evidence)},check['version'])

def delivery_allowed(app,family):
 check=record(app,family)
 if not check:return not policy(app,family)
 return (check['status']=='checked' and check['result']=='completed' and app.clock.now()>=timestamp(check['scheduled_at']) and app.clock.iso()[:10]==check['course_date'] and observation(app,family,check)[0]=='completed')

def require_verified(app,family,event):
 if event.get('service_action_id')!='H01' or not (record(app,family) or policy(app,family)):return
 require(delivery_allowed(app,family),'FIRST_LESSON_NOT_VERIFIED','首日高光须在19点检查确认首课完成后制作和发送；未完成、数据不明或已跨日均不发送。')

def validate_plan(app,family,event):
 if event.get('service_action_id')!='H01' or not (record(app,family) or policy(app,family)):return
 require_verified(app,family,event)
 step=event.get('next_action') or {};check=record(app,family)
 require(not step.get('execute_at') or timestamp(step['execute_at'])>=timestamp(check['checked_at']),'FIRST_LESSON_CHECK_TIME','首日高光执行时间不能早于本次19点核验时间，请按核验后的当前时间安排。')


def policy(app,family):
 return next(iter(app.store.list('lab_first_lesson_policy',family)),None)

def configure(app,family,course_date,batch_id):
 require(app.store.family(family)['mock']==1,'REAL_CHANNEL_DISABLED','仅用于隔离实验')
 with app.store.transaction():
  return app.store.put('lab_first_lesson_policy','first-lesson-policy',family,{'course_date':course_date,'batch_id':batch_id,'check_at':course_date+'T19:00:00+08:00','planning_required':True})

def commit_schedule(app,family,cmd,plan,binding):
 p=policy(app,family)
 if not p or p['course_date']!=cmd.business_date:
  require(not cmd.first_lesson_check_at,'FIRST_LESSON_POLICY_MISSING','没有当前首日核验规则，不得预置检查')
  return plan
 require(cmd.first_lesson_check_at and timestamp(cmd.first_lesson_check_at)==timestamp(p['check_at']),'FIRST_LESSON_PLAN_REQUIRED','首日工作计划须明确提交first_lesson_check_at为当天19:00；仅安排检查，不提前创建高光。')
 check=schedule(app,family,p['course_date'],p['batch_id'])
 check=app.store.put(KIND,check['id'],family,{**check,'planned_by':'planner_daily','plan_id':plan['id'],'planning_request_id':cmd.request_id,'planning_run_id':binding.get('run_id')},check['version'])
 return app.store.put('plan',plan['id'],family,{**plan,'scheduled_checks':[{'kind':KIND,'id':check['id'],'at':check['scheduled_at']}]},plan['version'])
