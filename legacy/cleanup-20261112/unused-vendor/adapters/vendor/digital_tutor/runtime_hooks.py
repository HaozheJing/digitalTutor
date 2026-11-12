"""Private, host-controlled stable-point gates for isolated fault acceptance."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import time
from .domain import require

def gate(app,token,name):
    hooks=getattr(app,'test_hooks',{})
    value=hooks.get(name)
    if not value:return
    if isinstance(value,dict):
        faults={('before:knowledge.read','knowledge_unavailable'):('KNOWLEDGE_UNAVAILABLE','知识正文读取服务暂不可用，未取得正文'),
                ('before:skills.read','required_guide_unavailable'):('SKILL_MISSING','所选Skill的必读规范缺失，未取得完整方法和必读规范')}
        fault=faults.get((name,value.get('fault')))
        require(app.strict_runtime and fault is not None,'FAULT_SCOPE','Unsupported isolated fault')
        app.auth(token,{'service','planner_daily','planner_progress'})
        if value.get('active_file') and not Path(value['active_file']).exists():
            return
        require(False,*fault)
    root=Path(value).resolve()
    require(app.strict_runtime,'FAULT_SCOPE','Fault gates require isolated strict runtime')
    root.mkdir(parents=True,exist_ok=True)
    (root/'reached').touch()
    deadline=time.monotonic()+30
    while not (root/'release').exists():
        app.auth(token,{'service','planner_daily','planner_progress'})
        require(time.monotonic()<deadline,'FAULT_GATE_TIMEOUT','Test gate release missing')
        time.sleep(.02)


def crash_boundary(app, name, **identity):
    """Private one-shot observation barrier, never a business decision or retry grant.

    The external isolated controller kills the real processes after reached.json.
    A consumed barrier is never rearmed on restart. No live transaction may span it.
    """
    value=getattr(app,'test_hooks',{}).get('crash:'+name)
    if not value:return
    allowed={'before_transport','transport_entered','after_transport','after_receipt',
             'after:progress.submit','after:planner.commit_progress','worker:before_finish'}
    require(app.strict_runtime and name in allowed and isinstance(value,dict),
            'FAULT_SCOPE','Unsupported isolated crash boundary')
    with app.store.lock:
        require(not app.store.connection.in_transaction,'FAULT_SCOPE','Crash boundary must follow commit')
    if value.get('job_kind') and value['job_kind']!=identity.get('job_kind'):return
    root=Path(value['root']).resolve()
    root.mkdir(parents=True,exist_ok=True)
    try:
        (root/'consumed').mkdir()
    except FileExistsError:return
    record={'boundary':name,'pid':os.getpid(),'ppid':os.getppid(),
            'at':datetime.now(timezone.utc).isoformat(),
            'identity':{k:v for k,v in identity.items() if k in {'operation_id','job_id','job_kind','report_id','revision','request_id'}},
            'business_transaction_open':False}
    temp=root/'reached.tmp'
    with temp.open('x',encoding='utf-8') as handle:
        json.dump(record,handle);handle.flush();os.fsync(handle.fileno())
    temp.replace(root/'reached.json')
    deadline=time.monotonic()+120
    while not (root/'release').exists():
        require(time.monotonic()<deadline,'FAULT_GATE_TIMEOUT','Crash controller did not acknowledge boundary')
        time.sleep(.02)
