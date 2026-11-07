"""Pinned weekly PDFs; host-owned Saturday S10 delivery for every completion band."""
import datetime as dt
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from contextlib import closing
import sql_tools as s
import test_mode as tm

REGISTRY = Path(__file__).with_name('weekly_materials.json')
# User assigns weekly PDFs to Saturday S10; never to Friday S09.
ENABLED = True

def initialize(c):
    c.execute('CREATE TABLE IF NOT EXISTS weekly_material_deliveries(run_id TEXT,uid TEXT,scope TEXT,asset_id TEXT,action_key TEXT UNIQUE,payload TEXT,PRIMARY KEY(run_id,uid,scope))')
    c.execute('CREATE TABLE IF NOT EXISTS weekly_material_authorizations(operation_id TEXT PRIMARY KEY,action_key TEXT,authorized_at TEXT)')

def companion_file_keys(c, day):
    """The PDF and its same-day S10 text are one logical optional contact."""
    state=tm.state()
    if not state or not c.execute("SELECT 1 FROM sqlite_master WHERE name='weekly_material_deliveries'").fetchone():return set()
    return {x[0] for x in c.execute("""SELECT wm.action_key FROM weekly_material_deliveries wm
        JOIN deliveries pdf ON pdf.action_key=wm.action_key
        WHERE wm.run_id=? AND wm.uid=? AND substr(pdf.created_at,1,10)=?
        AND EXISTS(SELECT 1 FROM proactive_receipts pr
          JOIN proactive_candidates pc ON pc.id=pr.candidate_id
          JOIN deliveries summary ON summary.action_key=pr.delivery_key
          WHERE pc.rule='weekly-learning-review' AND pr.dedupe_key LIKE ?
            AND substr(summary.created_at,1,10)=? AND summary.status<>'superseded')""",
        (state['run_id'],state['uid'],day,state['run_id']+':S10:%',day))}


def select(state=None):
    import course_service
    state=state or tm.state()
    if not state:return {'status':'unavailable','reason':'当前家庭未绑定'}
    day=state['as_of'][:10];binding=course_service.binding(state['uid'])
    if not binding.get('class_type') or not binding.get('cohort'):return {'status':'unknown','reason':'班型或期次缺失'}
    schedule_day=(dt.date.fromisoformat(day)-dt.timedelta(days=1)).isoformat() if dt.date.fromisoformat(day).weekday()==5 else day
    schedule=course_service.course_schedule(schedule_day,binding['class_type'],binding['cohort'])
    if schedule.get('status')!='scheduled':return {'status':'not_applicable','reason':'今日无可匹配课程'}
    registry=json.loads(REGISTRY.read_text())
    rows=[x for x in registry['assets'] if x['class_type']==binding['class_type'] and x['lesson_start']<=schedule['lesson_no']<=x['lesson_end']]
    if len(rows)!=1:return {'status':'unknown','reason':'没有唯一匹配的当周文件，不能借用其他周'}
    asset=rows[0];verify_asset(asset)
    monday=(dt.date.fromisoformat(day)-dt.timedelta(days=dt.date.fromisoformat(day).weekday())).isoformat()
    scope=':'.join([str(binding['class_type']),str(binding['cohort']),monday,asset['id']])
    return {'status':'ready','asset':asset,'scope':scope,'business_day':day,'schedule':schedule}

def verify_asset(asset):
    path=s.R/asset['path'];base=(s.R/'assets/weekly-review').resolve()
    if path.is_symlink() or path.resolve()!=path.absolute() or not path.resolve().is_relative_to(base) or not path.is_file():raise ValueError('固定文件路径无效')
    raw=path.read_bytes()
    if len(raw)!=asset['size'] or hashlib.sha256(raw).hexdigest()!=asset['sha256'] or not raw.startswith(b'%PDF-'):raise ValueError('固定PDF版本或哈希变化')
    return raw

def due(state):
    at=dt.datetime.fromisoformat(state['as_of']).astimezone(tm.TZ)
    return at.weekday()==5 and '09:00:00'<=at.strftime('%H:%M:%S')<'22:00:00'

def allowed(c,state):
    import service_workflow as wf
    if not __import__('service_hours').is_open(__import__('service_hours').now(c)):return '每日22:00至次日06:30停止自动外发'
    if c.execute('SELECT 1 FROM contact_constraints WHERE day=?',(state['as_of'][:10],)).fetchone():return '家庭要求当天暂停联系'
    if wf.enabled(state) and wf.constraint_blocks(c,state,'weekly-learning-review',at=state['as_of']):return '家庭暂停主动服务'
    gate=c.execute("SELECT payload FROM task_state WHERE id='chat-gate'").fetchone();gate=json.loads(gate[0]) if gate else {}
    if gate.get('active') or gate.get('until',0)>time.time() or c.execute("SELECT 1 FROM events WHERE kind='passive' AND status IN ('pending','dispatched')").fetchone():return '等待当前家庭交流结束'
    import message_hooks
    message_hooks.tables(c)
    if time.time()<message_hooks.deadline(c):return '等待新消息静默'
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='operator_alerts'").fetchone() and c.execute("SELECT 1 FROM operator_alerts WHERE run_id=? AND status IN ('pending','queued','accepted','unknown','partial','failed')",(state['run_id'],)).fetchone():return '等待已有人工事项处理'
    return None

def queue():
    if not ENABLED:return {'status':'disabled','reason':'固定文件发送已撤销'}
    import experiment_runtime as er
    state=tm.state()
    if not state or not due(state):return {'status':'not_due'}
    # Only explicit simulation or verified manual acceptance may publish pinned files.
    manifest=er.manifest()
    if not manifest and not __import__('manual_workbench').verify() and not __import__('family_lab_bridge').verify():return {'status':'blocked','reason':'固定文件尚未启用真实渠道'}
    material=select(state)
    if material['status']!='ready':return material
    asset=material['asset'];key='weekly-material:'+hashlib.sha256((state['run_id']+':'+state['uid']+':'+material['scope']).encode()).hexdigest()[:32]
    url=('https://experiment.invalid/weekly-material/'+manifest['experiment_run_id']+'/'+asset['sha256']+'.pdf') if manifest else __import__('manual_media').publish(asset,verify_asset(asset))
    payload={'name':asset['name'],'url':url,'size':asset['size']}
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('BEGIN IMMEDIATE');initialize(c)
        current=json.loads(c.execute("SELECT value FROM metadata WHERE key='test_mode'").fetchone()[0])
        if current!=state:raise ValueError('业务时钟或家庭已变化')
        prior=c.execute('SELECT w.action_key,d.status FROM weekly_material_deliveries w JOIN deliveries d ON d.action_key=w.action_key WHERE w.run_id=? AND w.uid=? AND w.scope=?',(state['run_id'],state['uid'],material['scope'])).fetchone()
        if prior:return {'status':prior[1],'action_key':prior[0],'duplicate':True}
        reason=allowed(c,state)
        if reason:return {'status':'deferred','reason':reason}
        __import__('message_hooks').register_external(c,key,'weekly_material_original')
        c.execute('INSERT INTO weekly_material_deliveries VALUES(?,?,?,?,?,?)',(state['run_id'],state['uid'],material['scope'],asset['id'],key,s.encoded(payload)))
        c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',(key,asset['name'],'queued',state['as_of'],'test_wechat_pending'))
    return {'status':'queued','action_key':key,'asset_id':asset['id'],'duplicate':False}

def s10_delivery_gate(c):
    """A new S10 contract cannot claim complete service without its exact PDF."""
    import service_workflow as wf
    if not wf.enabled() or not wf.planning_contract('weekly-learning-review').get('required_weekly_material'):
        return None
    state=tm.state();material=select(state)
    if material['status']!='ready':return {'status':'blocked','reason':'S10本周PDF不可用：'+material.get('reason','缺少匹配文件')}
    if not (__import__('experiment_runtime').manifest() or __import__('manual_workbench').verify() or __import__('family_lab_bridge').verify()):return {'status':'blocked','reason':'S10真实PDF发布通道未配置，不能声明已附文件'}
    initialize(c)
    row=c.execute('SELECT d.status FROM weekly_material_deliveries w JOIN deliveries d ON d.action_key=w.action_key WHERE w.run_id=? AND w.uid=? AND w.scope=?',(state['run_id'],state['uid'],material['scope'])).fetchone()
    status=row[0] if row else 'not_sent'
    if status=='sent':return None
    if status in ('failed','unknown','partial'):return {'status':'blocked','reason':'S10本周PDF交付未确认（'+status+'），不重发文字或替换文件'}
    return {'status':'deferred','reason':'等待程序确认本周PDF送达后，再交付总结口信',
            'retry_at':(dt.datetime.fromisoformat(state['as_of'])+dt.timedelta(minutes=1)).isoformat()}


def payload_for(c,action_key):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='weekly_material_deliveries'").fetchone():return None
    row=c.execute('SELECT payload FROM weekly_material_deliveries WHERE action_key=?',(action_key,)).fetchone()
    return json.loads(row[0]) if row else None

def authorize_part(c,key,operation_id=None,body=None):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='weekly_material_deliveries'").fetchone():return False
    state=tm.state()
    if not state:return False
    row=c.execute('SELECT action_key,payload,scope,asset_id FROM weekly_material_deliveries WHERE run_id=? AND uid=? AND ?=run_id||\':\'||action_key',(state['run_id'],state['uid'],key)).fetchone()
    if not row:return False
    if not ENABLED:raise ValueError('固定文件发送已撤销')
    if not due(state):raise ValueError('固定文件已超出当周发送窗口')
    reason=allowed(c,state)
    if reason:raise ValueError(reason)
    material=select(state)
    if material['status']!='ready' or material['scope']!=row[2] or material['asset']['id']!=row[3]:raise ValueError('当周文件绑定已变化')
    if body is not None and json.loads(body)!=json.loads(row[1]):raise ValueError('固定文件消息被修改')
    if not __import__('experiment_runtime').manifest():
        if not (__import__('manual_workbench').verify() or __import__('family_lab_bridge').verify()) or json.loads(row[1])['url']!=__import__('manual_media').url(material['asset']):raise ValueError('真实PDF发布绑定不符')
    if operation_id:c.execute('INSERT OR IGNORE INTO weekly_material_authorizations VALUES(?,?,?)',(operation_id,row[0],s.now()))
    return True

def context():
    state=tm.state();value=select(state)
    if value['status']!='ready':return value
    asset=value['asset'];result={'status':'prepared','name':asset['name'],'course_week':asset['course_week'],'sha256':asset['sha256'],'kind':'weekly_review_pdf','is_learning_status_link':False}
    with closing(sqlite3.connect(s.DB)) as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='weekly_material_deliveries'").fetchone():
            row=c.execute('SELECT d.status FROM weekly_material_deliveries w JOIN deliveries d ON d.action_key=w.action_key WHERE w.run_id=? AND w.uid=? AND w.scope=?',(state['run_id'],state['uid'],value['scope'])).fetchone()
            if row:result['delivery_status']=row[0]
    return result

def tick():
    if not ENABLED:return {'status':'disabled','reason':'固定文件发送已撤销'}
    state=tm.state()
    if not state:return {'status':'disabled'}
    path=s.R/'config/frozen-experiment-input.json'
    scenario=json.loads(path.read_text()).get('scenario',{}) if path.exists() else {}
    if scenario and not (scenario.get('mode')=='planning' or scenario.get('weekly_material_only') is True or scenario.get('mode')=='execution' and any(x.get('skill')=='weekly-learning-review' for x in scenario.get('initial_tasks',[]))):return {'status':'outside_experiment_scope'}
    if not state.get('auto_highlights') and not scenario:
        target=state.get('manual_service_task_id')
        selected=s.read("SELECT 1 FROM service_tasks WHERE id=? AND run_id=? AND skill='weekly-learning-review' AND status IN ('planned','deferred')",(target,state['run_id'])) if target else []
        if not selected or not __import__('manual_workbench').verify():return {'status':'manual'}
    import observability
    return observability.invoke('host_weekly_material',{},queue,origin='scheduler')

if __name__=='__main__':
    __import__('observability').initialize()
    print(s.encoded(tick()))
