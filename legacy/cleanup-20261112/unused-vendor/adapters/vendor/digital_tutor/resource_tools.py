"""Family-scoped bounded archive access and validated problem continuation."""
import json
from pydantic import Field
from typing import Literal
from .domain import Strict, ID, require, timestamp
from .storage import digest
from .business_time import business_day

ROLES={'service','planner_daily','planner_progress','admin'}


class ResourceRead(Strict):
    kind: str = Field(pattern='^(message|event|pending|run_outcome)$')
    record_id: ID | None = None
    query: str | None = Field(default=None,max_length=200)
    query_mode: Literal['all','any'] = Field(default='all',description='空格分隔的多个词：all要求全部命中，any命中一个即可。主题检索通常用any；检索提示不是事实证据。')
    business_day: str | None = Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}$')
    after: int = Field(default=0,ge=0)
    limit: int = Field(default=20,ge=1,le=50)
    snapshot: str | None = None
    detail: bool = False


def read(app,token,args):
    cmd=ResourceRead.model_validate(args)
    with app.store.transaction():
        family,binding=app.auth(token,ROLES)
        rows=app.store.list(cmd.kind,family)
        if cmd.kind=='message':
            rows=[r for r in rows if timestamp(r['occurred_at'])<=app.clock.now()
                  and timestamp(r.get('received_at',r['occurred_at']))<=app.clock.now()]
        if cmd.record_id:rows=[r for r in rows if r['id']==cmd.record_id]
        if cmd.business_day:
            rows=[r for r in rows if r.get('received_at',r.get('occurred_at')) and
                  business_day(r.get('received_at',r.get('occurred_at')))==cmd.business_day]
        from .recall import matches
        if cmd.query:
            import re
            terms=re.findall(r'[^\s,，;；]+',cmd.query)
            rows=[r for r in rows if (any(matches(term,json.dumps(r,ensure_ascii=False)) for term in terms)
                  if cmd.query_mode=='any' else matches(cmd.query,json.dumps(r,ensure_ascii=False)))]
        snapshot=digest([family,cmd.kind,cmd.record_id,cmd.query,cmd.query_mode,cmd.business_day,[(r['id'],r['version']) for r in rows]])
        require(cmd.after==0 or cmd.snapshot==snapshot,'RESOURCE_SNAPSHOT_CHANGED','分页范围已变，请从第一页重新读取')
        page=rows[cmd.after:cmd.after+cmd.limit]
        if cmd.kind=='message' and (cmd.detail or cmd.record_id):
            from .service_context import record_message_read
            seq={r['message_id']:r['seq'] for r in app.store.connection.execute('SELECT seq,message_id FROM message_index WHERE family_id=?',(family,))}
            record_message_read(app,family,binding,{'messages':[{'seq':seq[r['id']],**r} for r in page]})
        if not cmd.detail and not cmd.record_id:
            from .workflow import enabled
            page=[{**{k:v for k,v in r.items() if k in {'id','version','status','direction','occurred_at','received_at','goal_text','job_id','event_id'}},
                   **({'preview':r.get('body','')[:160],'preview_is_partial':len(r.get('body',''))>160} if enabled(app) else {})} for r in page]
        return {'kind':cmd.kind,'records':page,'total':len(rows),'snapshot':snapshot,
                'next_after':cmd.after+len(page),'has_more':cmd.after+len(page)<len(rows),
                'detail':cmd.detail or bool(cmd.record_id),'read_marks_answered':False}


class Continue(Strict):
    request_id: ID
    current_message_id: ID
    previous_message_id: ID
    progress_note: str = Field(min_length=1,max_length=1000)


def link(app,token,args):
    cmd=Continue.model_validate(args)
    def action(family,binding):
        require(cmd.current_message_id in binding.get('reply_scope',{}),'REPLY_SCOPE','当前来源不在本轮范围')
        old=app.store.get('message',cmd.previous_message_id,family)
        current=app.store.get('message',cmd.current_message_id,family)
        require(old['direction']=='inbound' and current['direction']=='inbound','INVALID_SOURCE','仅关联真实入站来源')
        require(timestamp(old['received_at'])<=app.clock.now() and timestamp(old['occurred_at'])<=app.clock.now(),'FUTURE_SOURCE','来源尚未到达')
        from .runtime_config import reply_scope
        require(old['id'] in reply_scope(app,family),'TASK_ALREADY_RESOLVED','原问题没有未结责任')
        link_id='link:'+digest([family,current['id'],old['id']])
        existing=app.store.connection.execute("SELECT id FROM records WHERE kind='problem_link' AND id=? AND family_id=?",(link_id,family)).fetchone()
        record=app.store.get('problem_link',link_id,family) if existing else app.store.put('problem_link',link_id,family,
            {'current_message_id':current['id'],'previous_message_id':old['id'],'progress_note':cmd.progress_note,
             'created_at':app.clock.iso()})
        from .jobs import JobQueue
        from .steering import enabled as steering_enabled
        JobQueue(app).enqueue(family,'continue:'+record['id'],{'kind':'service','message_id':old['id'],
            'problem_link_id':record['id'],
            **({'steerable':True,'coalesced_message_ids':[old['id']]} if steering_enabled(app) else {})})
        return {'link_id':record['id'],'continuation_queued':True,'current_reply_scope_unchanged':True}
    return app.write(token,{'service'},'service.link_problem',cmd.model_dump(),action)


SPECS={'resource.read':(ROLES,ResourceRead,'分页检索当前家庭旧聊天、事项及待办。先索引，record_id或detail读取原文。分页携带snapshot；检索无结果可去掉query完整回退。'),
       'service.link_problem':({'service'},Continue,'将本轮来信关联未结旧问题，宿主签发下一续接任务，不扩大当前外发范围。')}
HANDLERS={'resource.read':read,'service.link_problem':link}
