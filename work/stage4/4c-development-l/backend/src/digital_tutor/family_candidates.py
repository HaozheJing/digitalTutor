"""Conservative positive family-request adapter. Model proposals are not authority."""
from datetime import datetime,timedelta
import re
from pydantic import Field
from .domain import Strict,ID,Positive,SHANGHAI,require,timestamp,BusinessError
from .storage import digest


class MakeupProposal(Strict):
    request_id: ID
    expected_family_version: Positive
    source_message_id: ID
    source_version: Positive
    source_excerpt: str = Field(min_length=1,max_length=2000)
    batch_id: ID
    day: int = Field(strict=True,ge=1,le=240)


CHINESE_NUMBERS={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,
                 '十一':11,'十二':12,'十三':13,'十四':14,'十五':15,'十六':16,'十七':17,'十八':18,'十九':19,'二十':20}


def number(value):
    return int(value) if value.isdigit() else CHINESE_NUMBERS.get(value)


def message_content(message):
    return {key:message.get(key) for key in ('id','external_id','body','occurred_at','direction','source_kind')}


def explicit_request(message):
    """Only a direct, single-day positive request is accepted; unsupported prose stays pending.

    The complete message is checked, not a model-selected positive excerpt cut out of a negation.
    Date precision is authoritative; a day-only schedule never becomes a 23:59 deadline.
    """
    body=message['body'].strip()
    require(not re.search(r'不|别|取消|如果|假如|是否|能否|可不可以|或者|还是|可能|考虑|听说|引用|原话|例如|比如|[“”"「」《》]',body),
            'REQUEST_AMBIGUOUS','Only an unambiguous direct positive request is supported')
    require(re.match(r'^(?:请|麻烦)(?:帮(?:我们|我|孩子))?(?:安排|协助)?',body),
            'REQUEST_AMBIGUOUS','A direct request is required')
    days=re.findall(r'第\s*([0-9一二两三四五六七八九十]+)\s*(?:天|课)',body)
    require(len(days)==1 and number(days[0]) is not None,'LESSON_AMBIGUOUS','Exactly one explicit lesson day is required')
    require(re.search(r'补(?:读|课|学|完)',body),'REQUEST_AMBIGUOUS','An explicit makeup request is required')
    # Remove the narrow supported request vocabulary and time expression; reject extra clauses.
    residue=re.sub(r'^(?:请|麻烦)(?:帮(?:我们|我|孩子))?(?:安排|协助)?','',body)
    residue=re.sub(r'第\s*[0-9一二两三四五六七八九十]+\s*(?:天|课)','',residue)
    residue=re.sub(r'(?:\d{4}年)?\d{1,2}月\d{1,2}日|今天|明天|后天|本周[一二三四五六日天]|周[一二三四五六日天]','',residue)
    residue=re.sub(r'(?:上午|下午|晚上|中午)?(?:\d{1,2}|[一二两三四五六七八九十]+)点(?:半|[0-9]{1,2}分)?','',residue)
    residue=re.sub(r'补读|补课|补学|补完|读完|完成|安排|课程|课文|阅读|学习|一下|之前|以前|前|在|于|，|,|。|！|!|；|;|\s','',residue)
    require(not residue,'REQUEST_AMBIGUOUS','Additional or unsupported meaning must be clarified')
    occurred=timestamp(message['occurred_at']).astimezone(SHANGHAI)
    dates=re.findall(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日',body)
    relatives=re.findall(r'今天|明天|后天|(?:本)?周[一二三四五六日天]',body)
    require(len(dates)+len(relatives)<=1,'DEADLINE_AMBIGUOUS','Multiple dates need clarification')
    date=None;date_text=None
    if dates:
        year,month,day=dates[0]
        try:date=datetime(int(year) if year else occurred.year,int(month),int(day)).date()
        except ValueError:require(False,'DEADLINE_AMBIGUOUS','Invalid source date')
        require(bool(year) or date>=occurred.date(),'DEADLINE_AMBIGUOUS','Unspecified year cannot silently roll over')
        date_text=re.search(r'(?:\d{4}年)?\d{1,2}月\d{1,2}日',body).group()
    elif relatives:
        date_text=relatives[0]
        if date_text in {'今天','明天','后天'}:
            date=occurred.date()+timedelta(days={'今天':0,'明天':1,'后天':2}[date_text])
        else:
            weekday={'一':0,'二':1,'三':2,'四':3,'五':4,'六':5,'日':6,'天':6}[date_text[-1]]
            delta=weekday-occurred.weekday()
            date=occurred.date()+timedelta(days=delta if date_text.startswith('本') else delta%7)
    times=re.findall(r'(上午|下午|晚上|中午)?(\d{1,2}|[一二两三四五六七八九十]+)点(半|[0-9]{1,2}分)?',body)
    require(len(times)<=1,'DEADLINE_AMBIGUOUS','Multiple times need clarification')
    deadline={'precision':'date','value':date.isoformat(),'source_text':date_text,'meaning':'schedule'} if date else None
    due=None
    if times:
        require(date is not None,'DEADLINE_AMBIGUOUS','A time without an explicit date is not a deadline')
        meridiem,hour,minute=times[0];hour=number(hour)
        require(hour is not None and 0<=hour<=23,'DEADLINE_AMBIGUOUS','Invalid source hour')
        require(bool(meridiem) or hour>12,'DEADLINE_AMBIGUOUS','Morning/evening must not be guessed')
        if meridiem in {'上午','下午','晚上'}:
            require(1<=hour<=12,'DEADLINE_AMBIGUOUS','Inconsistent source meridiem')
        require(not (meridiem in {'上午','晚上'} and hour==12),
                'DEADLINE_AMBIGUOUS','Twelve in a morning/evening phrase needs clarification')
        if meridiem in {'下午','晚上'} and hour<12:hour+=12
        if meridiem=='中午':require(hour in {11,12,13},'DEADLINE_AMBIGUOUS','Ambiguous noon time')
        minute=30 if minute=='半' else int(minute[:-1]) if minute else 0
        require(0<=minute<60,'DEADLINE_AMBIGUOUS','Invalid source minute')
        exact=datetime.combine(date,datetime.min.time(),tzinfo=SHANGHAI).replace(hour=hour,minute=minute)
        deadline={'precision':'datetime','value':exact.isoformat(),
                  'source_text':body,'meaning':'schedule'}
        # A schedule time is not necessarily a promised completion deadline.
        if re.search(r'点(?:半|[0-9]{1,2}分)?(?:之|以)?前',body) and re.search(r'补完|读完|完成',body):
            due=exact.isoformat();deadline['meaning']='completion_deadline'
    if deadline:
        date_match=re.search(re.escape(date_text),body)
        time_match=re.search(r'(?:上午|下午|晚上|中午)?(?:\d{1,2}|[一二两三四五六七八九十]+)点(?:半|[0-9]{1,2}分)?(?:之前|以前|前)?',body)
        start,end=date_match.span()
        if time_match:start,end=min(start,time_match.start()),max(end,time_match.end())
        elif re.match(r'(?:之前|以前|前)',body[end:]):
            end+=len(re.match(r'(?:之前|以前|前)',body[end:]).group())
        # Preserve the exact source span, separately from parsed value/precision.
        deadline['source_text']=body[start:end]
        if deadline['source_text'].endswith(('前','以前','之前')) and re.search(r'补完|读完|完成',body):
            deadline['meaning']='completion_deadline'
    return {'day':number(days[0]),'requested_time':deadline,'due_at':due,
            'original_deadline':deadline['source_text'] if deadline else None}


def authoritative_binding(app,family,batch_id,day):
    options=[]
    for binding in app.store.list('course_binding',family):
        mappings=[entry for entry in binding.get('lesson_map') or [] if entry['day']==day]
        if len(mappings)==1 and binding.get('course_id') and binding.get('course_version'):
            options.append((binding,mappings[0]['lesson_ref']))
    require(len(options)==1 and options[0][0]['batch_id']==batch_id,
            'COURSE_BINDING_AMBIGUOUS','A uniquely confirmed current course/day binding is required')
    binding,lesson=options[0]
    require(binding.get('source_kind')=='trusted_host_confirmation','COURSE_BINDING_AMBIGUOUS','Binding must be host-confirmed')
    return binding,lesson


def propose_makeup(app,token,args):
    cmd=MakeupProposal.model_validate(args)
    def action(family,context):
        app.check_version(family,cmd.expected_family_version)
        message=app.store.get('message',cmd.source_message_id,family)
        require(message['direction']=='inbound' and message['version']==cmd.source_version,
                'EVIDENCE_SUPERSEDED','Source must be a current inbound message')
        require(cmd.source_excerpt==message['body'],'SOURCE_EXCERPT_INCOMPLETE','The complete source message is required')
        if context.get('lease'):
            require(cmd.source_message_id in context.get('reply_scope',{}),'REPLY_SCOPE','Request must belong to this run')
        intent_ids=[i['intent_id'] for i in message.get('intents',[]) if i['requires_reply'] and not i['reply_operation_refs']]
        require(message.get('interpreted') and len(intent_ids)==1,'REPLY_SCOPE','先解释唯一的待答补课安排意图')
        parsed=explicit_request(message)
        require(parsed['day']==cmd.day,'LESSON_SOURCE_MISMATCH','Proposed day must equal the literal source day')
        binding,lesson=authoritative_binding(app,family,cmd.batch_id,cmd.day)
        source_id='candidate-source:'+digest([family,'family-makeup',message['id']])
        pending_id='pending:'+digest([family,'family-makeup',message['id']])
        previous=next((r for r in app.store.list('candidate_source',family) if r['id']==source_id),None)
        if previous:
            require(previous['source_content_fingerprint']==digest(message_content(message)),
                    'IDEMPOTENCY_CONFLICT','Source content changed')
            # The original request_id replays its receipt before this callback.
            # A new request_id for identical source must not wake the family.
            raise BusinessError('REQUEST_ALREADY_RECORDED','Source is already recorded as '+pending_id)
        source={'source_id':source_id,'source_kind':'family_makeup_request','pending_id':pending_id,
            'rule_version':'explicit-positive-single-day-v1','enabled':True,'confirmed':True,
            'source_message_id':message['id'],'source_message_version':message['version'],
            'source_content_fingerprint':digest(message_content(message)),
            'batch_id':cmd.batch_id,'day':cmd.day,'binding_id':binding['id'],'binding_version':binding['version'],
            'due_at':parsed['due_at'],'requested_time':parsed['requested_time'],
            'original_deadline':parsed['original_deadline']}
        app.store.put('candidate_source',source_id,family,source)
        saved = app.store.put('pending',pending_id,family,{'source_message_id':message['id'],
            'source_version':message['version'],'request':message['body'],'original_deadline':parsed['original_deadline'],
            'requested_time':parsed['requested_time'],'done_work':[],'remaining_work':[lesson],
            'raised_at':message['occurred_at'],'disposition':'pending','event_id':None,
            'candidate_source_id':source_id,'source_kind':'host_verified_explicit_makeup',
            'completion_condition':None,'source_intent_id':intent_ids[0],
            'planning_requested':True,'await_decision':True,'decision_revision':1})
        from .admission import enqueue_request
        enqueue_request(app,family,saved)
        return saved
    return app.write(token,{'service'},'propose_makeup',cmd.model_dump(),action)


def derive_family_request(app,family,source):
    common={'source_kind':'family_makeup_request','source_refs':[], 'rule_version':source['rule_version'],
            'pending_id':source['pending_id'],'status':'blocked','gaps':[],'goal_key':None,'completion':None,
            'due_at':source['due_at'],'requested_due_at':source['due_at'],'delivery_parts':[],
            'requested_time':source['requested_time'],'original_deadline':source['original_deadline']}
    if not source['enabled']:
        return {**common,'gaps':['source_disabled']}
    try:
        message=app.store.get('message',source['source_message_id'],family)
    except BusinessError:
        return {**common,'gaps':['original_message_missing']}
    if digest(message_content(message))!=source['source_content_fingerprint']:
        return {**common,'gaps':['original_message_content_changed']}
    try:
        binding,lesson=authoritative_binding(app,family,source['batch_id'],source['day'])
    except BusinessError:
        return {**common,'gaps':['binding_missing_or_ambiguous']}
    if binding['id']!=source['binding_id'] or binding['version']!=source['binding_version']:
        return {**common,'gaps':['binding_changed_request_requires_review']}
    from .candidates import makeup_identity
    identity=makeup_identity(family,binding['course_id'],binding['course_version'],[lesson])
    return {**common,'status':'ready','goal_key':'makeup:'+digest(identity),'source_identity':identity,
        'goal_text':'补读已确认原课节 '+lesson,
        'completion':{'kind':'goal','lesson_refs':[lesson],'required_part_ids':[],'required_stage':'sent'},
        'source_refs':[{'kind':'message','id':source['source_message_id'],'version':source['source_message_version']},
                       {'kind':'course_binding','id':binding['id'],'version':binding['version']}],
        'source_fingerprint':digest([source,message_content(message),binding])}


SPECS={'pending.propose_makeup':({'service'},MakeupProposal,
       '提出家庭明确补课需求：引用完整原话和课日；宿主核验单一正向请求及唯一确认课节，模糊输入保留待确认；成功只登记待规划需求，不安排执行或未来通知，不提供主动联系许可')}
HANDLERS={'pending.propose_makeup':propose_makeup}
