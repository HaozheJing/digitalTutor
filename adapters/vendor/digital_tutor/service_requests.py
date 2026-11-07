"""Source-bound family service requests, interpreted by service and admitted by planner."""
from pydantic import Field, model_validator
from typing import Literal
from .domain import Strict, ID, Positive, require, timestamp
from .storage import digest
from .report_contract import message_source_digest


class RequestedTime(Strict):
    meaning: Literal['at', 'before', 'window', 'unspecified']
    source_text: str
    start: str | None = Field(default=None, description='at为不得提前执行的约定时点，window为区间起点')
    end: str | None = Field(default=None, description='before/window的真实上界；at省略，重复同一时点会归一，不能用来制造交付截止')

    @model_validator(mode='after')
    def at_end_unambiguous(self):
        normalize_requested_time(self.model_dump())
        return self

    def normalized(self):
        return normalize_requested_time(self.model_dump())


def normalize_requested_time(value):
    """Normalize only redundant at.end; preserve true deadline/window meaning."""
    result=dict(value)
    if result.get('meaning')=='at':
        require(result.get('start'),'INVALID_TIME','约定时点缺少start')
        start=timestamp(result['start'])
        if result.get('end') is not None:
            require(timestamp(result['end'])==start,'INVALID_TIME','at含不同时刻的end，须明确原请求是时点、期限还是区间')
        result['end']=None
    return result


class ServiceRequest(Strict):
    request_id: ID
    expected_family_version: Positive
    source_message_id: ID
    source_version: Positive
    source_intent_id: ID
    source_excerpt: str = Field(min_length=1)
    request: str = Field(min_length=1, description='本次需要以后履行的文字服务需求；忠实于原意图，不能自创服务')
    requested_time: RequestedTime
    completion_condition: str | None = Field(default=None, description='新独立请求：明确给出的交付内容、结果或完成要求须保留原文，无说明才null。关联existing_event_id：比较本条与原合同；仅同义重申且无新增完成要求时null表示没有增量，宿主仍保留原completion_condition；有新增要求时用本条相应原文填写，不能漏掉或当已接纳。完整原话始终另存source_excerpt，null不删除原条件，也不表示该来源已经履行')
    existing_event_id: ID | None = Field(default=None, description='同一原事项填原ID；新独立需求才留空，不用改写需求逃避去重')


def propose_service(app, token, args):
    cmd = ServiceRequest.model_validate(args)
    def action(family, binding):
        app.check_version(family, cmd.expected_family_version)
        msg = app.store.get('message', cmd.source_message_id, family)
        require(msg['version'] == cmd.source_version and msg['direction'] == 'inbound',
                'EVIDENCE_SUPERSEDED', '须引用当前家庭原来信')
        require(msg['interpreted'] and cmd.source_excerpt == msg['body'], 'SOURCE_EXCERPT_INCOMPLETE', '须先解释并引用完整来信')
        intent = next((i for i in msg['intents'] if i['intent_id'] == cmd.source_intent_id), None)
        require(intent and intent['requires_reply'] and not intent['reply_operation_refs'], 'REPLY_SCOPE', '须属于真实待答请求意图')
        require(intent.get('service_kind','reply')=='future_delivery'
                and isinstance(intent.get('service_authorization'),str)
                and intent['service_authorization'].strip()
                and intent['service_authorization'] in msg['body'],
                'FUTURE_DELIVERY_NOT_AUTHORIZED', '仅接受原意图已解释并引用原文授权的未来文字交付；普通缺资料答疑保留原意图等待')
        if binding.get('run_id'):
            scope = binding.get('reply_scope', {}).get(msg['id'], [])
            require('*' in scope or cmd.source_intent_id in scope, 'REPLY_SCOPE', '请求超出本次授权')
        requested = cmd.requested_time.normalized()
        require(not requested['source_text'] or requested['source_text'] in msg['body'],
                'TIME_SOURCE_MISMATCH', '时间原文须实际存在')
        if requested['meaning'] != 'unspecified':
            require(requested['source_text'], 'TIME_SOURCE_MISMATCH', '时间须有原文依据')
        for key in ('start', 'end'):
            if requested[key]: timestamp(requested[key])
        require((requested['meaning'] != 'at' or bool(requested['start'])) and
                (requested['meaning'] != 'before' or bool(requested['end'])) and
                (requested['meaning'] != 'window' or bool(requested['start'] and requested['end'])),
                'INVALID_TIME', '时间解释缺必要边界')
        require(requested['meaning'] != 'unspecified' or not (requested['start'] or requested['end']),
                'INVALID_TIME', '无时间要求不能捏造精确时刻')
        if requested['start'] and requested['end']:
            require(timestamp(requested['start']) <= timestamp(requested['end']), 'INVALID_TIME', '时间区间颠倒')
        condition = (cmd.completion_condition or '').strip() or None
        require(condition is None or condition in msg['body'], 'COMPLETION_SOURCE_MISMATCH', '明确条件须来自原话；没有时留空')
        requested_condition = condition
        pid = 'pending:' + digest([family, msg['id'], cmd.source_intent_id])
        sid = 'candidate-source:' + digest([family, pid])
        require(not any(p['id'] == pid for p in app.store.list('pending', family)),
                'REQUEST_ALREADY_RECORDED', '同一原请求已登记；读取原决定，不重复接单')
        if cmd.existing_event_id:
            event = app.event(family, cmd.existing_event_id)
            require(event['completion']['kind'] == 'delivery' and event['status'] not in {'done','cancelled'},
                    'INVALID_TARGET', '原事项必须是未结束的文字交付')
            require(all(p['media_type'] == 'text' for p in event.get('delivery_parts', [])), 'MEDIA_UNAVAILABLE', '不能用文字替代其他媒体')
            identity = event.get('source_identity')
            require(identity, 'SOURCE_UNCONFIRMED', '原事项缺来源身份')
            completion, parts = event['completion'], event.get('delivery_parts', [])
            goal_key, goal_text = event['goal_key'], event['goal_text']
            condition = event.get('completion_condition')
        else:
            identity = {'kind':'family_text_delivery', 'family_id':family, 'message_id':msg['id'], 'intent_id':cmd.source_intent_id}
            completion = {'kind':'delivery','lesson_refs':[],'required_part_ids':['requested-text'],'required_stage':'sent'}
            parts = [{'part_id':'requested-text','media_type':'text','purpose':cmd.request}]
            goal_key, goal_text = 'delivery:' + digest(identity), cmd.request
        due = requested['end'] if requested['meaning'] in {'before','window'} else None
        source = {'source_id':sid,'source_kind':'family_service_request','pending_id':pid,
                  'rule_version':'source-bound-service-interpretation-v1','enabled':True,
                  'source_message_id':msg['id'],'source_message_version':msg['version'],
                  'source_message_digest':message_source_digest(msg),'source_intent_id':cmd.source_intent_id,
                  'requested_time':requested,'due_at':due,'original_deadline':requested['source_text'] or None,
                  'source_identity':identity,'goal_key':goal_key,'goal_text':goal_text,
                  'completion':completion,'delivery_parts':parts,'completion_condition':condition,
                  'requested_completion_condition':requested_condition,'requested_event_id':cmd.existing_event_id,
                  'source_request_text':cmd.request}
        app.store.put('candidate_source',sid,family,source)
        saved = app.store.put('pending',pid,family,{'source_message_id':msg['id'],'source_version':msg['version'],
            'source_intent_id':cmd.source_intent_id,'request':cmd.request,'original_deadline':source['original_deadline'],
            'requested_time':requested,'completion_condition':condition,'done_work':[],
            'requested_completion_condition':requested_condition,'requested_event_id':cmd.existing_event_id,
            'remaining_work':completion['required_part_ids'],'raised_at':msg['occurred_at'],
            'disposition':'pending','event_id':None,'candidate_source_id':sid,
            'source_kind':'source_bound_service_interpretation','planning_requested':True,
            'await_decision':True,'decision_revision':1})
        from .admission import enqueue_request
        enqueue_request(app,family,saved)
        return saved
    return app.write(token, {'service'}, 'propose_service', cmd.model_dump(), action)


def derive_service_request(app, family, source):
    msg = app.store.get('message',source['source_message_id'],family)
    value = {k:source.get(k) for k in ('source_kind','rule_version','pending_id','goal_key','goal_text',
             'source_identity','completion','delivery_parts','completion_condition','due_at','original_deadline','requested_time',
             'requested_completion_condition','requested_event_id','source_request_text')}
    valid = source['enabled'] and message_source_digest(msg) == source['source_message_digest']
    gaps=[] if valid else ['original_request_changed']
    differences=[]
    requested=source.get('requested_completion_condition')
    if source.get('requested_event_id') and requested is not None and requested!=source.get('completion_condition'):
        gaps.append('supplemental_completion_condition_requires_review')
        differences.append({'field':'completion_condition','effective':source.get('completion_condition'),
                            'requested':requested,'source_message_id':msg['id']})
    return {**value,'status':'blocked' if gaps else 'ready','gaps':gaps,'constraint_differences':differences,
            'source_refs':[{'kind':'message','id':msg['id'],'version':source['source_message_version']}]}


SPECS = {'pending.propose_service': ({'service'}, ServiceRequest,
    '仅登记原意图service_kind=future_delivery且有真实service_authorization的未来文字服务，保留原时间/完成条件并等待Planner；普通答疑缺资料仍留原意图等待，不调用本工具。登记不等于正式接纳')}
HANDLERS = {'pending.propose_service': propose_service}
