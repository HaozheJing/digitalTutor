"""Local WeChat-shaped adapter; it has no network transport or real send path."""
from .domain import MessageRead, Send, Receipt, BusinessError, require
from .storage import digest, new_id, encode
from .send_policy import check_send, reserve_contact
from .receipt_state import merge_stage


def read_messages(app,token,args):
    cmd=MessageRead.model_validate(args)
    with app.store.transaction() as db:
        family,binding=app.auth(token,{'service','planner_daily','planner_progress','admin'})
        filters=['family_id=?']; params=[family]
        if cmd.direction!='all':
            filters.append('direction=?'); params.append(cmd.direction)
        if cmd.mode=='since':
            filters.append('seq>?'); params.append(cmd.after_seq)
        where=' AND '.join(filters)
        total=db.execute('SELECT count(*) FROM message_index WHERE '+where,params).fetchone()[0]
        # Full reads either return the entire snapshot or explicitly fail; never silently truncate.
        if cmd.mode=='all':
            require(total<=10000,'MESSAGE_RESULT_TOO_LARGE','请用since模式和游标分页读取')
        order='DESC' if cmd.mode=='latest' else 'ASC'
        sql='SELECT seq,message_id FROM message_index WHERE '+where+' ORDER BY seq '+order
        if cmd.mode!='all':
            sql+=' LIMIT ?'; params.append(cmd.limit)
        rows=db.execute(sql,params).fetchall()
        if cmd.mode=='latest':
            rows=list(reversed(rows))
        messages=[{'seq':r['seq'],**app.store.get('message',r['message_id'],family)} for r in rows]
        watermark=db.execute('SELECT coalesce(max(seq),0) FROM message_index WHERE family_id=?',(family,)).fetchone()[0]
        # A read checkpoint is runtime bookkeeping, not a family business mutation or answer.
        # Keep direction-specific complete prefixes; latest and jumped since offsets cannot
        # claim unread older input was observed. All either succeeds in full or fails above.
        if app.strict_runtime and binding.get('job_kind')=='service' and cmd.direction in {'all','inbound'}:
            cursor_id='wechat:service:'+family+':'+cmd.direction
            previous=db.execute("SELECT version,data FROM records WHERE kind='runtime_cursor' AND id=? AND family_id=?",(cursor_id,family)).fetchone()
            current=app.store.get('runtime_cursor',cursor_id,family) if previous else {'after_seq':0}
            complete_prefix=cmd.mode=='all' or (cmd.mode=='since' and cmd.after_seq<=current['after_seq'])
            next_seq=rows[-1]['seq'] if rows else cmd.after_seq
            if complete_prefix and rows and next_seq>current['after_seq']:
                app.store.put('runtime_cursor',cursor_id,family,{'direction':cmd.direction,'after_seq':next_seq,
                    'source':'successful_service_message_read','read_marks_answered':False},
                    previous['version'] if previous else None)
        return {'mode':cmd.mode,'messages':messages,'returned_count':len(messages),'matching_count':total,
                'has_more':cmd.mode=='since' and total>len(messages),
                'has_older':cmd.mode=='latest' and total>len(messages),
                'next_cursor':rows[-1]['seq'] if rows else cmd.after_seq,
                'snapshot_watermark':watermark,'read_marks_answered':False,'channel':'mock_wechat'}


def _save_confirmed_message(app,family,op):
    db=app.store.connection
    existing=db.execute("SELECT message_id FROM message_index WHERE family_id=? AND external_id=? AND direction='outbound'",(family,op['id'])).fetchone()
    if existing:
        return existing['message_id']
    mid=new_id('msg')
    app.store.put('message',mid,family,{'external_id':op['id'],'body':op['body'],
        'occurred_at':op['occurred_at'],'received_at':app.clock.iso(),'direction':'outbound',
        'source_kind':'mock_wechat','operation_id':op['id'],'interpreted':True,'intents':[]})
    db.execute('INSERT INTO message_index(message_id,family_id,external_id,direction) VALUES(?,?,?,?)',(mid,family,op['id'],'outbound'))
    grouped={}
    partial={}
    for ref in op['reply_to']:
        if not ref.get('resolves_intent',True):
            partial.setdefault(ref['message_id'],set()).add(ref['intent_id'])
            continue
        grouped.setdefault(ref['message_id'],set()).add(ref['intent_id'])
    for message_id in set(grouped)|set(partial):
        intents=grouped.get(message_id,set())
        msg=app.store.get('message',message_id,family)
        for intent in msg['intents']:
            if intent['intent_id'] in intents and op['id'] not in intent['reply_operation_refs']:
                intent['reply_operation_refs'].append(op['id'])
            if intent['intent_id'] in partial.get(message_id,set()):
                refs=intent.setdefault('partial_reply_operation_refs',[])
                if op['id'] not in refs:refs.append(op['id'])
        app.store.put('message',message_id,family,msg,msg['version'])
    return mid


def prepare_message(app,token,args):
    cmd=Send.model_validate(args)
    request=cmd.model_dump()
    for ref in request['reply_to']:
        if ref['resolves_intent']:ref.pop('resolves_intent')  # Preserve old receipt digests.

    def action(family,context):
        require(not context.get("no_contact"),"NO_CONTACT_RUN","内部复核不能发送消息")
        db=app.store.connection
        binding=app.store.family(family)
        require(binding['mock']==1,'REAL_CHANNEL_DISABLED','未接入真实微信发送')
        require(bool(cmd.reply_to) != bool(cmd.event_id),'INVALID_TARGET','一次发送选择来信答复或原事项交付')
        if cmd.reply_to:
            require(cmd.action_kind in {'reply','clarify_existing'} and cmd.part_id is None,'INVALID_TARGET','来信答复不能伪装独立交付')
            reply_pairs=[(r.message_id,r.intent_id) for r in cmd.reply_to]
            require(len(reply_pairs)==len(set(reply_pairs)),'INVALID_SCHEMA','回复意图重复')
            identity={'reply_to':sorted(reply_pairs)}
            unresolved=sorted((r.message_id,r.intent_id) for r in cmd.reply_to if not r.resolves_intent)
            if unresolved:identity['unresolved_reply_to']=unresolved
            purpose=digest(identity)
            requirement_revision=None
        else:
            event=app.event(family,cmd.event_id)
            require(cmd.action_kind in {'remind_makeup','deliver_feedback'},'INVALID_TARGET','主动事项必须声明实际动作')
            if cmd.action_kind=='deliver_feedback':
                require(event['completion']['kind']=='delivery' and cmd.part_id in event['completion']['required_part_ids'],'INVALID_TARGET','交付部分不属于原事项')
            else:
                require(event['completion']['kind']=='goal' and cmd.part_id is None,'INVALID_TARGET','补课提醒必须关联目标型事项')
            requirement_revision=event['requirement_revision']
            identity={'event_id':cmd.event_id,'part_id':cmd.part_id,'action_kind':cmd.action_kind,'requirement_revision':requirement_revision}
            if cmd.step_id and cmd.action_kind=='remind_makeup':
                identity['step_id']=cmd.step_id
            purpose=digest(identity)
        content_digest=digest({'body':cmd.body,'recipient':binding['recipient'],'action_kind':cmd.action_kind})
        old=db.execute('SELECT operation_id FROM operation_keys WHERE family_id=? AND purpose_key=?',(family,purpose)).fetchone()
        if old:
            op=app.store.get('operation',old['operation_id'],family)
            if op['stage']=='cancelled' and op['dispatch_count']==0:
                db.execute('DELETE FROM operation_keys WHERE family_id=? AND purpose_key=?',(family,purpose))
            else:
                require(op['content_digest']==content_digest,'OPERATION_ALREADY_EXISTS','原义务已有操作，改写内容不能获得新发送资格')
                return {**op,'reused_operation':True}
        app.check_version(family,cmd.expected_family_version)
        check_send(app,family,cmd, capability=context)
        lessons=reserve_contact(app,family,cmd)
        opid=new_id('operation')
        stage=getattr(app,'mock_send_outcome','sent')
        require(stage in {'sent','delivered','unknown','accepted','failed'},'INVALID_CONFIG','未知模拟发送模式')
        op=app.store.put('operation',opid,family,{
            'body':cmd.body,'action_kind':cmd.action_kind,'event_id':cmd.event_id,'part_id':cmd.part_id,
            'reply_to':request['reply_to'], 'recipient':binding['recipient'],
            'requirement_revision':requirement_revision,'purpose_key':purpose,'content_digest':content_digest,
            'stage':'queued','occurred_at':app.clock.iso(),'channel':'mock_wechat','simulated':True,
            'business_date':app.clock.now().date().isoformat(),'lesson_refs':lessons,
            'request':request,'lease':context.get('lease'),
            'run_id':context.get('run_id'),
            'send_capability':{k:context.get(k) for k in ('mode','reply_scope','no_contact','output_mode','job_id','run_id')},
            'prepared_family_version':app.store.family(family)['version']+1,
            'dispatch_count':0,'receipts':[]})
        db.execute('INSERT INTO operation_keys VALUES(?,?,?)',(family,purpose,opid))
        return op
    payload=dict(request)
    if cmd.step_id is None:
        payload.pop('step_id')  # Preserve stage-one request digests.
    return app.write(token,{'service'},'wechat.send_message',payload,action)


def send_message(app,token,args):
    from .outbox import dispatch
    result=prepare_message(app,token,args)
    if result['outcome']=='duplicate':
        return result
    family,_=app.auth(token,{'service'})
    op=dispatch(app,family,result['data']['id'])
    final={**result,'data':op,'record_versions':{'family_input_version':app.store.family(family)['version']}}
    with app.store.transaction() as db:
        db.execute('UPDATE receipts SET result=? WHERE family_id=? AND request_id=?',(encode(final),family,args['request_id']))
    return final


def mock_receipt(app,token,args):
    args = Receipt.model_validate(args).model_dump()
    def action(family,_):
        op=app.store.get('operation',args['operation_id'],family)
        stage=args['stage']
        op['stage']=merge_stage(op['stage'],stage,strict=True)
        op['receipts'].append({'stage':stage,'observed_at':app.clock.iso(),'source':'mock_receipt'})
        op=app.store.put('operation',op['id'],family,op,op['version'])
        if op['stage'] in {'sent','delivered'}:
            _save_confirmed_message(app,family,op)
        return op
    return app.write(token,{'channel','admin'},'wechat.mock_receipt',args,action)
