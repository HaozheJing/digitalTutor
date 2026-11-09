# 工具、Skill 与配置口径（独立候选）

本文件解释现有快循环调用契约，不启用功能、不创建服务事件、不扩大触达时间、强度或节奏。慢循环业务服务规划仍占位。旧实现和接口转接保留在代码中；本轮变更前版本另存 `legacy/tool-contracts-before-20261109`，不是自动回退来源。

## 所有权与加载

- 宿主负责事件验证、定时唤醒、家庭绑定、候选、上下文、发前复核、入站语音理解、节日成图检查和事件收尾。
- Skill 规定判断和表达。Skill 名称引用不是自动调用；需要时用 `skill_read(name=...)` 读取正文或 `skill_read(name=...,path=...)` 读取附件。目录依赖是阅读说明，不是权限授予。
- S01–S11、统一周互动及节日的独立执行通常加载业务 Skill 与 reply-style；其他任务按宿主分流加载共享约束。家庭实验 persona 有独立加载策略。
- 工具是否可调用取决于当前配置、runtime-capabilities.json、任务类型、读取回执及当前上下文。manifest 不覆盖全局退役和内部任务禁令；发布业务正文不能自行开权限。
- 事件／时点 → 上下文 → 既有 Skill 与知识 → 执行和复核 → 事项及交付状态回写。内部日计划是快循环排期，不是自主发明服务的慢循环。

## 当前模型入口与保留实现

| 当前入口 | 内部保留承接 | 限制 |
|---|---|---|
| skill_read | service_catalog / service_skill / service_reference | 读取实际正文版本；不自动激活目录里的所有动作 |
| knowledge_browse/search/read | course_browse/search/read 等 | 正式 curriculum_ref 为主；检索候选后读正文；查询注册收口不等于删除底层分发 |
| conversation_read / evidence_read | 受控历史、材料及快照读取 | 历史与快照不授予当前执行权 |
| workflow_read | workflow_context / service_schedule_list / service_case_list / workflow_recheck | context 默认 inspect；明确修改用 amend；立即履行用 fulfill＋实际 task_id；refresh 只刷新当前活动任务 |
| workflow_commit | 原事项事务 | 内部日计划仅已启用机会；家长修改需当前原话、实际 task_id/revision 或明确暂停范围 |
| workflow_fulfill | 原事项领取与交付 | 重复履行读取原交付最新回执，不再 send_message |
| send_message | reply_to_message / deliver_message / deliver_proactive / send_image → deliver_festival | event_id/day/家庭由宿主绑定；S01不传候选或图片ID；其他主动沿用候选与令牌；highlight 任务由宿主交付原图原文 |
| request_human_confirmation | 宿主绑定的人工转交 | 内部转交、外部工单受理、正式交付、业务问题解决分别核验 |
| memory_read | memory_recall 或治理记忆；for_update → preference_context | 根据真实配置选择来源；读取不等于写入 |

候选生成、check_proactive、completion_plan_context、finish_event、转写理解和图片检查等旧名可能出现在 origin=host/adapter 审计记录中，这是内部兼容实现，不是模型调用指令。旧 service_schedule/save_plan/pause_contact 等不属于当前工作流模型入口。

`plan-today` 保留明确标识的旧模式说明；当前 agent-workflow-v1 即使任务标签仍叫 plan-today，实际使用 plan-service-day。禁用的 service-policy/references/supervision.md 保持禁用。S11 planning-contract 中 delivery=deliver_proactive 是保留的内部路由标识，不改成模型入口名。

## 条件能力与媒体边界

- manage-service-arrangements 打开 workflow_read；明确上下文才打开修改或履行，纯查看不开放写操作。
- plan-service-day 和 plan-after-lesson-completion 只在对应内部任务获得提交能力；内部任务不得发送。
- highlight-growth-video 当前也承载 highlight_lookup 读取授权；不代表 H13 资格成立或视频制作、投递已接通。
- inspect-family-image 打开受控图片理解，generate-reading-audio 打开音频生成；生成音频不证明微信语音已送达。
- 节日任务才能打开相应生图能力；生成、宿主实际检查与图文分别送达都要有回执。

## 配置口径

只报告实际运行根目录的配置。独立候选代码目录、冻结验收工作区与线上家庭目录不是同一配置作用域；没有根配置不能推断所有实验未启用。2026-11-09 盘点的代表冻结工作区启用了工作流、完课规划、消息钩子和新版工具表面；不把该状态推广到其他运行或线上。

| preference-memory.enabled | slow_enabled | 对外可说明的行为 |
|---|---|---|
| 未开启 | 任意 | 治理与夜间整理未启用；本轮若实际开放 memory_remember，只能保存有精确自报原话的 speaker.self_reported_name；旧记录不是治理偏好 |
| true | 未开启 | 治理读取及明确操作控制按既有权限执行，旧称呼写入口退出；不承诺夜间自动提取 |
| true | true | 治理模式＋独立夜间整理；家庭服务会话仍不能调用维护专用工具 |

本次不修改任何配置值。明确暂停即时通过既有 workflow 事务处理，不等待夜间记忆，也不把临时约束保存成永久画像。外部高光接收代码存在不代表当前工作区启用了生产者入口，更不代表获得其他家庭消息授权。

## 回归与验收口径

本轮针对注册/兼容、Skill 读取、权限作用域、配置提示、规划、暂停、合并、重复、过期、履行与回执做隔离自动化回归。测试使用本候选 runtime 下新建的工作区，独立数据库、配置、临时文件和日志，禁止外部网络，不启动线上或候选服务。

自动化检查不等于新增原生模型回放，更不等于线上业务验收。所有触发时点、暂停规则、频控、manifest、发送守卫和回执状态机保持原约束；历史原生验收仍按其原提交与场景解释。
