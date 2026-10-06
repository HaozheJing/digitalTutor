# native06 family_requests 独立语义终审

test15新运行：8 native / 54 API / 5实际sent。原程序通过；五维1/1/2/2/1，总7，来源及完整性未达2，限定语义不通过。不是沿用test13失败。

## 已闭合的工程与真实承接

两条需求实际成为一项goal、一项delivery；第一目标期限与第三文字请求的10月5日16:00前均保留。goal/null完成条件不等于已完成补读；delivery仅承接、尚未实际交付。本例没有执行后续步骤，不能借本例证明总结内容合格或补读已完成。

单一future意图的登记op52706…与正式安排确认opfb59…现在是两个实际sent，后者host decision_reply_refs包含原pending、decision revision/request、Event、commitment和原message/intent。其真实安排为10/5 09:00至16:00，故“9点后四点前发”有安排依据，不按旧版判无依据承诺。原future intent仍只有partial refs，未因确认送达结清。中间尝试resolves=true被DELIVERY_RESPONSIBILITY_DELEGATED拦下，修正false后实际发送；失败草稿不能计外发。

goal确认op206ed…实际sent并结清第一当前安排答复，但原goal保持pending。第二来信虽被模型当重复并答复，新增行动日期未被独立保留；这和前述dedup/guard闭合是两件事。

## 仍有问题

[
  {
    "priority": "P1",
    "type": "unsupported_activity_fact",
    "run_id": "run-deff36abb1fd4640a651f85a949cf678",
    "operation_id": "operation-206ed60b7e144cb1a3819ae50cc5b68c",
    "quote": "目前这项事项还在待完成状态，补读本身还没有进行",
    "basis": "实际承接/pending只能证明责任未结案，原学习快照incomplete也不能证明活动从未开始；家长未来补读请求没有明确陈述未进行。发送前state.read只有承接/消息/操作投影，没有实际未开始的来源。",
    "suggestion": "只表达事项待完成或尚未核实完成，不新增家庭活动缺席事实。"
  },
  {
    "priority": "P1",
    "type": "duplicate_request_time_responsibility",
    "source_message_id": "msg-bae278895c0b4e069b3c94e39034a9b0",
    "quote": "请帮我明天补读第1天。",
    "operation_id": "operation-01b852806aa643b09bd5392a10d5e01b",
    "basis": "被interpret为reply且resolves=true，没有pending/alias绑定；原事项仍仅before 10/5 16:00并在10/4 09:00有remind_makeup与active grant。首请求“明天下午四点前”是期限，第二请求“明天补读”增加行动日期。回复称同一请求并保留原期限，却未表达/澄清新行动日要求。没有实际执行提前提醒，不声称已提前联系。",
    "engineering_boundary": "原第一pair守卫正常；第二pair是模型对业务责任的分类/归并问题，不是已经绑定第二pair后宿主守卫失效。无需强制每消息一个pending，但归并须保留新增约束及来源。"
  },
  {
    "priority": "P2",
    "type": "expression",
    "quote": "event-369924db35a74295b23b429811264d09",
    "basis": "实际确认泄漏内部事项ID；“如果这个期限对您很重要”也属不必要措辞，不影响确认送达证据。"
  }
]

## 证据

83源码SHA与test15 source.zip一致；业务/渠道两个SQLite integrity_check=ok；17类records与业务库一致；5条渠道body与operation/outbound逐字一致。source-rows只是07:30合成快照；没有实际未开始活动记录。详见semantic-native06-remaining-evidence.json。

## 全部实际对话

### inbound / msg-2196a1c9dfc5446c908f4a87f424de3d / 2026-10-04T08:00:00+08:00

请安排明天下午四点前补完第1天。

### outbound / msg-1e21cf203bbd41339f33cceecb4b8751 / 2026-10-04T08:00:00+08:00

收到，第1天（课程 LESSON-L2-FOX-D01）的补做请求我已经按您说的"明天下午四点前"登记好了。目前这条请求还在等待安排，还没有确定具体的补做时间，安排好后我这边会有记录。如果这个期限对您很重要，也可以告诉我您明天哪个时间段方便，我把它一并记进这条请求里。

### inbound / msg-bae278895c0b4e069b3c94e39034a9b0 / 2026-10-04T08:00:00+08:00

请帮我明天补读第1天。

### outbound / msg-bc7b788b6433499dbfc6438dd0a66eaa / 2026-10-04T08:00:00+08:00

收到～这条和刚才那条是同一个请求。第1天（课程 LESSON-L2-FOX-D01）"明天（2026-10-05）下午四点前补读"的需求已经登记好了，目前还在等待安排，还没有确定具体的补做时间；有安排结果后我会跟您确认。您不用重复发，原期限我都保留着。

### inbound / msg-c2e5b7608eae43a6b961c6c7f385c090 / 2026-10-04T08:00:00+08:00

我还想要一份这周阅读情况的文字总结，10月5日下午四点前需要，先记下需求。

### outbound / msg-d94585c24ca14cbb8e1597995d60d515 / 2026-10-04T08:00:00+08:00

收到～这份"本周阅读情况文字总结"的需求我已经记下了，您说的"10月5日下午四点前需要"也原样保留在这条请求里。目前它还在等待安排，还没有确定的结果；有安排后我会跟您确认。另外，第1天（LESSON-L2-FOX-D01）"明天下午四点前补读"的请求也仍在等待安排中，两条我都记着，您不用重复发。

### outbound / msg-03803ed162b9499e9e4b47456e8fbf4c / 2026-10-04T08:00:00+08:00

跟您同步一下进展：第1天（LESSON-L2-FOX-D01）的补读请求已经正式登记为一项事项，期限按您说的保留为2026-10-05（明天）下午四点前。目前这项事项还在待完成状态，补读本身还没有进行，完成之前我会按这条事项继续核对。另外，"本周阅读情况文字总结"这条需求目前仍在等待安排，还没有确定的结果；有安排后我再跟您确认。

### outbound / msg-a13cf793fb5e4801b5cd775044de9287 / 2026-10-04T08:00:00+08:00

跟您确认一下：您要的"本周阅读情况文字总结"已经正式接纳安排了。我会在10月5日上午9点后、您要求的下午四点前，把这份文字总结发给您，内容只基于已经核实到的本周学习记录。这条需求现在由事项 event-369924db35a74295b23b429811264d09 跟踪，您不用重复提醒；到时间我会按这条履行。

## 当前责任原文

```json
{
  "inbound": [
    {
      "id": "msg-2196a1c9dfc5446c908f4a87f424de3d",
      "version": 5,
      "body": "请安排明天下午四点前补完第1天。",
      "direction": "inbound",
      "external_id": "family-request-0",
      "intents": [
        {
          "intent_id": "intent-makeup-d1",
          "partial_reply_operation_refs": [
            "operation-bf7ca864027547c28795e6b80fa34483",
            "operation-01b852806aa643b09bd5392a10d5e01b"
          ],
          "reply_operation_refs": [
            "operation-206ed60b7e144cb1a3819ae50cc5b68c"
          ],
          "requires_reply": true,
          "service_authorization": null,
          "service_kind": "reply",
          "text": "家长明确要求安排补完第1天，期望在此前一天（2026-10-05）下午四点前完成。"
        }
      ],
      "interpreted": true,
      "occurred_at": "2026-10-04T08:00:00+08:00",
      "received_at": "2026-10-04T08:00:00+08:00",
      "source_kind": "mock_wechat"
    },
    {
      "id": "msg-bae278895c0b4e069b3c94e39034a9b0",
      "version": 3,
      "body": "请帮我明天补读第1天。",
      "direction": "inbound",
      "external_id": "family-request-1",
      "intents": [
        {
          "intent_id": "intent-makeup-d1-again",
          "reply_operation_refs": [
            "operation-01b852806aa643b09bd5392a10d5e01b"
          ],
          "requires_reply": true,
          "service_authorization": null,
          "service_kind": "reply",
          "text": "家长再次请求明天补读第1天，与已登记的补做请求（msg-2196a1c9dfc5446c908f4a87f424de3d / intent-makeup-d1，pending:59e576f5c8e4bbfe475f50fecd676cc3af452c119bdcf12ec8da7a94f8c7025d）为同一事项。"
        }
      ],
      "interpreted": true,
      "occurred_at": "2026-10-04T08:00:00+08:00",
      "received_at": "2026-10-04T08:00:00+08:00",
      "source_kind": "mock_wechat"
    },
    {
      "id": "msg-c2e5b7608eae43a6b961c6c7f385c090",
      "version": 5,
      "body": "我还想要一份这周阅读情况的文字总结，10月5日下午四点前需要，先记下需求。",
      "direction": "inbound",
      "external_id": "family-request-2",
      "intents": [
        {
          "intent_id": "intent-weekly-summary",
          "partial_reply_operation_refs": [
            "operation-52706dccbb414752993db429f43dae4b",
            "operation-206ed60b7e144cb1a3819ae50cc5b68c",
            "operation-fb59bbc1e4bd4e79af6a9c8c153da119"
          ],
          "reply_operation_refs": [],
          "requires_reply": true,
          "service_authorization": "我还想要一份这周阅读情况的文字总结，10月5日下午四点前需要，先记下需求。",
          "service_kind": "future_delivery",
          "text": "家长要求以后交付一份本周阅读情况的文字总结，期望时间为10月5日下午四点前，并明确要求先记下该需求。"
        }
      ],
      "interpreted": true,
      "occurred_at": "2026-10-04T08:00:00+08:00",
      "received_at": "2026-10-04T08:00:00+08:00",
      "source_kind": "mock_wechat"
    }
  ],
  "pending": [
    {
      "id": "pending:59e576f5c8e4bbfe475f50fecd676cc3af452c119bdcf12ec8da7a94f8c7025d",
      "version": 2,
      "await_decision": false,
      "candidate_source_id": "candidate-source:59e576f5c8e4bbfe475f50fecd676cc3af452c119bdcf12ec8da7a94f8c7025d",
      "completion_condition": null,
      "decision_reason": "家长明确要求安排明天（2026-10-05）下午四点前补完第1天；本次admission只处理该绑定pending，按ready候选新建正式补读事项，保留原期限与课程身份LESSON-L2-FOX-D01。另一条weekly-summary pending不在本次范围，未处理。",
      "decision_request_id": "req-admission-run-9d24a315-makeup-d1",
      "decision_revision": 1,
      "decision_state": "created",
      "disposition": "created",
      "done_work": [],
      "event_id": "event-5ad35642bcab42cfaffdc67f10f590b7",
      "original_deadline": "明天下午四点前",
      "planning_requested": true,
      "raised_at": "2026-10-04T08:00:00+08:00",
      "remaining_work": [
        "LESSON-L2-FOX-D01"
      ],
      "request": "请安排明天下午四点前补完第1天。",
      "requested_time": {
        "meaning": "completion_deadline",
        "precision": "datetime",
        "source_text": "明天下午四点前",
        "value": "2026-10-05T16:00:00+08:00"
      },
      "source_intent_id": "intent-makeup-d1",
      "source_kind": "host_verified_explicit_makeup",
      "source_message_id": "msg-2196a1c9dfc5446c908f4a87f424de3d",
      "source_version": 2
    },
    {
      "id": "pending:cbf28ba4b3d056f6145b96d49bc018776683a7f0718745ae1ad8f4b44242603b",
      "version": 2,
      "await_decision": false,
      "candidate_source_id": "candidate-source:219c6b50f76978278f45dc033050cf1f793c4f5d411fd8a654fe1285ec8626a6",
      "completion_condition": null,
      "decision_reason": "本次admission只处理该绑定pending：家长要求记下并于10月5日下午四点前收到本周阅读情况文字总结；按ready候选新建正式文字交付事项，保留原期限、goal_key、completion与delivery_parts。",
      "decision_request_id": "req-admission-run-47f81ed5-weekly-summary",
      "decision_revision": 1,
      "decision_state": "created",
      "disposition": "created",
      "done_work": [],
      "event_id": "event-369924db35a74295b23b429811264d09",
      "original_deadline": "10月5日下午四点前需要",
      "planning_requested": true,
      "raised_at": "2026-10-04T08:00:00+08:00",
      "remaining_work": [
        "requested-text"
      ],
      "request": "家长希望收到一份本周阅读情况的文字总结，并要求先记下该需求。",
      "requested_time": {
        "end": "2026-10-05T16:00:00+08:00",
        "meaning": "before",
        "source_text": "10月5日下午四点前需要",
        "start": null
      },
      "source_intent_id": "intent-weekly-summary",
      "source_kind": "source_bound_service_interpretation",
      "source_message_id": "msg-c2e5b7608eae43a6b961c6c7f385c090",
      "source_version": 2
    }
  ],
  "events": [
    {
      "id": "event-5ad35642bcab42cfaffdc67f10f590b7",
      "version": 2,
      "accepted_commitment": {
        "authorized_action_kinds": [
          "remind_makeup",
          "verify_learning",
          "clarify_existing"
        ],
        "authorized_step_key": "6d2d4f6e5056025ce8084191316530204407f988679406d8474a1656b536f31d",
        "authorized_target_refs": [
          "LESSON-L2-FOX-D01"
        ],
        "candidate_ref": {
          "id": "candidate:f717d32f8d4baa78934f4543bcd8356e19de49a3f999725dbf5871ea54055386",
          "version": 2
        },
        "commit_request_id": "req-admission-run-9d24a315-makeup-d1",
        "id": "commitment:bdb32223a877048ee78953cc0c2b22c639a7d4b6cd0f624ff3d5d54bc79e3fd4",
        "requirement_revision": 1,
        "schedule_revision": 1,
        "source_message_digest": "04eb5d5ad98dcf54eb207c0f3bfc17fbdb56249349d5cb35a31de71d9579967a",
        "source_message_ref": {
          "id": "msg-2196a1c9dfc5446c908f4a87f424de3d",
          "version": 4
        },
        "source_pending_id": "pending:59e576f5c8e4bbfe475f50fecd676cc3af452c119bdcf12ec8da7a94f8c7025d",
        "status": "active",
        "valid_from": "2026-10-04T09:00:00+08:00",
        "valid_until": "2026-10-04T21:00:00+08:00"
      },
      "candidate_id": "candidate:f717d32f8d4baa78934f4543bcd8356e19de49a3f999725dbf5871ea54055386",
      "candidate_ref": {
        "id": "candidate:f717d32f8d4baa78934f4543bcd8356e19de49a3f999725dbf5871ea54055386",
        "version": 1
      },
      "completion": {
        "kind": "goal",
        "lesson_refs": [
          "LESSON-L2-FOX-D01"
        ],
        "required_part_ids": [],
        "required_stage": "sent"
      },
      "completion_condition": null,
      "completion_contract_version": 1,
      "completion_history": [],
      "creation_source_refs": [
        {
          "id": "msg-2196a1c9dfc5446c908f4a87f424de3d",
          "kind": "message",
          "version": 2
        },
        {
          "id": "course-binding:1aac169c6326f68e6a5a5278f50d657f73dd63d3ac2062ce67f0a0008bd86784",
          "kind": "course_binding",
          "version": 1
        }
      ],
      "deadline_history": [],
      "delivery_parts": [],
      "due_at": "2026-10-05T16:00:00+08:00",
      "evidence_refs": [],
      "goal_key": "makeup:9b210ec4803f6c07f9190c6fb79da39068f615bc3be8fd2ba07aa933fe432243",
      "goal_text": "补读已确认原课节 LESSON-L2-FOX-D01",
      "next_action": {
        "action_kind": "remind_makeup",
        "completion_condition": "家长确认收到补读提醒；提醒本身不代表孩子完成补读",
        "execute_at": "2026-10-04T09:00:00+08:00",
        "purpose": "在10月5日16:00截止前提醒家长安排孩子补读第1天（LESSON-L2-FOX-D01）",
        "target_refs": [
          "LESSON-L2-FOX-D01"
        ]
      },
      "original_deadline": "明天下午四点前",
      "proposed_change": null,
      "remaining_work": [
        "LESSON-L2-FOX-D01"
      ],
      "requested_time": {
        "meaning": "completion_deadline",
        "precision": "datetime",
        "source_text": "明天下午四点前",
        "value": "2026-10-05T16:00:00+08:00"
      },
      "requirement_revision": 1,
      "service_hint": null,
      "source_identity": {
        "course_id": "simulation-L2-fox",
        "course_version": "simulation-v1",
        "family_id": "synthetic-4b1-a",
        "kind": "makeup",
        "lesson_refs": [
          "LESSON-L2-FOX-D01"
        ]
      },
      "status": "pending",
      "uid": "synthetic-4b1-uid-a",
      "waiting": null
    },
    {
      "id": "event-369924db35a74295b23b429811264d09",
      "version": 2,
      "accepted_commitment": {
        "authorized_action_kinds": [
          "deliver_feedback",
          "verify_operation",
          "clarify_existing"
        ],
        "authorized_step_key": "d55c00ffe57626184ac5dc4e6f284f405c72ba796e71debef3082f13369fc293",
        "authorized_target_refs": [
          "requested-text"
        ],
        "candidate_ref": {
          "id": "candidate:dacc9cf9e159974b35f506512ee3eeefa24a863111e1334e60bde989e2214558",
          "version": 2
        },
        "commit_request_id": "req-admission-run-47f81ed5-weekly-summary",
        "id": "commitment:b058c9282724f8dea36d688b9d74f3bd3222a2aba8e57b57f7c9d1535af2ec79",
        "requirement_revision": 1,
        "schedule_revision": 1,
        "source_message_digest": "52f4efb0c070a912ee2d5bd79936ba3e67764d96867fc2ac1db52b1131958006",
        "source_message_ref": {
          "id": "msg-c2e5b7608eae43a6b961c6c7f385c090",
          "version": 4
        },
        "source_pending_id": "pending:cbf28ba4b3d056f6145b96d49bc018776683a7f0718745ae1ad8f4b44242603b",
        "status": "active",
        "valid_from": "2026-10-05T09:00:00+08:00",
        "valid_until": "2026-10-05T16:00:00+08:00"
      },
      "candidate_id": "candidate:dacc9cf9e159974b35f506512ee3eeefa24a863111e1334e60bde989e2214558",
      "candidate_ref": {
        "id": "candidate:dacc9cf9e159974b35f506512ee3eeefa24a863111e1334e60bde989e2214558",
        "version": 1
      },
      "completion": {
        "kind": "delivery",
        "lesson_refs": [],
        "required_part_ids": [
          "requested-text"
        ],
        "required_stage": "sent"
      },
      "completion_condition": null,
      "completion_contract_version": 1,
      "completion_history": [],
      "creation_source_refs": [
        {
          "id": "msg-c2e5b7608eae43a6b961c6c7f385c090",
          "kind": "message",
          "version": 2
        }
      ],
      "deadline_history": [],
      "delivery_parts": [
        {
          "media_type": "text",
          "part_id": "requested-text",
          "purpose": "家长希望收到一份本周阅读情况的文字总结，并要求先记下该需求。"
        }
      ],
      "due_at": "2026-10-05T16:00:00+08:00",
      "evidence_refs": [],
      "goal_key": "delivery:17d8ad2bb5a08d9fd29d54b8e0a67591d784269b95bb4c6c85e74b792a7a113a",
      "goal_text": "家长希望收到一份本周阅读情况的文字总结，并要求先记下该需求。",
      "next_action": {
        "action_kind": "deliver_feedback",
        "completion_condition": "本周阅读情况文字总结已通过微信操作实际发送（stage=sent）",
        "execute_at": "2026-10-05T09:00:00+08:00",
        "purpose": "在10月5日16:00前整理本周阅读情况文字总结并发送给家长，内容仅基于已核实的本周记录",
        "target_refs": [
          "requested-text"
        ]
      },
      "original_deadline": "10月5日下午四点前需要",
      "proposed_change": null,
      "remaining_work": [
        "requested-text"
      ],
      "requested_time": {
        "end": "2026-10-05T16:00:00+08:00",
        "meaning": "before",
        "source_text": "10月5日下午四点前需要",
        "start": null
      },
      "requirement_revision": 1,
      "service_hint": null,
      "source_identity": {
        "family_id": "synthetic-4b1-a",
        "intent_id": "intent-weekly-summary",
        "kind": "family_text_delivery",
        "message_id": "msg-c2e5b7608eae43a6b961c6c7f385c090"
      },
      "status": "pending",
      "uid": "synthetic-4b1-uid-a",
      "waiting": null
    }
  ]
}
```

普通回答事实问题留给用户共同评审；时间责任问题不能降为纯措辞。不改冻结源码、prompt、原program结果。