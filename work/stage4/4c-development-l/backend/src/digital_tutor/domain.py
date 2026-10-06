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
    kind: Literal['message', 'control', 'operation', 'evidence']
    id: ID
    version: Positive


class Waiting(Strict):
    reason: Annotated[str, Field(min_length=1)]
    resume_condition: Annotated[str, Field(min_length=1)]
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
    target_refs: list[ID] = Field(min_length=1)
    purpose: Annotated[str, Field(min_length=1)]
    execute_at: str | None = Field(default=None, description='本步骤最早执行的业务时刻，含时区；不同于事项截止，不在purpose里暗藏时间')
    completion_condition: str | None = Field(default=None, description='本动作完成条件；可空，空时Service根据实际结果自主判断，不改变父事项完成标准')

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
    event_ref: ID | None = None
    reason: str = Field(min_length=1)


class EventCreate(Strict):
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


class Change(Strict):
    status: Status | None = None
    waiting: Waiting | None = None
    next_action: Step | None = None
    service_hint: str | None = None
    proposed_due_at: str | None = None
    accept_change_id: str | None = None

    @field_validator('status', 'proposed_due_at', 'accept_change_id')
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
    facts: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    suggested_update: str = ''
    remaining_work: list[str] | None = Field(default=None, description='null或省略表示本轮未判断，不等于全部完成；[]只表示Service判断无剩余，最终仍核原条件及证据')
    completion_assessments: list[CompletionAssessment] = Field(default_factory=list, description='Service对原事项的有据判断；事项条件为空时自主确定判断依据，不能把当前动作完成当父目标完成')
    evidence_refs: list[Ref] = Field(min_length=1)
    supersedes_revision: int | None = None


class Inbound(Strict):
    external_id: ID
    body: Annotated[str, Field(min_length=1, max_length=30000)]
    occurred_at: str

    @field_validator('occurred_at')
    @classmethod
    def time_valid(cls, value):
        timestamp(value)
        return value


class Intent(Strict):
    intent_id: ID
    text: Annotated[str, Field(min_length=1)]
    requires_reply: bool = Field(default=True, description='是否仍欠本意图的回应或履行；future_delivery必须true，即使约定以后发送也仍欠履行。该字段不授权立即发送')
    service_kind: Literal['reply', 'future_delivery'] = Field(default='reply', description='普通提问/缺资料仍reply；仅家长明确要求以后履行独立文字交付时future_delivery，不因当前无法回答而升级')
    service_authorization: str | None = Field(default=None, description='future_delivery须引用家长原消息中明确要求未来交付的连续非空原文；reply留空。不是模型的补资料计划或将来承诺')


class Interpret(Strict):
    request_id: ID
    message_id: ID
    expected_version: Positive
    intents: list[Intent] = Field(min_length=1)


class ControlCommand(Strict):
    request_id: ID
    event_id: ID | None = Field(default=None, description='省略或null表示全家庭所列动作；指定非空事项ID仅限该事项。空字符串/纯空白无效')
    action_kinds: list[Action] = Field(min_length=1)
    reason: Annotated[str, Field(min_length=1)]
    source_message_id: ID
    source_version: Positive
    release_control_id: str | None = None

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


class ReplyRef(Strict):
    message_id: ID
    intent_id: ID
    resolves_intent: bool = Field(default=True, description=(
        '逐个意图判断本次是否已有完整、有依据的实质答复。true只用于该意图已答，'
        '或已清楚拒绝确实越权/不应提供的请求。缺适用正文、政策、必读规范，'
        '暂不能确认，或让对方到别处核实，都只是缺口说明，必须false并保留原未答；'
        '缺资料不属于权限拒绝。一次发送涉及多个意图时分别判断，'
        '不能因其中一项已答或说明已送达就结清其他缺依据的意图。'))


class Send(Strict):
    request_id: ID
    expected_family_version: Positive
    body: Annotated[str, Field(min_length=1, max_length=10000, description='实际外发正文。直接解决当前请求，同一事实表达一次，核对日期与时刻避免相对时间复述矛盾；未找到依据只代表未知，不推定心理或来源。只陈述有据事实与已执行结果；未来动作须有已确认安排。假设例子明确标为假设；不把参与或表达表现当学习诊断，不替家长补写未提供的孩子表现；需要引用期限时保留原日期、时刻和精度；局部活动结束不等于满足原事项完成条件，不能以建议改变原标准。')]
    action_kind: Action
    event_id: str | None = None
    part_id: str | None = None
    step_id: str | None = None
    reply_to: list[ReplyRef] = Field(default_factory=list)


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
