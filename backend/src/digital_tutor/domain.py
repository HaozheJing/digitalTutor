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


class EventCreate(Strict):
    client_ref: ID
    goal_key: ID
    goal_text: Annotated[str, Field(min_length=1)]
    completion: Completion
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
    plan_refs: list[ID] = Field(default_factory=list)
    pending_decisions: list['PendingDecision'] = Field(default_factory=list)


class PendingDecision(Strict):
    input_id: ID
    disposition: Literal['linked', 'created', 'not_adopted', 'retained']
    event_ref: str | None = None
    reason: Annotated[str, Field(min_length=1)]


class ProgressCommit(Strict):
    request_id: ID
    expected_family_version: Positive
    report_id: ID
    revision: Positive
    event_updates: list[EventUpdate] = Field(min_length=1)


class Report(Strict):
    request_id: ID
    report_id: ID
    revision: Positive = 1
    event_ids: list[ID] = Field(min_length=1)
    facts: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    suggested_update: str = ''
    remaining_work: list[str] = Field(default_factory=list)
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
    requires_reply: bool = True


class Interpret(Strict):
    request_id: ID
    message_id: ID
    expected_version: Positive
    intents: list[Intent] = Field(min_length=1)


class ControlCommand(Strict):
    request_id: ID
    event_id: str | None = None
    action_kinds: list[Action] = Field(min_length=1)
    reason: Annotated[str, Field(min_length=1)]
    source_message_id: ID
    source_version: Positive
    release_control_id: str | None = None


class PendingInput(Strict):
    request_id: ID
    source_message_id: ID
    source_version: Positive
    request: Annotated[str, Field(min_length=1)]
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
