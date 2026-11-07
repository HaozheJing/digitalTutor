"""Local WeChat-shaped adapter; it has no network transport or real send path."""
from .domain import MessageRead, MessageReadAll, MessageReadStream, Send, Receipt, BusinessError, require
from .storage import digest, new_id, encode
from .send_policy import check_send, reserve_contact
from .receipt_state import merge_stage


def read_all_messages(app, token, args):
    cmd = MessageReadAll.model_validate(args)
    _require_arrived(app, token)
    result = read_messages(app, token, {'mode': 'all', 'direction': cmd.direction})
    return {**result, 'read_tool': 'wechat.read_all_messages', 'now': app.clock.iso(),
            'visibility': 'arrived_messages_only', 'complete_snapshot': True}


def read_message_stream(app, token, args):
    cmd = MessageReadStream.model_validate(args)
    _require_arrived(app, token)
    from .workflow import enabled
    from .business_time import business_day
    scope=cmd.scope or ('current_day' if enabled(app) else 'all_history')
    day=business_day(app.clock.now()) if scope=='current_day' else None
    result = read_messages(app, token, {'mode': 'since', **cmd.model_dump(exclude={'scope'})},day=day)
    return {**result, 'read_tool': 'wechat.read_message_stream', 'now': app.clock.iso(),
            'scope':scope,'business_day':day,'archive_tool':'resource.read',
            'visibility': 'arrived_messages_only', 'polling': True,
            'meaning': '只返回当前已入库且seq>after_seq的新增页；有has_more时继续分页，未到达消息不预加载。读取不标记已答。'}


def _require_arrived(app, token):
    from .domain import timestamp
    with app.store.transaction():
        family, _ = app.auth(token, {'service','planner_daily','planner_progress','admin'})
        now=app.clock.now()
        require(all(timestamp(m['occurred_at'])<=now and timestamp(m['received_at'])<=now
                    for m in app.store.list('message',family)), 'FUTURE_MESSAGE_STATE',
                '消息库含未来输入，拒绝读取而非用游标掩盖泄漏；须修正宿主加载器')


def read_messages(app,token,args,*,day=None):
    cmd=MessageRead.model_validate(args)
    with app.store.transaction() as db:
        family,binding=app.auth(token,{'service','planner_daily','planner_progress','admin'})
        filters=['family_id=?']; params=[family]
        if day:
            from .business_time import business_day
            ids=[m['id'] for m in app.store.list('message',family)
                 if business_day(m.get('received_at',m['occurred_at']))==day]
            filters.append('message_id IN ('+','.join('?' for _ in ids)+')' if ids else '0=1')
            params.extend(ids)
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
        if not day and app.strict_runtime and binding.get('job_kind')=='service' and cmd.direction in {'all','inbound'}:
            cursor_id='wechat:service:'+family+':'+cmd.direction
            previous=db.execute("SELECT version,data FROM records WHERE kind='runtime_cursor' AND id=? AND family_id=?",(cursor_id,family)).fetchone()
            current=app.store.get('runtime_cursor',cursor_id,family) if previous else {'after_seq':0}
            complete_prefix=(cmd.mode=='all' or (cmd.mode=='latest' and total==len(rows))
                             or (cmd.mode=='since' and cmd.after_seq<=current['after_seq']))
            next_seq=rows[-1]['seq'] if rows else cmd.after_seq
            if complete_prefix and rows and next_seq>current['after_seq']:
                app.store.put('runtime_cursor',cursor_id,family,{'direction':cmd.direction,'after_seq':next_seq,
                    'source':'successful_service_message_read','read_marks_answered':False},
                    previous['version'] if previous else None)
        result={'mode':cmd.mode,'messages':messages,'returned_count':len(messages),'matching_count':total,
                'has_more':cmd.mode=='since' and total>len(messages),
                'has_older':cmd.mode=='latest' and total>len(messages),
                'next_cursor':rows[-1]['seq'] if rows else cmd.after_seq,
                'snapshot_watermark':watermark,'read_marks_answered':False,'channel':'mock_wechat'}
        from .service_context import record_message_read
        from .conversation_context import utterance_groups
        result['utterance_groups']=utterance_groups(messages)
        result['utterance_group_contract']='连续发言的阅读视图，不改原message_id、时间或答复记账；unknown不证明同一说话人。'
        record_message_read(app,family,binding,result)
        return result


def _save_confirmed_message(app,family,op):
    db=app.store.connection
    existing=db.execute("SELECT message_id FROM message_index WHERE family_id=? AND external_id=? AND direction='outbound'",(family,op['id'])).fetchone()
    if existing:
        return existing['message_id']
    mid=new_id('msg')
    app.store.put('message',mid,family,{'external_id':op['id'],'body':op['body'],
        'occurred_at':op['occurred_at'],'received_at':app.clock.iso(),'direction':'outbound',
        'source_kind':'mock_wechat','operation_id':op['id'],'interpreted':True,'intents':[],
        **({'media':op['media']} if op.get('media') else {})})
    from .media_generation import record_artifact
    record_artifact(app,family,op)
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
    if not cmd.context_refs:request.pop('context_refs')
    if cmd.proposal_ref is None:request.pop('proposal_ref')
    if cmd.retry_operation_id is None:request.pop('retry_operation_id')
    if cmd.media_id is None:request.pop('media_id')
    for ref in request['reply_to']:
        if ref['resolves_intent']:ref.pop('resolves_intent')  # Preserve old receipt digests.
        if ref.get('response_excerpt') is None:ref.pop('response_excerpt', None)

    def action(family,context):
        require(not context.get("no_contact"),"NO_CONTACT_RUN","内部复核不能发送消息")
        db=app.store.connection
        binding=app.store.family(family)
        for mid in cmd.context_refs:
            app.store.get('message', mid, family)
        proposal = None
        if cmd.proposal_ref:
            from .reschedule_response import proposal_snapshot
            proposal = proposal_snapshot(app, family, cmd.proposal_ref.event_id, cmd.proposal_ref.change_id)
        require(binding['mock']==1,'REAL_CHANNEL_DISABLED','未接入真实微信发送')
        require(bool(cmd.reply_to) != bool(cmd.event_id),'INVALID_TARGET','一次发送选择来信答复或原事项交付')
        if cmd.reply_to:
            require(cmd.action_kind in {'reply','clarify_existing'} and cmd.part_id is None,'INVALID_TARGET','来信答复不能伪装独立交付')
            reply_pairs=[(r.message_id,r.intent_id) for r in cmd.reply_to]
            require(len(reply_pairs)==len(set(reply_pairs)),'INVALID_SCHEMA','回复意图重复')
            multiple_messages = len({r.message_id for r in cmd.reply_to}) > 1
            for ref in cmd.reply_to:
                source_message=app.store.get('message',ref.message_id,family)
                source_intent=next((i for i in source_message['intents'] if i['intent_id']==ref.intent_id),None)
                if source_intent and source_intent.get('source_message_id'):
                    require(source_intent['source_message_id']==ref.message_id,
                            'INTENT_SOURCE_MISMATCH','原意图与其消息来源不一致，不能发送或登记已答')
                require(ref.response_excerpt is None or ref.response_excerpt in cmd.body,
                        'REPLY_BINDING_INVALID', '回应摘录必须实际出现在本次发送正文')
                require(not (app.strict_runtime and multiple_messages and not ref.resolves_intent)
                        or ref.response_excerpt is not None,
                        'REPLY_BINDING_REQUIRED', '多消息部分回应须提供对应正文摘录；只作参考的旧问题用context_refs')
            identity={'reply_to':sorted(reply_pairs)}
            if proposal:
                identity['proposal'] = {k: proposal[k] for k in ('event_id', 'change_id', 'proposal_digest')}
            unresolved=sorted((r.message_id,r.intent_id) for r in cmd.reply_to if not r.resolves_intent)
            if unresolved:identity['unresolved_reply_to']=unresolved
            if app.strict_runtime:
                from .request_responsibility import decision_reply_refs
                decision_refs = decision_reply_refs(app, family, cmd)
                if decision_refs:identity['decision_reply_refs'] = decision_refs
                from .acceptance_handoff import reply_phases, reply_identity
                acceptance_phases = reply_phases(app, family, cmd)
                if acceptance_phases:identity['acceptance_reply_phases'] = reply_identity(acceptance_phases)
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
        from .media_generation import for_send
        media=for_send(app,family,cmd)
        content_digest=digest({'body':cmd.body,'recipient':binding['recipient'],'action_kind':cmd.action_kind,**({'media':media} if media else {})})
        if proposal:
            for prior in app.store.list('operation', family):
                if prior.get('proposal_snapshot') != proposal or (prior['stage'] == 'cancelled' and prior['dispatch_count'] == 0):
                    continue
                require(prior['content_digest'] == content_digest, 'PROPOSAL_OPERATION_EXISTS', '原提议已有操作 ' + prior['id'] + '，先查询原回执，不借改回复意图重发')
                return {**prior, 'reused_operation': True}
        old=db.execute('SELECT operation_id FROM operation_keys WHERE family_id=? AND purpose_key=?',(family,purpose)).fetchone()
        retry_parent=None
        if cmd.retry_operation_id:
            parent=app.store.get('operation',cmd.retry_operation_id,family)
            require(parent.get('purpose_key')==purpose,'RETRY_SCOPE','Original purpose mismatch')
            successors=[o for o in app.store.list('operation',family) if o.get('retry_of')==parent['id']]
            if successors:
                require(len(successors)==1 and successors[0]['content_digest']==content_digest and successors[0].get('part_id')==cmd.part_id,
                        'RETRY_PAYLOAD_CHANGED','Existing successor has another payload')
                return {**successors[0],'reused_operation':True}
            from .delivery_retry import check_attempt
            retry_parent=check_attempt(app,family,cmd,context)
        if old and retry_parent is None:
            op=app.store.get('operation',old['operation_id'],family)
            if op['stage']=='cancelled' and op['dispatch_count']==0:
                require(not op.get('retry_of'),'RETRY_REFERENCE_REQUIRED','Cancelled retry retains its lineage; cite this operation for a bounded successor')
                db.execute('DELETE FROM operation_keys WHERE family_id=? AND purpose_key=?',(family,purpose))
            else:
                require(op.get('proposal_snapshot') == proposal, 'OPERATION_ALREADY_EXISTS', '不能把旧外发重标为新提议')
                require(op['content_digest']==content_digest,'OPERATION_ALREADY_EXISTS','原义务已有操作，改写内容不能获得新发送资格')
                return {**op,'reused_operation':True}
        app.check_version(family,cmd.expected_family_version)
        from .delivery_retry import check_attempt
        check_attempt(app,family,cmd,context)
        check_send(app,family,cmd, capability=context)
        lessons=reserve_contact(app,family,cmd)
        opid=new_id('operation')
        stage=getattr(app,'mock_send_outcome','sent')
        require(stage in {'sent','delivered','unknown','accepted','failed'},'INVALID_CONFIG','未知模拟发送模式')
        from .delivery_retry import source_digest
        op=app.store.put('operation',opid,family,{
            **({'body_source_digest':source_digest(app,family),
                'material_approval_digest':digest(getattr(app,'material_approval',None))}
               if cmd.action_kind=='deliver_feedback' else {}),
            **({'retry_of':retry_parent['id'],'root_operation_id':retry_parent.get('root_operation_id',retry_parent['id']),
                'attempt_number':retry_parent.get('attempt_number',1)+1} if retry_parent else {}),
            'body':cmd.body,'action_kind':cmd.action_kind,'event_id':cmd.event_id,'part_id':cmd.part_id,
            **({'media':media} if media else {}),
            'reply_to':request['reply_to'], 'recipient':binding['recipient'],
            'context_refs':cmd.context_refs,
            **({'decision_reply_refs':decision_refs, 'acceptance_reply_phases':acceptance_phases}
               if app.strict_runtime and cmd.reply_to else {}),
            'requirement_revision':requirement_revision,'purpose_key':purpose,'content_digest':content_digest,
            'stage':'queued','occurred_at':app.clock.iso(),'channel':'mock_wechat','simulated':True,
            'business_date':app.clock.now().date().isoformat(),'lesson_refs':lessons,
            'request':request,'lease':context.get('lease'),
            **({'proposal_snapshot':proposal} if proposal else {}),
            'run_id':context.get('run_id'),
            'send_capability':{k:context.get(k) for k in ('mode','reply_scope','no_contact','output_mode','job_id','run_id','job_kind','current_step_key','event_id','commitment_id','schedule_revision','steerable','steering_seen_seq')},
            'prepared_family_version':app.store.family(family)['version']+1,
            'dispatch_count':0,'receipts':[]})
        if retry_parent:
            changed=db.execute('UPDATE operation_keys SET operation_id=? WHERE family_id=? AND purpose_key=? AND operation_id=?',
                               (opid,family,purpose,retry_parent['id'])).rowcount
            require(changed==1,'RETRY_HEAD_CHANGED','Concurrent retry cannot branch')
        else:
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
    with app.store.transaction() as db:
        # Preparation and dispatch may update different records. Return all final
        # touched versions, including source messages and the confirmed outbound.
        keys={(r['kind'],r['id']) for r in result['record_versions'].get('changed_records',[])}
        keys.add(('operation',op['id']))
        keys.update(('message',r['message_id']) for r in op.get('reply_to',[]))
        keys.update(('message',m['id']) for m in app.store.list('message',family)
                    if m.get('operation_id')==op['id'])
        refs=[{'kind':kind,'id':rid,'version':app.store.get(kind,rid,family)['version']}
              for kind,rid in sorted(keys)]
        final={**result,'data':op,'record_versions':{
            'family_input_version':app.store.family(family)['version'], 'changed_records':refs}}
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
