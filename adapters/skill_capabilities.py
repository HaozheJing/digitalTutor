"""Turn-owned tool exposure from code-reviewed Skill runtime manifests.

Published teacher Skill prose cannot change this registry or grant authority.
The original domain guards still validate every execution and receipt.
"""
import json,hashlib,sqlite3
from pathlib import Path
from contextlib import closing
import sql_tools as s

SKILLS=('manage-service-arrangements','plan-service-day','plan-after-lesson-completion','review-service-case','highlight-growth-video','send-festival-wishes','inspect-family-image','generate-reading-audio')
PRIVATE=frozenset({'workflow_read','workflow_commit','workflow_fulfill','preference_commit','completion_plan_commit','service_case_review','service_task_finish','workflow_highlight','highlight_lookup','generate_image','generate_audio','perceive_image'})
INTERNAL_KINDS=frozenset({'plan','completion_plan','case_review'})
INTERNAL_FORBIDDEN=frozenset({'send_message','workflow_fulfill','workflow_highlight','generate_image','generate_audio'})
PUBLIC_CAPABILITY_SKILLS=[{'name':'manage-service-arrangements','description':'查看、调整或在当前回复中履行已有安排时使用；仅发普通回复不会完成原事项。'},{'name':'inspect-family-image','description':'理解已登记或授权图片，开放perceive_image。'},
                         {'name':'generate-reading-audio','description':'按明确需要生成音频素材；当前不支持语音交付。'}]

def enabled():
    return __import__('capability_surface').enabled() and __import__('tool_surface').enabled('skill_scopes')

def policies():
    result={}
    for name in SKILLS:
        path=s.R/'skills'/name/'runtime-capabilities.json'
        value=json.loads(path.read_text())
        if value.get('version')!=1 or set(value)-{'version','task_kinds','on_load','on_task'}:raise ValueError('invalid_skill_runtime_contract')
        for field in ('on_load','on_task'):
            if not isinstance(value[field],list) or not set(value[field])<=PRIVATE:raise ValueError('invalid_skill_runtime_tools')
        result[name]=value
    return result

def turn_binding():
    if not s.read("SELECT 1 FROM sqlite_master WHERE name='metadata'"):return None
    rows=s.read("SELECT value FROM metadata WHERE key='model_tool_turn'")
    return json.loads(rows[0]['value']) if rows else None

def bind_turn(task_id,request_id):
    if not enabled():return
    state=__import__('test_mode').state();task=__import__('agent_plan').active()
    if not state or not task or task['id']!=task_id or task['native_request_id']!=request_id:raise ValueError('tool_turn_binding_mismatch')
    value={'run_id':state['run_id'],'uid':state['uid'],'task_id':task_id,'request_id':request_id,'attempts':task['attempts']}
    with closing(sqlite3.connect(s.DB)) as c,c:c.execute("INSERT OR REPLACE INTO metadata VALUES('model_tool_turn',?)",(s.encoded(value),))

def release_turn(request_id=None):
    if not enabled():return
    current=turn_binding()
    if current and (request_id is None or current['request_id']==request_id):
        with closing(sqlite3.connect(s.DB)) as c,c:c.execute("DELETE FROM metadata WHERE key='model_tool_turn'")

def owner():
    state=__import__('test_mode').state()
    if not state:return None
    task=__import__('agent_plan').active()
    bound=turn_binding()
    if not task and bound and bound['run_id']==state['run_id'] and bound['uid']==state['uid']:
        rows=s.read('SELECT * FROM service_tasks WHERE id=? AND run_id=? AND native_request_id=? AND attempts=?',(bound['task_id'],state['run_id'],bound['request_id'],bound['attempts']))
        task=rows[0] if rows else None
    if task:
        identity=['task',task['id'],task.get('native_request_id'),task.get('attempts')]
        kind=task['kind']
    else:
        rows=s.read("SELECT id,attempts FROM events WHERE kind='passive' AND status='dispatched'")
        if len(rows)!=1:return None
        identity=['dialogue',rows[0]['id'],rows[0]['attempts']];kind='dialogue'
    key=hashlib.sha256(s.encoded([state['run_id'],state['uid'],identity]).encode()).hexdigest()
    return {'key':key,'kind':kind,'run_id':state['run_id'],'uid':state['uid']}

def grant(names):
    if not enabled():return []
    current=owner()
    if not current:return []
    if not set(names)<=PRIVATE:raise ValueError('unknown_private_capability')
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('CREATE TABLE IF NOT EXISTS skill_capability_grants(owner TEXT,run_id TEXT,uid TEXT,name TEXT,created_at TEXT,PRIMARY KEY(owner,name))')
        for name in names:c.execute('INSERT OR IGNORE INTO skill_capability_grants VALUES(?,?,?,?,?)',(current['key'],current['run_id'],current['uid'],name,s.now()))
    return sorted(set(names)-INTERNAL_FORBIDDEN if current['kind'] in INTERNAL_KINDS else names)

def visible():
    if not enabled():return set(PRIVATE)
    current=owner()
    if not current:return set()
    names=set()
    if current['kind']!='dialogue':names.update(('service_task_finish','workflow_read'))
    for value in policies().values():
        if current['kind'] in value['task_kinds']:names.update(value['on_task'])
    if s.read("SELECT 1 FROM sqlite_master WHERE name='skill_capability_grants'"):
        names.update(x['name'] for x in s.read('SELECT name FROM skill_capability_grants WHERE owner=? AND run_id=? AND uid=?',(current['key'],current['run_id'],current['uid'])))
    return names-INTERNAL_FORBIDDEN if current['kind'] in INTERNAL_KINDS else names

def hidden():
    if not enabled():return set()
    current=owner()
    return (PRIVATE-visible()) | (INTERNAL_FORBIDDEN if current and current['kind'] in INTERNAL_KINDS else set())

def guard(name,args=None):
    if name in hidden():raise __import__('agent_plan').ServiceValidationError('capability_not_open_in_current_skill_flow:'+name)
    if name in ('send_message','reply_to_message'):require_amendment_receipt()
    if not enabled() or name not in ('workflow_commit','workflow_fulfill'):return
    current=owner()
    if not current or current['kind']!='dialogue':return
    rows=s.read('SELECT * FROM skill_workflow_grants WHERE owner=?',(current['key'],)) if s.read("SELECT 1 FROM sqlite_master WHERE name='skill_workflow_grants'") else []
    binding=rows[0] if rows else None;a=args or {}
    purpose='amend' if name=='workflow_commit' else 'fulfill'
    if not binding or binding['purpose']!=purpose or a.get('context_token')!=binding['context_token']:
        raise __import__('agent_plan').ServiceValidationError('workflow_context_purpose_mismatch')
    if purpose=='fulfill' and (task_identity(a.get('task_id'))!=task_identity(binding['task_id']) or type(a.get('revision')) is not int or a['revision']!=binding['revision']):
        raise __import__('agent_plan').ServiceValidationError('workflow_fulfillment_target_mismatch')

def require_amendment_receipt():
    """A dialogue in the amendment flow cannot acknowledge an uncommitted change."""
    if not enabled():return
    current=owner()
    if not current or current['kind']!='dialogue':return
    binding=s.read("SELECT context_token,purpose FROM skill_workflow_grants WHERE owner=?",(current['key'],)) if s.read("SELECT 1 FROM sqlite_master WHERE name='skill_workflow_grants'") else []
    if not binding:
        if current_pause_request():
            raise __import__('agent_plan').ServiceValidationError('arrangement_pause_flow_required：当前家长消息包含联系暂停要求。先skill_read(name=manage-service-arrangements)，再workflow_read(view=context,purpose=amend)，按当前原话提交暂停并核对committed回执后才可确认。不得猜测范围、时间；需要澄清时可用purpose=inspect查看后提问，不能声称已暂停。')
        return
    if binding[0]['purpose']!='amend':return
    receipts=s.read("SELECT result FROM service_workflow_commits WHERE run_id=? AND json_extract(body,'$[0]')=?",(current['run_id'],binding[0]['context_token'])) if s.read("SELECT 1 FROM sqlite_master WHERE name='service_workflow_commits'") else []
    if any(json.loads(r['result']).get('status')=='committed' for r in receipts):return
    raise __import__('agent_plan').ServiceValidationError('arrangement_amendment_not_committed：本轮已进入安排修改流程，但没有本次上下文的committed回执。先调用workflow_commit提交原话支持的修改或contact_constraint；暂停约束即使tasks为空也要保存。家长明确暂停全部才可scope={kind:"all"}，某类暂停仍用对应skill。不能用send_message先确认“今天不打扰”或“已暂停”。资料变化时重新读取purpose=amend；只查看或需澄清使用purpose=inspect，不能声称已改动。')


def current_pause_request():
    """Route explicit current-message pause wording; never infer or write a constraint."""
    import re
    if not s.read("SELECT 1 FROM sqlite_master WHERE name='messages'"):return False
    if 'payload' not in {x['name'] for x in s.read('PRAGMA table_info(events)')}:return False
    rows=s.read("SELECT payload FROM events WHERE kind='passive' AND status='dispatched'")
    if len(rows)!=1:return False
    payload=json.loads(rows[0]['payload']);ids=payload.get('source_message_ids') or [payload.get('source_message_id')]
    for ident in set(ids)-{None}:
        for msg in s.read("SELECT body FROM messages WHERE source_id=? AND speaker='user'",(ident,)):
            body=re.sub(r'(?:不要|不用|无需|不必|不想|不能|别|不)(?:再)?(?:暂停|停止|停发)', '', msg['body'])
            contact=r'(?:主动|提醒|表扬|鼓励|联系|服务)'
            if re.search(r'(?:暂停|停止|停发)[^。！？；\n]{0,24}'+contact+'|'+contact+r'[^。！？；\n]{0,24}(?:暂停|停止|停发)|(?:不要|不用|别)(?:再)?(?:提醒|联系|打扰|催)',body):return True
    return False


def task_identity(value):
    return __import__('business').original(value) if s.read("SELECT 1 FROM sqlite_master WHERE name='business_ids'") else value

def workflow_scope(args,value,current):
    # Replace, never accumulate, the two dialogue write capabilities. Invalid or
    # empty reads revoke prior grants too; domain receipt/dedupe checks remain.
    purpose=args.get('purpose','inspect');task=None
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('CREATE TABLE IF NOT EXISTS skill_capability_grants(owner TEXT,run_id TEXT,uid TEXT,name TEXT,created_at TEXT,PRIMARY KEY(owner,name))')
        c.execute('CREATE TABLE IF NOT EXISTS skill_workflow_grants(owner TEXT PRIMARY KEY,context_token TEXT,purpose TEXT,task_id TEXT,revision INTEGER)')
        c.execute("DELETE FROM skill_capability_grants WHERE owner=? AND name IN ('workflow_commit','workflow_fulfill')",(current['key'],))
        c.execute('DELETE FROM skill_workflow_grants WHERE owner=?',(current['key'],))
    if not value.get('context_token'):return []
    if purpose not in ('inspect','amend','fulfill'):raise ValueError('invalid_workflow_purpose')
    if purpose=='fulfill':
        task=next((t for t in value.get('tasks',[]) if task_identity(t.get('id'))==task_identity(args.get('task_id'))),None)
        if not task or task.get('status') not in ('planned','deferred') or task.get('kind') in INTERNAL_KINDS|{'festival'} or type(task.get('revision')) is not int:
            raise ValueError('workflow_fulfillment_requires_existing_eligible_task')
    with closing(sqlite3.connect(s.DB)) as c,c:
        c.execute('INSERT INTO skill_workflow_grants VALUES(?,?,?,?,?)',(current['key'],value['context_token'],purpose,task['id'] if task else None,task['revision'] if task else None))
    return [] if purpose=='inspect' else ['workflow_commit' if purpose=='amend' else 'workflow_fulfill']

def loaded(name,value):
    if not enabled() or not isinstance(value,dict) or not value.get('body'):return value
    policy=policies().get(name)
    names=grant(policy['on_load']) if policy else []
    return {**value,'flow_capabilities':names,'capability_scope':'current_task_or_message_attempt_only','arrangement_changes':'如果家长要求取消、暂停、修改安排，或现在完成已有服务，应另读skill_read(name=manage-service-arrangements)。现在完成已有服务必须查实际task_id，再以purpose=fulfill读取上下文并用workflow_fulfill交付；不能先send_message再补记完成。仅阅读业务Skill或发送普通回复不会修改或完成原事项。'}

def read_complete(name,args,value):
    if not enabled() or not isinstance(value,dict):return value
    current=owner()
    if not current or current['kind']!='dialogue':return value
    names=[]
    if name=='memory_read' and args.get('for_update') is True and value.get('context_token'):names=['preference_commit']
    if name=='workflow_read' and args.get('view','context')=='context':names=workflow_scope(args,value,current)
    opened=grant(names)
    return {**value,'flow_capabilities':opened} if opened else value

def guidance():
    if not enabled():return ''
    return ('专用入口只在当前Skill流程开放，不是公共工具：家长取消、暂停、改时或恢复已有安排，必须先skill_read(name=manage-service-arrangements)，再workflow_read(view=context,purpose=amend)获取事务材料后提交；只查看用purpose=inspect。仅在本轮回复完整履行已有事项时，先用tasks视图找到实际事项，再以purpose=fulfill和task_id读取上下文，才开放绑定该事项及版本的workflow_fulfill；不与修改入口同时开放。'
            '明确遗忘或媒体许可先memory_read(for_update=true)，返回有效context_token后才开放preference_commit。'
            '读取现有高光先skill_read(name=highlight-growth-video)；图片理解先skill_read(name=inspect-family-image)；只有明确需要合成音频时才skill_read(name=generate-reading-audio)。'
            '主动任务的专用入口由宿主按任务类型提供；内部规划、完课规划和事项检查没有发送或生成交付素材权限。只用本轮已开放入口，权限与回执仍由业务程序核实；进入新消息或重试不会沿用上一轮入口。')

if __name__=='__main__':
    import sys
    action=sys.argv[1]
    if action=='bind':bind_turn(*sys.argv[2:])
    elif action=='release':release_turn(*sys.argv[2:])
    elif action=='reset':release_turn()
    else:raise ValueError('unknown_tool_scope_action')
    print(json.dumps({'status':'ok'}))
