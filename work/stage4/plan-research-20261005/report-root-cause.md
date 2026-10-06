# K report_gate 空 remaining_work 的根因与最小收敛方案

日期：2026-10-05。独立只读研究；没有修改冻结源码、角色稿、旧证据，没有调用模型或渠道。本报告不是新增关口通过证据。

## 结论

1. **不是夹具注入，也不是模型提交“没有剩余工作”。** K 的真实 `progress.submit` 参数省略了 `remaining_work`；`Report.remaining_work = Field(default_factory=list)` 自动填成 `[]`，随后 `model_dump` 持久化。模型提交的 facts、suggested_update、保存的记忆及实际发送都明确“只读了一小段，尚未读完”。将这个问题描述为“模型生成空剩余并认为完成”不准确。
2. **原运行没有误结案。** 原事项仍为 pending、剩余 `LESSON-L2-FOX-D01`，报告未消费。report_gate 只验证未消费报告阻止 daily，没有运行 planner_progress。当前代码也不把报告数组复制到 Event；done 仍需可信完成证据。空数组目前是报告语义/表示不齐及潜在误导，不是已证实的状态清零漏洞。
3. **存在独立而真实的 progress 消费缺口。** 原报告引用消息 v2，随后确认回复回执只改变该消息的 `reply_operation_refs`，版本升为 v3。原 DB 的隔离 backup 上执行合法 progress no-op，返回 `EVIDENCE_SUPERSEDED`。这是内容未变而派生记录版本变动导致的阻断；不是 K 原运行已尝试 progress 失败，也不是空数组导致。
4. 最小方案应同时明确 **Event 权威剩余、报告本轮观察的权限边界**，并对报告引用区分源内容更正和解释/回执派生变化。不能靠解析 facts 判断是否完成，不能要求所有报告剩余非空，也不能吞掉所有版本过期。

## 冻结依据与实际生成链

源码根：`work/stage4/4c-development-k/`。K report_gate manifest 的 69 个源文件全部与该目录 SHA256 相同。

证据根：`work/stage4/k-final1/4c-flash-20261004-k-final1/evaluations/backend-stage4/4c-flash-20261004-k/report_gate/`。

| 环节 | 代码或实际证据 | 结论 |
|---|---|---|
| 外部输入 | `backend/scripts/accept_stage4_daily.py:155–163` | 夹具仅建立历史事项、输入“只读了一小段，还没有读完，请帮我记住这个进度”，运行真实 service，之后转 daily 检查门禁；没有建立报告或指定字段答案。 |
| 真实模型调用 | `tool-trace.json` 第 5 条（0 基索引 4）`progress.submit`；run `run-89631e6a9f464227aa80ad34c214a2b2` | 参数含 event_ids、facts、suggested_update、evidence_refs、报告/request ID；**没有 remaining_work**。 |
| 参数规范化 | `backend/src/digital_tutor/domain.py:178–187`，特别 185；`tools.py:43–51` | 字段为可省略 `list[str]`、默认空数组，JSON Schema 没有本字段语义说明，也不在 required 中。 |
| 保存 | `application.py:199–233`，特别 209、226–231 | 校验事项存在、证据当前、修订与幂等后保存规范化载荷；不核对报告剩余与 Event 是否一致，不改 Event。 |
| 实际持久化 | `records.json` report `report-fox-progress-001` revision 1 | facts：“第1天补读只读了一小段，尚未读完”；suggested_update：“…仍处于未完成状态”；payload.remaining_work 为 `[]`，resolution 为 null。 |
| 实际外发 | operation `operation-09c6bcf6f46642f59bf4eedffe557015` | “……第1天（LESSON-L2-FOX-D01）的补读任务仍算未完成，继续保留在待补读里。”原运行 sent；没有声明已完成。 |
| 事项实态 | `records.json` event `historical-event-fox` | version 1、pending、remaining_work 为 `["LESSON-L2-FOX-D01"]`。 |

原设计合同 `docs/教辅后端数据与接口合同.md:129` 的报告样例已经是 `[{event_id, work_key, description}]`。当前 `list[str]` 是落地简化，既缺事项归属，也没有区分“未提供观察”与“明确空列表”。不能反过来声称现有合同已经明确定义该字段只是本轮局部观察；这是需要收敛的语义边界。第 174 行明确未提供 changes.remaining_work 应保留原值，不能清空未完成课节。

## progress 消费与完成守卫

- `context.py:60–72,114–116` 同时组装绑定原报告、关联原 Event 全量数据和真实引用记录，附 referenced_version/reference_current；原 Event 的 completion、remaining_work 没有丢失。`agent/prompts/planner_progress.md:2–6` 要求读取原目标/完成标准，goal 依可信课节完成，delivery 依原部分/回执；允许有据无变化消费。
- `domain.py:123–129,145–149` 的 Change 不接受 remaining_work；Strict 在 36–37 行禁止额外字段。
- `planning.py:138–159` 精确绑定报告版本和全部关联事项，验证报告 refs，再更新 Event，最后标记报告 applied。没有读取/复制 report.remaining_work 来更新 Event。
- `planning.py:7–23,54–58` 对 done 的 goal 要求可信、有效 lesson_completed 覆盖全部课节；delivery 要求当前事项/要求版本/收件人的全部必要部分到达原回执阶段。只有这个条件满足后才将 Event.remaining_work 清空。
- 因而 local observation 不能充当 authoritative full remaining；就算未来报告写成完整建议，也只是建议，最终权威仍是原 Event 合同和可信完成证据。空/非空报告都不应单独决定完成。

## 派生版本变化的独立根因

对比真实 `message.interpret` 回执 v2 和最终 `records.json` v3，差异只有：

- version：2 → 3；
- 同一个意图的 reply_operation_refs：空数组 → 上述实际 sent operation ID。

body、来源身份、发生/收到时刻、方向、intent_id/text/requires_reply 均不变。`wechat.py:72–81` 正是在回执落库时更新该消息及版本；`application.py:128–135` 对所有引用一律要求整个记录版本相等；`planning.py:151` 又验证报告原 refs。因此最终副本中即使 changes={}，也不能消费这个内容有效的报告。

可复用已有原语，不需新 Planner 入口：`service_context.py:14–20` 已用固定原始字段计算 message_source_digest，并明确解释和回执不是输入变化；40–58 行已有宿主真实读取证据。`family_candidates.py:27–28,139,146–147,171–172` 已采用原文内容指纹校验候选。两处字段集合略不同，实施时应统一选定语义/版本，而非随意混用。SQL 原始行的 source_rows 不能直接代替消息源证据。

**不能仅比较当前原文与模型摘要，也不能把旧引用 version 替换成最新 version。** 新报告应由宿主在提交时保存与 report/revision/ref 精确绑定的原始内容指纹/源修订投影；progress 对 message 原文证据比较这份可信快照。回执/解释派生变化只可通过这个专用校验，不改变全局 validate_refs；control、operation、完成证据仍按各自当前有效性校验。正文、来源身份、实际来源时间或业务解释依赖若改变，仍须受限复核/报告修订。若引用实际依赖某个已解释意图，不能因为正文相同就忽略意图含义变化。

历史报告没有该快照时，只在存在可证明对应原版本的宿主可信读取记录/历史投影时走兼容校验；证据不足保持阻断，沿既有 service 报告连续 revision 路径补充，保留旧事实、原 event_ids 和旧报告。不得删除报告绕过 daily、仅信导出调试文本、让 planner_progress 改报告或另加规划入口。无变化审计不能 bump 家庭版本或反复唤醒。

## 新隔离复现

运行：`D:\conda\python.exe -X utf8 work/stage4/plan-research-20261005/report-repro.py`。

产物：`report-repro-evidence.json`、`report-repro-20261005-b/`。这是本地冻结源码核心交易复现，**不是服务器/带租约真实 Worker 的新验收**。实际 DB 以只读连接 backup 到新目录；原 DB 前后 SHA256 相同。初次研究脚本把 daily event_ids 映射当列表导致夹具构造失败，失败目录 a 保留；修正研究脚本后使用独立 b 重跑，未改产品代码。

| 检查 | 实际结果 |
|---|---|
| 原 K DB 副本直接 progress no-op | EVIDENCE_SUPERSEDED；Event 仍 pending、原剩余保留；报告未消费；context 标 reference_current=false。 |
| 新合成双事项、当前消息 refs；省略报告 remaining | 持久化默认 `[]`。 |
| 同 request 重放并显式 `remaining_work=[]` | 成功幂等重放；说明改变默认规范化策略会影响旧请求摘要/身份兼容。 |
| progress 直接 changes.remaining_work=[] | INVALID_SCHEMA；不消费报告。 |
| 同报告仅据家长消息把第一事项 done | COMPLETION_NOT_MET；两个事项原剩余保留、报告未消费。 |
| 同报告合法双事项 no-op | applied；两个事项均 pending、各自 lesson 剩余保留。 |

已有 `backend/tests/test_stage1.py:218–279` 也覆盖可信完成门禁、交付部分/回执、禁止改完成标准、多事项原子回滚、精确消费与修订。现有守卫应保留，不重复发明完成校验。

## 最小分阶段处理与兼容

**第一步，当前修复边界。** 不改旧报告载荷或将 [] 回填为猜测内容。在 report/工具字段说明及 progress 宿主投影中明确：报告字段是本次可证实的剩余观察/建议，省略或旧 [] 表示未提供，不是“原事项无剩余”；完整权威剩余按 `event_id + event_version + completion + remaining_work` 从 Event 读取。按 event_id 呈现已有数据即可，不让 service 重抄全目录、不把多事项字符串列表按位置猜配。补宿主来源指纹，使只增回复回执不阻断真实有据报告消费；保留源更正阻断。

**第二步，向原合同收敛。** 恢复报告 `remaining_work` 的 `event_id/work_key/description` 结构化项，逐项必须属于绑定的原事项/原要求，仍为局部观察而非完成裁定。过渡期兼容历史 list[str]，标明旧格式未建立逐事项定位，禁止猜配；不要在读取或重放时改历史 payload。需明确新旧 schema/规范化版本，原 request_id 的 receipt 与同 report/revision 的不可变载荷优先，不能因新默认/结构生成不同 canonical payload 导致旧幂等冲突。若新内容确需补齐，按既有连续 revision 与保留旧事实规则提交。

这不要求所有报告非空；例如只有改约/暂停事实、没有新的剩余观察，或确有可信完成证据时，都可以无此类观察。也不通过关键词扫描 facts 做硬判定。只新增可验证的结构/来源校验，保留现有完成守卫。

## 有价值的关闭测试与依赖

1. 保留 K 原 report-before-send 顺序：部分进度 → 报告 → 确认回复/回执 → 真实带租约 progress no-op/更新 → 报告消费 → daily 恢复。断言 Event 仍未完成、原剩余保留、无新增外发/Planner 入口。测试仅给源事实，不告诉模型报告字段或目标提交。
2. 同正文仅解释/回执版本变化可以沿可信原始指纹消费；正文/来源更正、撤销有效完成证据、跨家庭相同正文、指纹缺失或篡改分别必须拒绝/进入既有受限复核。不能用“所有消息旧版本都可用”替代。
3. 省略、显式 []、旧字符串、新结构化项、混合多事项、旧 request/report revision 重放都覆盖；每个 event 各自剩余不混淆，消费一个报告不清除其他未消费报告。
4. 新旧报告都测试无可信完成的 done 拒绝，以及真实可信完成/完整必要交付到达原阶段后的合法 done；不能为了防空值而把合法完成卡死。
5. 结构/投影审计的无变化读取、进程重启和重复门禁扫描保持零 API/零新外发/业务版本稳定；来源真正更正后只触发所需既有恢复。

4C 独立“未消费报告阻 daily”已有实测结果仍有效，但不能据此说报告消费闭环通过。**在 4D progress 消费接通及“报告消费后 daily 恢复”验收前，上述消息派生版本兼容必须关闭；报告局部观察/权威剩余边界也须完成，否则不能把 [] 当完成证据。** 当前没有误结案证据，不应将其升级为已发生的完成条件绕过。
