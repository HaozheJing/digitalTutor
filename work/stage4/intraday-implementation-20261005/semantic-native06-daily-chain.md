# native06 daily-chain 独立语义终审

test15新闭合运行：16 native / 111 API / 2实际sent。五维2/2/2/2/1，总9，限定本例语义通过；不代签4B/4C/4D总关口。7份报告逐份复核，全部applied。

## 真实null条件与完成链

原候选及两个Event的completion_condition始终null。Service并未被迫补必填字段，而是引用原goal.lesson_refs或delivery.required_part_ids/required_stage形成有据assessment。提醒sent后的goal评估false，只有可信源变completed并带usable_for_completion的证据后才评true；文字delivery依据真实learning-feedback sent评true，progress消费后done。

20:00的source-completion以synthetic-live-progress-1最新覆盖及宿主lesson_map/可信证据为依据。次日09:00源更正使旧证据valid=false，review读取新incomplete批次与更正证据，报告false，progress在原Event恢复责任；原ID、goal和截止未替换。第二天daily恢复，只纳入仍需核验的原goal，未再创建同目标。两个日期各一份baseline plan。

## 日期范围与实际反馈

新回复开头称“本周的学情反馈”表示当前周期的反馈交付，正文明确数据截至10/4 07:30、三条没有可核对学习日期、“不能确认它们都发生在本周内”。这不是先把三条宣称本周学习再靠泛泛免责抵消，而是明确区分当前反馈与记录发生日期；旧版周期间误归属本次没有复现。原合同是据本周可用学情作简短反馈，允许如实报告日期未知，不虚构已发生的本周学习。

字段均来自实际SQL：day1 attended/incomplete/not_submitted，day2 attended/completed/submitted，day3 attended/unknown/unknown；not_checked与not_configured如实表达。不宣称理解提升或当前家庭原因。无未来主动通知保证；“如需核对细节随时告诉我”是邀请新提问，不是承诺未经安排的自动联系。

## 7份报告逐项

1. 10:00提醒：有真实sent，assessment=false，剩余goal保留。
2. 10:00文字反馈：有真实part sent，assessment=true；未把goal顺带结清。
3. 17:30核实：读取原synthetic-4b1-snapshot，说明无新增家长回复，未新增外发，false。
4. 18:00核实：继续保留原课节责任、未确认完成；没有谎报已经补读或修改截止。查询时刻不等于快照覆盖，后续20:00报告明确旧批覆盖仅07:30；不从旧字段推导具体逾期学习行为。
5. 20:00可信源完成：新批次、映射、有效evidence齐备，true，不把homework unknown附加为新完成条件。
6. 次日09:00更正review：旧证据失效与新批incomplete均核对，false，原责任恢复，无新外发。
7. 次日09:00步骤核实：同一最新覆盖仍incomplete，false，后续只安排核实，不制造新的补读完成。

## P2及判断边界

[
  {
    "priority": "P2",
    "operation_id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
    "quote": "第1课 LESSON-L2-FOX-D01 还没有补读",
    "basis": "本轮有可信补读候选、原未结清goal与截止，语境是在提醒尚待完成责任；没有像family确认那样明确声称活动尚未进行。建议更精确限定“尚未确认补读完成”，避免被理解为实时活动事实；不据这句话额外判定孩子完全没开始。"
  },
  {
    "priority": "P2",
    "quote": "原截止 20-04 18:00 前，实际为 2026-10-04 18:00",
    "basis": "17:30内部report剩余说明有冗余错误日期片段，紧接明确正确日期，Event原due_at始终10/4 18:00未改。不构成实质改约。"
  }
]

## 独立核验

83源码SHA与test15 source.zip一致；双SQLite integrity_check=ok；final全部16类records与业务库相同；2条独立channel sent body逐字对应operation/outbound。没有knowledge/Skill正文教学，本例不证明正式Skill方法语义。原summary全部true保留。

## 全部实际外发

### outbound / msg-ebb19ebcf3fb446da7717adbcff8a4ed / 2026-10-04T10:00:00+08:00

家长您好，提醒一下：第1课 LESSON-L2-FOX-D01 还没有补读，需要在今天 18:00 截止前完成。您看孩子什么时间方便，安排补读就可以；有问题随时告诉我。

### outbound / msg-a6d599c2f5a146eda72d9387df6578fe / 2026-10-04T10:00:00+08:00

家长您好，这是本周的学情反馈（数据截至10月4日早上7:30）。目前系统里能看到三天的课节记录：第1天已出勤，课程完成状态标为未完成，作业未提交；第2天已出勤，课程完成、作业已提交；第3天已出勤，完成和作业状态暂无记录。三天打卡均未见记录，伴学状态未配置。需要说明的是，这三条记录没有可核对的具体学习日期，所以只能按当前可见的三天来说，不能确认它们都发生在本周内。如需针对某天核对细节，随时告诉我。

## 原报告与完成评估

```json
[
  {
    "id": "[\"synthetic-4b1-a\",\"report:makeup-reminder-sent-5e421656\",1]",
    "version": 2,
    "commit_request_id": "planner-progress-report-makeup-reminder-sent-5e421656-rev1",
    "created_at": "2026-10-04T10:00:00+08:00",
    "event_ids": [
      "event-76992b96414c4b2bb260aab3c7a391a2"
    ],
    "origin_run_id": "run-152c89788a934aff839425eb2e45d193",
    "payload": {
      "completion_assessments": [
        {
          "complete": false,
          "criterion": "补读已确认原课节 LESSON-L2-FOX-D01",
          "event_id": "event-76992b96414c4b2bb260aab3c7a391a2",
          "evidence_refs": [
            {
              "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
              "kind": "operation",
              "version": 3
            }
          ],
          "reason": "本步骤只完成补课提醒外发并取得 sent 回执；提醒本身不代表补课完成，LESSON-L2-FOX-D01 补读目标尚未达成，家长也尚未回复安排。"
        }
      ],
      "event_ids": [
        "event-76992b96414c4b2bb260aab3c7a391a2"
      ],
      "evidence_refs": [
        {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      ],
      "facts": [
        "2026-10-04 10:00 按当日计划步骤向家长发出 LESSON-L2-FOX-D01 补读提醒，操作 operation-8f6e358341a447e7b25092b5d2f6a2fe 回执阶段为 sent。",
        "提醒正文载明截止为今天 18:00，并请家长反馈孩子方便的时间。",
        "尚未收到家长关于补读安排的回复，LESSON-L2-FOX-D01 补读本身未完成。"
      ],
      "remaining_work": [
        "LESSON-L2-FOX-D01 补读目标达成"
      ],
      "report_id": "report:makeup-reminder-sent-5e421656",
      "revision": 1,
      "suggested_update": "补课提醒已发出（sent）；等待家长回复补读安排，原课节补读仍待完成，截止 2026-10-04T18:00:00+08:00。",
      "supersedes_revision": null
    },
    "report_id": "report:makeup-reminder-sent-5e421656",
    "resolution": "applied",
    "revision": 1,
    "schema_version": 2,
    "source_snapshots": [
      {
        "message_digest": null,
        "ref": {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      }
    ]
  },
  {
    "id": "[\"synthetic-4b1-a\",\"report-learning-feedback-sent-46463a38\",1]",
    "version": 2,
    "commit_request_id": "progress-commit-synthetic-4b1-a-report-learning-feedback-sent-46463a38-r1",
    "created_at": "2026-10-04T10:00:00+08:00",
    "event_ids": [
      "event-9dbd36e81f5347fea5a79beda3e878f1"
    ],
    "origin_run_id": "run-812712c5278d4cf18a1706f5676070a3",
    "payload": {
      "completion_assessments": [
        {
          "complete": true,
          "criterion": "completion.kind=delivery，required_part_ids=[learning-feedback]，required_stage=sent；步骤条件为文字部分已发送并有操作回执，仅依据已查询学情快照撰写",
          "event_id": "event-9dbd36e81f5347fea5a79beda3e878f1",
          "evidence_refs": [
            {
              "id": "operation-42fc8d1b208045cf9baa1a6ecd9c8a6a",
              "kind": "operation",
              "version": 3
            }
          ],
          "reason": "唯一必需 part learning-feedback 为文字类型，已实际发送并回执 stage=sent；正文仅陈述SQL快照字段并明确三条记录无可核对学习日期，未添加无来源结论"
        }
      ],
      "event_ids": [
        "event-9dbd36e81f5347fea5a79beda3e878f1"
      ],
      "evidence_refs": [
        {
          "id": "operation-42fc8d1b208045cf9baa1a6ecd9c8a6a",
          "kind": "operation",
          "version": 3
        }
      ],
      "facts": [
        "已依据 learning_days 批次 synthetic-4b1-snapshot 的SQL原字段撰写本周文字反馈：第1天 attended/incomplete/not_submitted，第2天 attended/completed/submitted，第3天 attended/unknown/unknown，三天 checkin 均 not_checked、companion 均 not_configured，source_learning_date 均为空",
        "反馈正文通过微信工具发出，操作 operation-42fc8d1b208045cf9baa1a6ecd9c8a6a 回执 stage=sent，覆盖 required_part_ids 中的 learning-feedback"
      ],
      "remaining_work": [],
      "report_id": "report-learning-feedback-sent-46463a38",
      "revision": 1,
      "suggested_update": "learning-feedback 文字部分已发送并取得 sent 回执",
      "supersedes_revision": null
    },
    "report_id": "report-learning-feedback-sent-46463a38",
    "resolution": "applied",
    "revision": 1,
    "schema_version": 2,
    "source_snapshots": [
      {
        "message_digest": null,
        "ref": {
          "id": "operation-42fc8d1b208045cf9baa1a6ecd9c8a6a",
          "kind": "operation",
          "version": 3
        }
      }
    ]
  },
  {
    "id": "[\"synthetic-4b1-a\",\"report:verify-learning-d01-2026-10-04T17:30\",1]",
    "version": 2,
    "commit_request_id": "planner-progress-report-verify-learning-d01-2026-10-04T17:30-rev1",
    "created_at": "2026-10-04T17:30:00+08:00",
    "event_ids": [
      "event-76992b96414c4b2bb260aab3c7a391a2"
    ],
    "origin_run_id": "run-37226b692dab4fe0b30e503be643edd3",
    "payload": {
      "completion_assessments": [
        {
          "complete": false,
          "criterion": "补读已确认原课节 LESSON-L2-FOX-D01",
          "event_id": "event-76992b96414c4b2bb260aab3c7a391a2",
          "evidence_refs": [
            {
              "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
              "kind": "message",
              "version": 1
            },
            {
              "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
              "kind": "operation",
              "version": 3
            }
          ],
          "reason": "实际学情记录 day=1 completion_status=incomplete，且无家长回复或新的学习记录证明补读完成；仅提醒外发不构成补课完成。"
        }
      ],
      "event_ids": [
        "event-76992b96414c4b2bb260aab3c7a391a2"
      ],
      "evidence_refs": [
        {
          "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
          "kind": "message",
          "version": 1
        },
        {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      ],
      "facts": [
        "2026-10-04 17:30 执行 verify_learning 步骤：查询本家庭绑定批次 synthetic-4b1-snapshot 的 learning_days 原始字段，day=1（课程绑定映射为 LESSON-L2-FOX-D01）completion_status 仍为 incomplete、homework_status 为 not_submitted，未确认 LESSON-L2-FOX-D01 补读完成。",
        "本轮微信完整读取共 2 条消息，均为我方外发（seq1 补课提醒、seq2 学情反馈），截至 17:30 没有家长关于补读安排的新回复。",
        "原 10:00 补课提醒操作 operation-8f6e358341a447e7b25092b5d2f6a2fe 回执阶段仍为 sent；本步骤按 verify_learning 范围只核实事实，未新增外发动作。"
      ],
      "remaining_work": [
        "LESSON-L2-FOX-D01 补读目标达成（原截止 20-04 18:00 前，实际为 2026-10-04 18:00）"
      ],
      "report_id": "report:verify-learning-d01-2026-10-04T17:30",
      "revision": 1,
      "suggested_update": "",
      "supersedes_revision": null
    },
    "report_id": "report:verify-learning-d01-2026-10-04T17:30",
    "resolution": "applied",
    "revision": 1,
    "schema_version": 2,
    "source_snapshots": [
      {
        "message_digest": "f1d7b1c616cf178d382b55a5e40bd8a4eb8aa9287cf24443908943baf95ea6a1",
        "ref": {
          "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
          "kind": "message",
          "version": 1
        }
      },
      {
        "message_digest": null,
        "ref": {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      }
    ]
  },
  {
    "id": "[\"synthetic-4b1-a\",\"report:verify-learning-d01-2026-10-04T18:00\",1]",
    "version": 2,
    "commit_request_id": "planner-progress-report-verify-learning-d01-2026-10-04T18-00-rev1",
    "created_at": "2026-10-04T18:00:00+08:00",
    "event_ids": [
      "event-76992b96414c4b2bb260aab3c7a391a2"
    ],
    "origin_run_id": "run-706aad4baf7b406188134bcca8986859",
    "payload": {
      "completion_assessments": [
        {
          "complete": false,
          "criterion": "补读已确认原课节 LESSON-L2-FOX-D01",
          "event_id": "event-76992b96414c4b2bb260aab3c7a391a2",
          "evidence_refs": [
            {
              "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
              "kind": "message",
              "version": 1
            },
            {
              "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
              "kind": "operation",
              "version": 3
            }
          ],
          "reason": "18:00 截止时刻实际学情记录 day=1 completion_status=incomplete、homework_status=not_submitted，且无家长新回复或新的学习记录证明补读完成；仅提醒外发不构成补课完成。"
        }
      ],
      "event_ids": [
        "event-76992b96414c4b2bb260aab3c7a391a2"
      ],
      "evidence_refs": [
        {
          "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
          "kind": "message",
          "version": 1
        },
        {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      ],
      "facts": [
        "2026-10-04 18:00 截止时刻执行 verify_learning 步骤：查询本家庭绑定批次 synthetic-4b1-snapshot 的 learning_days 原始字段，day=1（课程绑定映射 LESSON-L2-FOX-D01）completion_status 仍为 incomplete、homework_status 仍为 not_submitted，未确认 LESSON-L2-FOX-D01 补读完成。",
        "本轮微信完整读取共 2 条消息（seq1 补课提醒、seq2 学情反馈），均为我方外发，截至 18:00 没有家长关于补读安排的新回复。",
        "按本步骤完成条件，截止时刻仍未确认补读完成，保留 LESSON-L2-FOX-D01 原课节补读责任，不新增无依据的完成结论；本步骤按 verify_learning 范围只核实事实，未新增外发动作。"
      ],
      "remaining_work": [
        "LESSON-L2-FOX-D01 补读目标达成（原截止 2026-10-04 18:00，截止时刻仍未确认完成，责任保留）"
      ],
      "report_id": "report:verify-learning-d01-2026-10-04T18:00",
      "revision": 1,
      "suggested_update": "事项保持 in_progress，remaining_work 保留 LESSON-L2-FOX-D01；后续需依据家长回复或实际学情记录变化再核实，不由本步骤自行结案或新增提醒动作。",
      "supersedes_revision": null
    },
    "report_id": "report:verify-learning-d01-2026-10-04T18:00",
    "resolution": "applied",
    "revision": 1,
    "schema_version": 2,
    "source_snapshots": [
      {
        "message_digest": "f1d7b1c616cf178d382b55a5e40bd8a4eb8aa9287cf24443908943baf95ea6a1",
        "ref": {
          "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
          "kind": "message",
          "version": 1
        }
      },
      {
        "message_digest": null,
        "ref": {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      }
    ]
  },
  {
    "id": "[\"synthetic-4b1-a\",\"report:verify-learning-d01-2026-10-04T20:00\",1]",
    "version": 2,
    "commit_request_id": "planner-progress-run89933ca3-event76992b96",
    "created_at": "2026-10-04T20:00:00+08:00",
    "event_ids": [
      "event-76992b96414c4b2bb260aab3c7a391a2"
    ],
    "origin_run_id": "run-6a684cd797eb492e9b58a00523894c12",
    "payload": {
      "completion_assessments": [
        {
          "complete": true,
          "criterion": "补读已确认原课节 LESSON-L2-FOX-D01",
          "event_id": "event-76992b96414c4b2bb260aab3c7a391a2",
          "evidence_refs": [
            {
              "id": "learning-evidence:dc60f6db7b9f8687a956b60a3c53615c86bfbf8da31631273cb00adebb5a9686",
              "kind": "evidence",
              "version": 1
            }
          ],
          "reason": "原目标为补读已确认原课节 LESSON-L2-FOX-D01（completion.kind=goal，lesson_refs=LESSON-L2-FOX-D01，required_part_ids 为空）。覆盖截至 2026-10-04T20:00:00+08:00 的绑定批次 synthetic-live-progress-1 中 day=1 completion_status=completed，宿主确认的课程绑定映射 LESSON-L2-FOX-D01，且宿主学情证据为 lesson_completed、usable_for_completion，满足恢复条件中『实际学情记录显示对应批次 day=1 completion_status 变为完成』。本步骤据此判断补读目标已达成，最终结案仍以宿主核验原合同为准；新批次 homework_status=unknown 已如实记录，不属于本目标 lesson_refs 的完成条件。"
        }
      ],
      "event_ids": [
        "event-76992b96414c4b2bb260aab3c7a391a2"
      ],
      "evidence_refs": [
        {
          "id": "learning-evidence:dc60f6db7b9f8687a956b60a3c53615c86bfbf8da31631273cb00adebb5a9686",
          "kind": "evidence",
          "version": 1
        }
      ],
      "facts": [
        "2026-10-04 20:00 复核本家庭绑定批次 learning_days 原始字段：新批次 synthetic-live-progress-1（imported_at 与 coverage_through 均为 2026-10-04T20:00:00+08:00）day=1 completion_status=completed、attendance_status=attended，homework_status=unknown、companion_status=unknown、checkin_status=unknown。",
        "新批次 synthetic-live-progress-1 的宿主确认课程绑定为 simulation-L2-fox / simulation-v1，lesson_map day=1 对应 LESSON-L2-FOX-D01，覆盖截至 2026-10-04T20:00:00+08:00。",
        "宿主学情证据 learning-evidence:dc60f6db7b9f8687a956b60a3c53615c86bfbf8da31631273cb00adebb5a9686（kind=lesson_completed，lesson_ref=LESSON-L2-FOX-D01，来源 synthetic-live-progress-1!row-2）标注 trusted、valid、usable_for_completion。",
        "此前用于 17:30 与 18:00 核实的批次 synthetic-4b1-snapshot（覆盖截至 2026-10-04T07:30:00+08:00）day=1 仍为 completion_status=incomplete、homework_status=not_submitted；该批次覆盖时间早于新批次，本次以覆盖更新的批次记录为准。",
        "本轮微信完整读取共 2 条消息（seq1 补课提醒 operation-8f6e358341a447e7b25092b5d2f6a2fe、seq2 学情反馈），均为我方外发，家长仍未回复新的补读安排；本次恢复依据是实际学情记录变化。",
        "本步骤按 verify_learning 范围只核实事实并提交报告，未新增外发动作，不自行结案。"
      ],
      "remaining_work": [
        "LESSON-L2-FOX-D01 补读目标：覆盖更新批次 day=1 completion_status 已为 completed，原课节补读责任待宿主按原合同最终核验"
      ],
      "report_id": "report:verify-learning-d01-2026-10-04T20:00",
      "revision": 1,
      "suggested_update": "",
      "supersedes_revision": null
    },
    "report_id": "report:verify-learning-d01-2026-10-04T20:00",
    "resolution": "applied",
    "revision": 1,
    "schema_version": 2,
    "source_snapshots": [
      {
        "message_digest": null,
        "ref": {
          "id": "learning-evidence:dc60f6db7b9f8687a956b60a3c53615c86bfbf8da31631273cb00adebb5a9686",
          "kind": "evidence",
          "version": 1
        }
      }
    ]
  },
  {
    "id": "[\"synthetic-4b1-a\",\"report:verify-learning-d01-correction-2026-10-05T09:00\",1]",
    "version": 2,
    "commit_request_id": "planner-progress-report-verify-learning-d01-correction-2026-10-05T09-00-rev1",
    "created_at": "2026-10-05T09:00:00+08:00",
    "event_ids": [
      "event-76992b96414c4b2bb260aab3c7a391a2"
    ],
    "origin_run_id": "run-1206c878bb6247ac90ce68467631f8b2",
    "payload": {
      "completion_assessments": [
        {
          "complete": false,
          "criterion": "补读已确认原课节 LESSON-L2-FOX-D01",
          "event_id": "event-76992b96414c4b2bb260aab3c7a391a2",
          "evidence_refs": [
            {
              "id": "learning-correction:7e1d1bdda4341cbd5b02e6fb0597a78d07d0ce8dc518e9df4a03561f438a9b32",
              "kind": "evidence",
              "version": 1
            }
          ],
          "reason": "此前判完所依据的学情证据已被宿主更正记录作废；覆盖最新的批次 synthetic-live-progress-2 中 day=1（绑定映射 LESSON-L2-FOX-D01）completion_status=incomplete，且无家长回复或新学习记录证明补读完成，故按当前实际学情该补读目标未达成，责任保留。"
        }
      ],
      "event_ids": [
        "event-76992b96414c4b2bb260aab3c7a391a2"
      ],
      "evidence_refs": [
        {
          "id": "learning-correction:7e1d1bdda4341cbd5b02e6fb0597a78d07d0ce8dc518e9df4a03561f438a9b32",
          "kind": "evidence",
          "version": 1
        },
        {
          "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
          "kind": "message",
          "version": 1
        },
        {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      ],
      "facts": [
        "2026-10-05 09:00 review：宿主学情更正记录 learning-correction:7e1d1bdda4341cbd5b02e6fb0597a78d07d0ce8dc518e9df4a03561f438a9b32（v1，trusted、valid）作废了此前用于判定补读完成的证据 learning-evidence:dc60f6db7b9f8687a956b60a3c53615c86bfbf8da31631273cb00adebb5a9686（现 v2，valid=false、usable_for_completion=false，superseded_by_batch=synthetic-live-progress-2）。",
        "SQL 复核本家庭批次原始字段：覆盖截至 2026-10-05T09:00:00+08:00 的最新批次 synthetic-live-progress-2 中 day=1 completion_status=incomplete、homework_status=unknown、attendance_status=attended；此前批次 synthetic-live-progress-1（覆盖截至 2026-10-04T20:00）day=1 曾为 completed，现已被更正覆盖。",
        "course_bindings 确认 synthetic-live-progress-2 绑定 simulation-L2-fox / simulation-v1，lesson_map day=1 对应 LESSON-L2-FOX-D01，覆盖截至 2026-10-05T09:00:00+08:00。",
        "本轮微信完整读取共 2 条消息（seq1 补课提醒、seq2 学情反馈），均为我方外发，家长至今没有回复补读安排；同覆盖时间也没有新的家长来信。",
        "按更正后的实际学情，LESSON-L2-FOX-D01 补读责任恢复保留（原截止 2026-10-04T18:00:00+08:00 已过）；本次为 review/核实，未新增外发动作，当前 run 文本外发能力未启用，不自行改事项状态。"
      ],
      "remaining_work": [
        "LESSON-L2-FOX-D01 补读目标达成（原截止 2026-10-04T18:00:00+08:00 已过；更正后覆盖最新批次 day=1 仍为 incomplete，责任保留，等待宿主按原合同核验后续处理）"
      ],
      "report_id": "report:verify-learning-d01-correction-2026-10-05T09:00",
      "revision": 1,
      "suggested_update": "",
      "supersedes_revision": null
    },
    "report_id": "report:verify-learning-d01-correction-2026-10-05T09:00",
    "resolution": "applied",
    "revision": 1,
    "schema_version": 2,
    "source_snapshots": [
      {
        "message_digest": null,
        "ref": {
          "id": "learning-correction:7e1d1bdda4341cbd5b02e6fb0597a78d07d0ce8dc518e9df4a03561f438a9b32",
          "kind": "evidence",
          "version": 1
        }
      },
      {
        "message_digest": "f1d7b1c616cf178d382b55a5e40bd8a4eb8aa9287cf24443908943baf95ea6a1",
        "ref": {
          "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
          "kind": "message",
          "version": 1
        }
      },
      {
        "message_digest": null,
        "ref": {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      }
    ]
  },
  {
    "id": "[\"synthetic-4b1-a\",\"report:verify-learning-d01-step-2026-10-05T09:00\",1]",
    "version": 2,
    "commit_request_id": "progress-commit-run-25ac2761cbe84338ac985ff4e90369ca-d01-verify-2026-10-05",
    "created_at": "2026-10-05T09:00:00+08:00",
    "event_ids": [
      "event-76992b96414c4b2bb260aab3c7a391a2"
    ],
    "origin_run_id": "run-1904d9ff955041db91e847859efa05bd",
    "payload": {
      "completion_assessments": [
        {
          "complete": false,
          "criterion": "补读已确认原课节 LESSON-L2-FOX-D01",
          "event_id": "event-76992b96414c4b2bb260aab3c7a391a2",
          "evidence_refs": [
            {
              "id": "learning-correction:7e1d1bdda4341cbd5b02e6fb0597a78d07d0ce8dc518e9df4a03561f438a9b32",
              "kind": "evidence",
              "version": 1
            },
            {
              "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
              "kind": "message",
              "version": 1
            }
          ],
          "reason": "覆盖最新的绑定批次 synthetic-live-progress-2（coverage_through=2026-10-05T09:00:00+08:00，绑定映射 day=1→LESSON-L2-FOX-D01）中 day=1 completion_status=incomplete；此前判完证据已被宿主更正记录作废，且无家长回复或新学习记录证明补读完成，按本步骤完成条件核验未通过。"
        }
      ],
      "event_ids": [
        "event-76992b96414c4b2bb260aab3c7a391a2"
      ],
      "evidence_refs": [
        {
          "id": "learning-correction:7e1d1bdda4341cbd5b02e6fb0597a78d07d0ce8dc518e9df4a03561f438a9b32",
          "kind": "evidence",
          "version": 1
        },
        {
          "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
          "kind": "message",
          "version": 1
        },
        {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      ],
      "facts": [
        "2026-10-05 09:00 执行 verify_learning 步骤：SQL 复核本家庭批次 learning_days 原始字段，覆盖截至 2026-10-05T09:00:00+08:00 的最新批次 synthetic-live-progress-2 中 day=1 completion_status=incomplete、homework_status=unknown、attendance_status=attended，未确认 LESSON-L2-FOX-D01 补读完成。",
        "course_bindings 确认 synthetic-live-progress-2 绑定 simulation-L2-fox / simulation-v1，lesson_map day=1 对应 LESSON-L2-FOX-D01；此前判完依据 learning-evidence:dc60f6db7b9f8687a956b60a3c53615c86bfbf8da31631273cb00adebb5a9686 已被宿主更正记录 learning-correction:7e1d1bdda4341cbd5b02e6fb0597a78d07d0ce8dc518e9df4a03561f438a9b32 作废（现 v2，usable_for_completion=false，superseded_by_batch=synthetic-live-progress-2）。",
        "本轮微信完整读取共 2 条消息（seq1 补课提醒 operation-8f6e358341a447e7b25092b5d2f6a2fe、seq2 学情反馈），均为我方外发，家长至今没有关于补读安排的新回复。",
        "本步骤完成条件（覆盖最新绑定批次 day=1 completion_status 变为 completed）未满足；按 verify_learning 范围只核实事实并提交报告，未新增外发动作，不自行改事项状态。"
      ],
      "remaining_work": [
        "LESSON-L2-FOX-D01 补读目标达成（原截止 2026-10-04T18:00:00+08:00 已过；覆盖最新批次 day=1 仍为 incomplete，责任保留）"
      ],
      "report_id": "report:verify-learning-d01-step-2026-10-05T09:00",
      "revision": 1,
      "suggested_update": "",
      "supersedes_revision": null
    },
    "report_id": "report:verify-learning-d01-step-2026-10-05T09:00",
    "resolution": "applied",
    "revision": 1,
    "schema_version": 2,
    "source_snapshots": [
      {
        "message_digest": null,
        "ref": {
          "id": "learning-correction:7e1d1bdda4341cbd5b02e6fb0597a78d07d0ce8dc518e9df4a03561f438a9b32",
          "kind": "evidence",
          "version": 1
        }
      },
      {
        "message_digest": "f1d7b1c616cf178d382b55a5e40bd8a4eb8aa9287cf24443908943baf95ea6a1",
        "ref": {
          "id": "msg-ebb19ebcf3fb446da7717adbcff8a4ed",
          "kind": "message",
          "version": 1
        }
      },
      {
        "message_digest": null,
        "ref": {
          "id": "operation-8f6e358341a447e7b25092b5d2f6a2fe",
          "kind": "operation",
          "version": 3
        }
      }
    ]
  }
]
```