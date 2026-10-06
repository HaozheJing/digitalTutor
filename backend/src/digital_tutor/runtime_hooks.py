"""Private, host-controlled stable-point gates for isolated fault acceptance."""
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
