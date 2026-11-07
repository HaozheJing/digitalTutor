"""Strict command schemas. Trusted family/role bindings are not model arguments."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHANGHAI = timezone(timedelta(hours=8))
ID = Annotated[str, Field(min_length=1, max_length=160)]
Positive = Annotated[int, Field(strict=True, ge=1)]
Action = Literal['reply', 'clarify_existing', 'remind_makeup', 'deliver_feedback']
Status = Literal['pending', 'in_progress', 'waiting', 'done', 'cancelled']


class BusinessError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def require(condition, code, message):
    if not condition:
        raise BusinessError(code, message)


def timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise BusinessError('INVALID_TIME', '时间必须是带时区的ISO格式') from None
    require(parsed.tzinfo is not None, 'INVALID_TIME', '时间缺少时区')
    return parsed


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('completion_condition', check_fields=False)
    @classmethod
    def optional_condition(cls, value):
        return value.strip() or None if isinstance(value,str) else value


class Ref(Strict):
    kind: Literal['message', 'control', 'operation', 'evidence', 'learning_query'] = Field(description='learning_query仅证明实际SQL读取及当时返回的快照，不是lesson_completed完成证据。只复制实际工具回执的kind/id/version，不把course_binding、candidate_source或event改标为evidence。')
    id: ID
    version: Positive


class Waiting(Strict):
    reason: Annotated[str, Field(min_length=1)]
    resume_condition: Annotated[str, Field(min_length=1, description='重新核查原责任的恢复信号；不得因额外信息缺口新增原完成合同没有要求的先决条件。原合同可接受的有效事实仍须能进入复核，未确认的按时性或其他属性分别保留未知。')]
    review_at: str | None = None

    @field_validator('review_at')
    @classmethod
    def time_valid(cls, value):
        if value is not None:
            timestamp(value)
        return value


class Completion(Strict):
    kind: Literal['goal', 'delivery']
    lesson_refs: list[ID] = Field(default_factory=list)
    required_part_ids: list[ID] = Field(default_factory=list)
    required_stage: Literal['sent', 'delivered'] = 'sent'

    @model_validator(mode='after')
    def valid(self):
        if self.kind == 'goal' and (not self.lesson_refs or self.required_part_ids):
            raise ValueError('goal requires lesson_refs only')
        if self.kind == 'delivery' and (not self.required_part_ids or self.lesson_refs):
            raise ValueError('delivery requires required_part_ids only')
        for values in (self.lesson_refs, self.required_part_ids):
            if len(values) != len(set(values)):
                raise ValueError('duplicate completion target')
        return self


class Step(Strict):
    action_kind: Literal['verify_learning', 'verify_operation', 'clarify_existing', 'remind_makeup', 'deliver_feedback']
    target_refs: list[ID] = Field(min_length=1, description='仅原completion中的目标：goal使用lesson_refs，delivery使用required_part_ids；不要填事项号、候选号、计划号或交付所参考的课节号。')
    purpose: Annotated[str, Field(min_length=1)]
    execute_at: str | None = Field(default=None, description='本步骤最早执行的业务时刻，含时区；不同于事项截止，不在purpose里暗藏时间')
    completion_condition: str | None = Field(default=None, description='本动作完成条件；可空，空时Service根据实际结果自主判断，不改变父事项完成标准。verify_learning的动作完成是完成有据核查并报告已知与未知，不要求父学习目标先完成；未知日期不自动成为新的完成或恢复门槛')

    @field_validator('execute_at')
    @classmethod
    def execute_time_valid(cls, value):
        if value is not None:
            timestamp(value)
        return value


class DeliveryPart(Strict):
    part_id: ID
    media_type: Literal['text'] = 'text'
    purpose: str = Field(min_length=1)


class CandidateDecision(Strict):
    candidate_id: ID
    expected_version: Positive
    disposition: Literal['created','linked','retained','not_adopted']
    event_ref: ID | None = Field(default=None, description=(
        'created必须填写本次new_events中对应项的client_ref，且该项candidate_id与本决定一致；'
        'linked填写候选的原existing_event_id，或同目标已在本次创建项的client_ref；'
        'retained/not_adopted必须为null。不能用pending_decisions.event_ref代替此字段。'))
    reason: str = Field(min_length=1)


class EventCreate(Strict):
    service_action_id: ID | None = Field(default=None, description="适用的主动服务动作编号S01-S10/I01-I05/H01-H13；绑定固定完成判准，不能借此改变原目标范围。")
    service_cycle_end_at: str | None = Field(default=None, description="有明确来源的本轮结束时刻；未确认则省略并保留待配置，不自行延长。")
    service_slot_parts: dict[str,str] | None = Field(default=None, description="已有可信candidate来源时通常省略本字段，由宿主自动绑定固定槽位；不要用part_id猜测或改写槽位名。S10 weekly_package由宿主自动映射weekly_review、next_schedule、next_preview、applicable_arrangement四项。")
    client_ref: ID
    candidate_id: ID | None = Field(default=None,description='新建正式事项绑定的本run ready候选ID；strict运行必须填写')
    delivery_parts: list[DeliveryPart] = Field(default_factory=list)
    goal_key: ID
    goal_text: Annotated[str, Field(min_length=1)]
    completion: Completion
    completion_condition: str | None = Field(default=None, description='来源提供的事项完成说明；空时由Service自主评估并记录依据，不清除completion硬条件')
    status: Literal['pending', 'waiting'] = 'pending'
    waiting: Waiting | None = None
    due_at: str | None = None
    next_action: Step | None = None

    @field_validator('due_at')
    @classmethod
    def time_valid(cls, value):
        if value is not None:
            timestamp(value)
        return value

    @model_validator(mode='after')
    def state_valid(self):
        if (self.status == 'waiting') != (self.waiting is not None):
            raise ValueError('waiting status and waiting details must agree')
        return self


class RescheduleTime(Strict):
    meaning: Literal['schedule', 'completion_deadline']
    precision: Literal['date', 'datetime']
    value: str = Field(min_length=1)
    source_text: str = Field(min_length=1, description='原入站中表示所提时间的连续原文，保留精度')

    @model_validator(mode='after')
    def valid_time(self):
        if self.precision == 'datetime':
            timestamp(self.value)
        else:
            from datetime import date
            date.fromisoformat(self.value)
        return self


class RescheduleProposal(Strict):
    source_message_id: ID
    requested_time: RescheduleTime = Field(description='原文明示的新完成截止用completion_deadline；同时有动作时刻时将动作放next_action.execute_at，不能漏截止。只有行动日期或时刻、无新增截止时用schedule并保留原due_at。')
    next_action: Step = Field(description='原对象的新动作及具体执行时刻；原话同时指定动作时刻和完成截止时，分别保留execute_at与requested_time，不将二者混为一个时刻')


class Change(Strict):
    status: Status | None = None
    waiting: Waiting | None = None
    next_action: Step | None = None
    service_hint: str | None = None
    propose_change: RescheduleProposal | None = Field(default=None, description='依据本报告原入站提出原事项时间变更，尚未生效；须实际送达提议、有效接受及后续progress消费')
    proposed_due_at: str | None = None
    accept_change_id: str | None = None

    @field_validator('status', 'proposed_due_at', 'accept_change_id', 'propose_change')
    @classmethod
    def not_null_if_set(cls, value):
        if value is None:
            raise ValueError('omit this field instead of null')
        return value

    @field_validator('proposed_due_at')
    @classmethod
    def time_valid(cls, value):
        timestamp(value)
        return value


class EventUpdate(Strict):
    event_id: ID
    expected_version: Positive
    changes: Change
    evidence_refs: list[Ref] = Field(default_factory=list)


class DailyCommit(Strict):
    first_lesson_check_at: str | None = Field(default=None,description="仅首日基线规划按first_lesson_check_policy安排检查，填写当日19:00含时区的完整时间；这是内部核验任务，不是高光交付任务。")
    request_id: ID
    business_date: str
    expected_family_version: Positive
    new_events: list[EventCreate] = Field(default_factory=list)
    event_updates: list[EventUpdate] = Field(default_factory=list)
    plan_refs: list[ID] = Field(default_factory=list,description='按今日优先级排列的事项引用：已有event_id或本次new_events.client_ref，不能填写plan_id/candidate_id；空表示今日不选主动成员，不删除责任')
    pending_decisions: list['PendingDecision'] = Field(default_factory=list)
    candidate_decisions: list[CandidateDecision] = Field(default_factory=list,
        description='精确覆盖本次candidate_decision_scope.required_candidate_refs；source_gaps不提交决定，required集合为空时填空列表')


class PendingDecision(Strict):
    input_id: ID
    disposition: Literal['linked', 'created', 'not_adopted', 'retained']
    event_ref: str | None = None
    reason: Annotated[str, Field(min_length=1)]


class AdmissionCommit(DailyCommit):
    pending_id: ID
    expected_plan_revision: Annotated[int, Field(strict=True, ge=0)]


class ProgressCommit(Strict):
    request_id: ID
    expected_family_version: Positive
    report_id: ID
    revision: Positive
    event_updates: list[EventUpdate] = Field(min_length=1)


class CompletionAssessment(Strict):
    event_id: ID
    complete: bool
    criterion: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    evidence_refs: list[Ref] = Field(min_length=1)


class Report(Strict):
    request_id: ID
    report_id: ID
    revision: Positive = 1
    event_ids: list[ID] = Field(min_length=1)
    facts: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1, description='仅记录来源直接支持的观察及其时点、对象和范围。没有回传、没有记录或未核到证据只说明信息缺口，不能推定家庭尚未行动或活动没有发生。工具已执行与家庭行为分别陈述；不把猜测写成事实。')
    suggested_update: str = ''
    remaining_work: list[str] | None = Field(default=None, description='null或省略表示本轮未判断，不等于全部完成；[]只表示Service判断无剩余，最终仍核原条件及证据')
    completion_assessments: list[CompletionAssessment] = Field(default_factory=list, description='Service对原事项的有据判断；事项条件为空时自主确定判断依据，不能把当前动作完成当父目标完成')
    evidence_refs: list[Ref] = Field(min_length=1)
    supersedes_revision: int | None = None


class TranscriptSource(Strict):
    original_body: Annotated[str, Field(max_length=30000)]
    source_ids: list[ID] = Field(min_length=1)
    normalization_id: ID
    confidence: Literal['high', 'medium', 'unchanged']
    input_form: Literal['text', 'speech_transcript', 'transcription_like', 'unknown'] = 'unknown'


class MessageSourceMetadata(Strict):
    source_id: ID
    source_alias: ID
    source_row: Annotated[int, Field(strict=True, ge=1)]
    source_sha256: Annotated[str, Field(pattern='^[0-9a-f]{64}$')]
    speaker_role: Literal['teacher','parent','child','family_member_unknown','system']
    media_status: Literal['text','image_missing','media_unavailable','unknown'] = 'text'
    transcript: TranscriptSource | None = Field(default=None, description='宿主保留的转写原文、整理版本和置信度；transcription_like仅表示推测，不能据此认定孩子原先写错。')


class Inbound(Strict):
    external_id: ID
    body: Annotated[str, Field(min_length=1, max_length=30000)]
    occurred_at: str
    source_metadata: MessageSourceMetadata | None = Field(default=None, description='仅宿主渠道提供的来源、说话人及媒体元数据；不授予成员权限')

    @field_validator('occurred_at')
    @classmethod
    def time_valid(cls, value):
        timestamp(value)
        return value


class Intent(Strict):
    intent_id: ID
    text: Annotated[str, Field(min_length=1)]
    source_excerpt: str | None = Field(default=None, min_length=1, description='本message_id正文中的连续原文，不是其他消息的内容。一次给同一消息登记多个意图时逐个必填；跨消息须分别调用message.interpret。单意图省略时宿主绑定本条完整原文。')
    requires_reply: bool = Field(default=True, description='是否仍欠本意图的回应或履行；future_delivery必须true，即使约定以后发送也仍欠履行。该字段不授权立即发送')
    service_kind: Literal['reply', 'future_delivery'] = Field(default='reply', description='普通提问、补读安排及其安排确认均为reply；明确要求以后履行独立文字交付（含重复、重申或补充同一交付）为future_delivery。只查询文字交付的安排状态不是新交付委托。未来补读日期、待完成学习目标或缺资料本身也不构成文字交付委托')
    service_authorization: str | None = Field(default=None, description='future_delivery须引用家长原消息中明确要求未来交付的连续非空原文；reply留空。不是模型的补资料计划或将来承诺')


class Interpret(Strict):
    request_id: ID
    message_id: ID = Field(description='精确复制本轮微信读取回执中待解释消息的id；不能拼接或猜测，不能用run_id、request_id、source_id、external_id代替。')
    expected_version: Positive
    intents: list[Intent] = Field(min_length=1)
    append_new_intents: bool = Field(default=False, description='补登记同一原消息遗漏的独立意图时为true，只传新intent_id，已有意图完全保留。仅该来源尚未被回复、控制、需求或报告使用时允许；不能覆盖、撤回、改授权或复制已有意图。优先首次一次登记本条全部意图。')


class ControlCommand(Strict):
    request_id: ID
    event_id: ID | None = Field(default=None, description='省略或null表示全家庭所列动作；指定非空事项ID仅限该事项。空字符串/纯空白无效')
    action_kinds: list[Action] = Field(min_length=1)
    reason: Annotated[str, Field(min_length=1)]
    source_message_id: ID
    source_version: Positive
    release_control_id: str | None = None
    temporal_scope: Literal['source_day', 'until', 'indefinite'] = Field(default='indefinite', description='今天暂停用source_day；到指定时刻用until和valid_until；持续暂停用indefinite。旧调用默认持续，不能从reason猜解除。')
    valid_until: str | None = None

    @field_validator('valid_until')
    @classmethod
    def control_expiry(cls, value):
        if value is not None:
            timestamp(value)
        return value

    @field_validator('event_id')
    @classmethod
    def nonblank_event_scope(cls, value):
        if value is not None and not value.strip():
            raise ValueError('event_id must be a nonblank ID or null for family scope')
        return value


class PendingInput(Strict):
    request_id: ID
    source_message_id: ID
    source_version: Positive
    request: Annotated[str, Field(min_length=1)]
    completion_condition: str | None = None
    original_deadline: str | None = Field(default=None, description='保留用户原始期望期限；登记不表示接受该交付期限或已安排执行')
    done_work: list[str] = Field(default_factory=list)
    remaining_work: list[str] = Field(min_length=1)


class MessageRead(Strict):
    mode: Literal['all', 'latest', 'since'] = 'all'
    limit: Annotated[int, Field(strict=True, ge=1, le=1000)] = 1
    after_seq: Annotated[int, Field(strict=True, ge=0)] = 0
    direction: Literal['all', 'inbound', 'outbound'] = 'all'


class MessageReadAll(Strict):
    direction: Literal['all', 'inbound', 'outbound'] = 'all'


class MessageReadStream(Strict):
    scope: Literal['current_day','all_history'] | None = Field(default=None,description='daily_native工作流默认current_day，只读取当前业务日；历史原文优先resource.read检索，显式all_history可逐页遍历全部。')
    after_seq: Annotated[int, Field(strict=True, ge=0)] = 0
    limit: Annotated[int, Field(strict=True, ge=1, le=1000)] = 100
    direction: Literal['all', 'inbound', 'outbound'] = 'all'


class ReplyRef(Strict):
    message_id: ID = Field(description='本轮微信读取回执中这条来源消息的真实id；不是运行编号、请求编号或回放source_id。')
    intent_id: ID
    response_excerpt: str | None = Field(default=None, min_length=1, description='本次正文中实际回应此意图的连续原文。多消息回复中的部分回应必须提供；仅参考过的旧问题放context_refs，不登记为部分回复。')
    resolves_intent: bool = Field(default=True, description=(
        '逐个意图判断本次是否已有完整、有依据的实质答复。安排已正式确定且本次完整确认该安排时可true；学习目标是否完成另行判断，不能捆绑。独立文字交付的确认仍不代替实际交付。true只用于该意图已答，'
        '或已清楚拒绝确实越权/不应提供的请求。缺适用正文、政策、必读规范，'
        '暂不能确认，或让对方到别处核实，都只是缺口说明，必须false并保留原未答；'
        '缺资料不属于权限拒绝。一次发送涉及多个意图时分别判断，'
        '不能因其中一项已答或说明已送达就结清其他缺依据的意图。'))


class ProposalRef(Strict):
    event_id: ID
    change_id: ID


class Send(Strict):
    media_id: ID | None = Field(default=None,description='引用media.generate_audio/image返回的媒体ID。图片body须等于caption，卡片文字随媒体交付；语音body须等于原讲稿。failed不可当成品发送，placeholder不能声称真实媒体已生成。')
    retry_operation_id: ID | None = Field(default=None, description='仅可信终局未执行的原交付part重试：原失败已报告并由Progress消费后，在当前正常step能力内引用原operation，正文与part保持完全相同；unknown、普通failed、已sent不能重发。内部no_contact不获授权。')
    request_id: ID
    expected_family_version: Positive
    body: Annotated[str, Field(min_length=1, max_length=10000, description='实际外发正文。直接解决当前请求，同一事实表达一次，核对日期与时刻避免相对时间复述矛盾；未找到依据只代表未知，不推定心理或来源。只陈述有据事实与已执行结果；未来动作须有已确认安排。假设例子明确标为假设；不把参与或表达表现当学习诊断，不替家长补写未提供的孩子表现；需要引用期限时保留原日期、时刻和精度；局部活动结束不等于满足原事项完成条件，不能以建议改变原标准。')]
    action_kind: Action
    event_id: str | None = Field(default=None, description='仅主动事项动作使用。普通答疑或安排确认用action_kind=reply和reply_to，必须省略event_id、part_id、step_id，即使确认的是已创建事项。主动交付反之省略reply_to。')
    part_id: str | None = None
    step_id: str | None = None
    reply_to: list[ReplyRef] = Field(default_factory=list)
    context_refs: list[ID] = Field(default_factory=list, description='仅作为上下文参考的消息ID，不获得发送权限、不登记已答或部分回复、不修改其版本。')
    proposal_ref: ProposalRef | None = Field(default=None, description='仅附当前原事项提议的实际外发快照，不增加回复或主动触达资格；正文须忠实表达提议而非已生效安排')


class Query(Strict):
    sql: Annotated[str, Field(min_length=1, max_length=20000, description='只读SQL。用户或来源记录提供的筛选值必须使用 :参数名 占位符，通过parameters绑定，不拼接为SQL字面量；选择和比较实际字段类型。')]
    parameters: dict[str, str | int | float | None] = Field(default_factory=dict, description='SQL命名参数的值映射，键不含冒号；每个动态筛选值在此提供，不能只把值写进SQL。无需动态值的查询可用空对象。')
    max_rows: Annotated[int, Field(strict=True, ge=1, le=5000)] = 1000


class OperationRead(Strict):
    operation_id: ID


class Receipt(Strict):
    request_id: ID
    operation_id: ID
    stage: Literal['sent', 'delivered', 'unknown', 'failed', 'accepted']


DailyCommit.model_rebuild()

AdmissionCommit.model_rebuild()
