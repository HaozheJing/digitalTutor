"""Host-scoped native sessions. Business state remains authoritative and separate."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from .context_transport import expand, fingerprint
from .domain import require


def scope_key(database, family, system, tools, provider, materials):
    return fingerprint({'database':str(Path(database).resolve()), 'family':family,
                        'role':'service', 'system':system, 'tools':tools,
                        'provider':provider, 'materials':materials, 'format':1})


@contextmanager
def session_lock(root):
    """A second host never overwrites the shared profile or reads stale history."""
    import fcntl
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root/'host.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            require(False, 'NATIVE_SESSION_BUSY', '同一家庭会话已有运行实例')
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def anchor_key(value):
    if isinstance(value,dict) and {'message_id','body','occurred_at','direction','version'} <= set(value):
        return fingerprint(value)
    return None


def delta_context(context, history):
    """Reference only exact anchors still present in native active history.

    After native compaction a summary does not prove preservation of an original
    anchor. Such anchors are included again. No durable high-water mark can hide
    a source correction or same-text message with a different source identity.
    """
    known = set()
    def collect(node):
        key=anchor_key(node)
        if key:known.add(key)
        if isinstance(node,dict):
            for v in node.values():collect(v)
        elif isinstance(node,list):
            for v in node:collect(v)
    for message in history:
        if message.get('role') != 'user':continue
        try:
            collect(expand(json.loads(message['content'])))
        except (ValueError,TypeError,KeyError):
            continue
    result=deepcopy(context);references=[]
    dialogue=result.get('dialogue_context',{})
    for field in ('transcript','preceding_outbound'):
        rows=[]
        for anchor in dialogue.get(field,[]):
            key=anchor_key(anchor)
            if key in known:
                ref={'history_message_sha256':key,'message_id':anchor['message_id'],
                     'version':anchor['version'],'source_id':anchor.get('source_id')}
                rows.append(ref);references.append(ref)
            else:rows.append(anchor)
        if field in dialogue:dialogue[field]=rows
    result['native_history_contract']={
        'historical_anchors_reused':len(references),
        'current_snapshot_is_authoritative':True,
        'rule':'history_message_sha256 指向当前有效原生历史中的完整同版本消息；它不是省略未知资料。需要原文可调用 wechat.read_all_messages 或消息流补读。最新 run_contract、controls、operations、未结责任覆盖旧轮次状态。历史工具回包不是本轮读取回执；历史 assistant 最终文本是内部输出，不是对家长的发送。'}
    return result,references


def missing_wire_references(references, messages):
    """Check again AFTER Hermes compression, against the actual request copy."""
    known=set()
    def scan(value):
        key=anchor_key(value)
        if key:known.add(key)
        if isinstance(value,dict):
            for child in value.values():scan(child)
        elif isinstance(value,list):
            for child in value:scan(child)
    for message in messages:
        if message.get('role')!='user':continue
        try:scan(expand(json.loads(message['content'])))
        except (ValueError,TypeError,KeyError):continue
    return sorted({r['history_message_sha256'] for r in references}-known)


class NativeSession:
    def __init__(self, root, scope, run_id, *, db_path=None):
        from hermes_state import SessionDB
        self.root=Path(root);self.scope=scope;self.run_id=run_id
        self.db=SessionDB(Path(db_path) if db_path is not None else self.root/'session.sqlite')
        self.active_path=self.root/'active-session.json'
        active=json.loads(self.active_path.read_text()) if self.active_path.exists() else {'generation':0,'session_id':'dt-'+scope}
        self.generation=active['generation']
        self.session_id=self.db.resolve_resume_session_id(active['session_id'])
        if self.db.get_session(self.session_id):self.db.reopen_session(self.session_id)
        self.history=self.db.get_messages_as_conversation(self.session_id,repair_alternation=True,include_row_ids=True)
        self.journal=self.root/'turn.json'
        previous=json.loads(self.journal.read_text()) if self.journal.exists() else None
        require(not previous or previous.get('completed') is True,
                'NATIVE_SESSION_RECOVERY_REQUIRED','前轮未确认结束，需先核对业务回执与会话记录')
        self.write({'run_id':run_id,'session_id':self.session_id,'completed':False,
                    'loaded_messages':len(self.history),'scope':scope})

    def write(self,value):
        temp=self.journal.with_suffix('.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temp.replace(self.journal)

    def finish(self,agent,result):
        self._atomic(self.active_path,{'generation':self.generation,'session_id':agent.session_id})
        self.write({'run_id':self.run_id,'session_id':agent.session_id,
                    'completed':bool(result.get('completed')) and not result.get('interrupted') and not result.get('error'),
                    'scope':self.scope,'loaded_messages':len(self.history),
                    'business_completion_is_separate':True})

    @staticmethod
    def _atomic(path,value):
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');tmp.replace(path)

    def rotate(self,reason,checkpoint):
        """Keep old transcript; atomically select an empty next generation.

        Host already holds family+scope locks and reconciled predecessor receipts.
        SQL is the checkpoint authority. This does not replay any business action.
        """
        from uuid import uuid4
        old=self.session_id;new='dt-'+self.scope+'-g'+str(self.generation+1)+'-'+uuid4().hex[:8]
        record={'old_session_id':old,'new_session_id':new,'generation':self.generation+1,
                'reason':reason,'run_id':self.run_id,'checkpoint_sha256':fingerprint(checkpoint),
                'old_history_deleted':False,'replayed_send':False}
        # Persist prepared evidence before publishing the active pointer.
        self._atomic(self.root/'rotation-prepared.json',record)
        self._atomic(self.root/('checkpoint-'+str(self.generation+1)+'.json'),checkpoint)
        self._atomic(self.active_path,{'generation':self.generation+1,'session_id':new})
        self.generation+=1;self.session_id=new;self.history=[]
        self.write({'run_id':self.run_id,'session_id':new,'generation':self.generation,
                    'completed':False,'scope':self.scope,'rotation':record})
        with (self.root/'rotations.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(record)+'\n')
        return record

    def close(self):
        self.db.close()


def reconcile_interrupted_turn(app, family, root):
    """Allow a NEW run only after authoritative prior-run/receipt reconciliation.

    Called under the host's family session lock. Never replays a prompt, sends a
    message, changes a business outcome, or treats assistant text as completion.
    Missing ownership evidence, an active old capability, and uncertain sends
    retain the fence. The ordinary business worker decides what remains runnable.
    """
    root=Path(root);journal=root/'turn.json'
    if not journal.exists():return None
    previous=json.loads(journal.read_text(encoding='utf-8'))
    if previous.get('completed') is True:return None
    run_id=previous.get('run_id')
    with app.store.transaction() as db:
        sessions=[dict(row) for row in db.execute(
            "SELECT family_id,active,binding FROM sessions WHERE json_extract(binding,'$.run_id')=?",(run_id,))]
        require(sessions and all(s['family_id']==family and not s['active'] for s in sessions),
                'NATIVE_SESSION_RECOVERY_REQUIRED','前轮会话归属或结束状态尚未确认')
        operations=app.store.list('operation',family)
        require(not any(o['stage'] in {'queued','dispatching','accepted','unknown'} for o in operations),
                'NATIVE_SESSION_RECOVERY_REQUIRED','存在待发送或结果未知的操作，须先核对回执')
        audit={'previous_run_id':run_id,'family_version':app.store.family(family)['version'],
               'at':app.clock.iso(),'operation_receipts':[{'id':o['id'],'version':o['version'],'stage':o['stage']}
                    for o in operations if o.get('run_id')==run_id],
               'action':'permit_new_run_from_current_business_state','replayed_send':False,
               'business_outcome_unchanged':True}
        with (root/'recoveries.jsonl').open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(audit,ensure_ascii=False)+'\n');handle.flush()
        temp=journal.with_suffix('.tmp')
        temp.write_text(json.dumps({**previous,'completed':True,'reconciled_interruption':audit},ensure_ascii=False),encoding='utf-8')
        temp.replace(journal)
    return audit
