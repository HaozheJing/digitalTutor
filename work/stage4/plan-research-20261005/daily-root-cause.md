# K 今日成员空步骤：独立工程根因排查

日期：2026-10-05。范围：只读 K 冻结源码、模型工具实参、业务导出及隔离 Scheduler 复现；未调用模型、未启动 Worker、未修改旧库/源码/提示词。适用祖先目录及仓库内未发现 AGENTS.md。

**结论：这不是 Hermes 取消恢复漏存字段，而是提交合同允许“选入今日、仍 pending、既无下一步也无等待条件”。两条真实路径均已落库。下一次 Scheduler 不会为这两个事项生成步骤；daily 已成功也不会当天再规划修补。建议在提交后的有效今日 Event 上校验“可执行下一步，或明确 waiting/resume”，保留 Plan.items 的名单/排序结构和 Event 唯一责任。**

本报告不将上述缺口扩大为已发生的发送失败。K 的 daily 模式本来就不执行主动步骤；下述 full 模式结果来自新的本地复制库，只证明调度分支。

## 1. 核对范围与可复查证据

源码根 `S = E:/Agent Project/digitalTutor/work/stage4/4c-development-k`。

证据根 `R = E:/Agent Project/digitalTutor/work/stage4/k-final1/4c-flash-20261004-k-final1/evaluations/backend-stage4/4c-flash-20261004-k`。

本文 `S/...:行号` 与 `R/...` 均以以上绝对路径展开。7 个相关 Python 模块与最终封包源码逐 SHA256 相同，记录于本目录 `daily-null-step-checks.json#/files`。机器核对脚本为 `check_daily_null_steps.py`；模型文件 SHA、工具 arguments/result digest、精确 trace index、最终 Event/Plan 与复制库调度结果均在该 JSON。原两份 SQLite 使用 `mode=ro&immutable=1` 读取；复制前后原文件 SHA 相同。

### A. inbound_interleave：恢复模型的新建提议就没有步骤

- 原被撤权 run：`run-fee7a0127465481385ce239f8859b16d`。`R/inbound_interleave/model-01.json#/native/tool_trace/3` 的 commit 返回 `RUN_REVOKED`，没有生效。该提议虽含步骤，target_refs 还是 candidate ID，不能借用为恢复后的有效步骤。
- 恢复 run：`run-41daf72e4e3a434eb94464efb34d85f6`；同原 daily job `daily:220e47935d87f6bdda696ed71bdb4d05d2ca873337dc5139a479cbe5a9c49857`。
- `R/inbound_interleave/model-03.json#/native/tool_trace/2`：请求 `req-daily-20261004-attempt2-001`，`expected_family_version=8`；新建 `evt-sim-weekly-feedback-20261006` **没有传 next_action，也没有 waiting**，status=pending，同时 plan_refs 选中它。工具返回 applied。
- 最终 DB：`plan-844750e81bab41e7b9bd5333d6bd07d5` 唯一成员 `event-820dfd9b591d498599d39064cf49d23c` v1；`status=pending, next_action=null, waiting=null`。delivery 的 `remaining_work=["learning-feedback"]`，完成要求仍为真实 sent，没有结案。
- JSON 默认值在新建时物化为 null。不存在“提交中有步骤，数据库写丢”的证据；也不存在从被撤权提议恢复原步骤的合同。

### B. create_and_reuse：次日名单加入无步骤历史责任

- 首日 run `run-4431e1707a534ea197940c6ec322b024` 创建 goal `event-286e9970e6b841159a18e416e1f1488c` 和 delivery `event-3cead74a34b24183bb46c7fe4709c09a`；两者均有有效 next_action，首日 plan 为 `plan-1807a9e1976047778e8dc8c94f5aa12b`。
- `not-selected-yesterday` 是运行前建立的历史合成责任 v1，原本 `pending/null/null`。来源在 `S/backend/scripts/accept_stage4_daily.py:68-82`。这类未选中、未完结历史责任可以保留空步骤，不能为了修复本问题删除它或预先编造动作。
- 次日 run `run-641f6d35e5684ba58f9d05d5b790da56`；`R/create_and_reuse/model-02.json#/native/tool_trace/4` 成功请求 `daily-commit:bd9f5fe220e10f1a2c9500e7fc74918b:run-641f6d35:4`，`new_events=[]; event_updates=[]`，plan_refs 为上述两正式 Event 加 `not-selected-yesterday`。
- 最终 `plan-333c660955aa4c7d9189426e557889e4` 第 3 项就是该历史责任，仍 `pending/null/null`。其余两项步骤未丢。
- 该 run 前 3 次提交分别被 INVALID_SCHEMA、EVIDENCE_MISSING、RECORD_NOT_FOUND 拒绝；尝试修改的是已有有效步骤的两个正式 Event，**从未为 not-selected-yesterday 提出步骤更新**。因此不能把该历史空步骤归因为某次更新回滚或证据校验误清。
- 两次 assembly 均投影历史责任和 next_action；这不是上下文漏掉旧事项。最后删去无必要的 event_updates 保留原两个步骤是合理的，额外选中无步骤责任未被拦才是问题。

## 2. 哪一层允许、哪一层没有补救

| 层次 | 精确代码位置 | 当前行为/影响 |
| --- | --- | --- |
| Schema | `S/backend/src/digital_tutor/domain.py:97-119,122-147,152-160` | EventCreate.next_action 默认为 None；仅约束 waiting 状态和 Waiting 对象同时出现。Change 允许显式 next_action=None，省略则保留。plan_refs 只校验引用类型，不联查 Event。允许全局空步骤本身合理，但缺成员条件校验。 |
| 新建与更新 | `S/backend/src/digital_tutor/planning.py:25-74,92-110` | 只在 next_action 非空时核目标范围/动作类型；没有空步骤的推进完整性约束。 |
| daily 成员事务 | `S/backend/src/digital_tutor/planning.py:111-136` | 解出新旧引用后，只拒 done/cancelled。pending/in_progress 且 null/null 仍可存为今日成员。计划仅存 event_id/rank，没有另一份动作被遗漏。 |
| 日常角色稿 | `S/agent/prompts/planner_daily.md:8` | 已要求“为入选责任确定原目标范围内的 next_action”；事实是模型未遵守时，工具仍返回成功。只继续强化话术无法替代事务门禁。 |
| 上下文 | `S/backend/src/digital_tutor/context.py:27-30,73-79,114-117` | daily 收到全部开放责任的 next_action/waiting；绑定 event 的 service 得到当前完整 Event，不会从旧模型草稿取步骤。 |
| Scheduler | `S/backend/src/digital_tutor/scheduler.py:27-44` | pending/in_progress 必须 next_action 真值才建 step；waiting 只有 review_at 到点才建 review。null/null 两者都不触发。 |
| 日任务结束 | `S/backend/src/digital_tutor/scheduler.py:96-99`、`jobs.py:173-181` | 今日计划存在即确认 daily 提交完成/跳过重复任务；不会检测成员是否可继续。 |
| 发送资格 | `S/backend/src/digital_tutor/send_policy.py:52-60` | 主动发送另需今日成员、可执行状态、未待更新及当前步骤等条件。Scheduler 遍历所有责任，不等于所有责任获主动发送权。 |

事务本身正常：`S/backend/src/digital_tutor/application.py:83-98` 将 Event、Plan、回执与家庭版本原子应用，已有回执在重新校验前重放。这不是数据库不一致或丢更新；问题是原子提交的对象缺少应有条件。

程序覆盖也解释了“全绿”：`accept_stage4_daily.py:187-193` 只对 due_today 检查入选步骤；create_and_reuse 检查 ID 复用，inbound_interleave 检查撤权/答复，没有统一成员完整性断言。候选测试公共 `command()`（`backend/tests/test_stage4_candidates.py:56-70`）本身经常创建并选中无步骤新事项。这些测试需补合法动作/等待，不能用已有绿色结果否认新缺陷。

## 3. 下一次执行会怎样：新复制库实测

运行：`D:/conda/python.exe work/stage4/plan-research-20261005/check_daily_null_steps.py`。使用精确 K 模块、两案原业务日 08:00，分别在 daily/full 新复制库运行 20 次 Scheduler.tick；没有 Worker 或模型、没有渠道调用。

| 复制案 | daily 模式 | full 模式 | null 事项结果 |
| --- | --- | --- | --- |
| inbound_interleave | 0 新 step | 0 新 step/review | 唯一今日成员不排入执行队列 |
| create_and_reuse | 0 新 step | 首扫仅为两个有步骤正式 Event 各建 1 个 step，其余 19 扫无新增 | 第 3 项不排队；历史 waiting-without-review 同样不排 review，但它有明确等待合同 |

4 份复制库家庭业务版本均不变，原两库 SHA 不变。daily 模式禁 step 是预期（`runtime_config.py:30-32`），不算缺陷。full 结果证明缺口存在于派生队列前，不是发出后失败。

同日不会再 daily，因为今日唯一计划已存在。来信仍可由 service 正常处理，真实进展可沿 report→progress 改原 Event，次日也可由 daily 再评估；这些都不是当前系统已经安排好的自动补步骤。一个旧排队 step 遇到当前步骤已清空，会因 step_key 不一致而 obsolete（`scheduler.py:73-78`），不会替空事项补动作。

## 4. 最小修复建议：有效成员要有下一步或真实等待

1. **不增 Plan.next_action，不把所有 Event.next_action 改永久必填。** Plan 保持名单/排序，Event 是唯一责任。未选中旧责任允许无步骤；完成项保留在历史计划但不执行。这里只要求当前提交最终有效成员不是 pending/in_progress 的无解释空壳。
2. 在 `commit_daily` 解引用、应用新建/更新后、写计划/回执前，验证所选 Event：pending/in_progress 必须有有效且原目标内的 Step；waiting 必须有有效 reason/resume_condition，review_at 仍可空。等待项入选表示保留今日资格，**不表示等待中可发**。有依据暂不能推进的事项应显式 waiting；不能为了过门禁自动合成等待理由。也可以合理不选该责任，继续完整保留目录。
3. 复用一个小型有效成员校验，在 `commit_progress` 对报告关联、且属于今日计划的更新后 Event 同样核对；done/cancelled 是合法终态，waiting 是合法阻塞态；从 waiting 恢复为 in_progress 必须同次给有效步骤。不要给 progress 新增成员权，未选责任更新之后仍不能主动联系。无需新角色、循环或任务队列。
4. 保留现有原目标范围、证据版本、完成条件、暂停、update_pending、窗口和发送三层检查。内部 verify_learning/verify_operation 不要求已经发消息，也不能等同于主动联系；不要用“只遍历今日成员”粗暴修改 Scheduler，误伤原目标内部核实。waiting.review_at 到点只创建无外发 review，计时器本身不能解除等待；只有文字 resume_condition 而无对应事件输入/报告通路时，不能宣称自动恢复已实现。
5. 工具返回明确成员 ID/缺失条件的业务错误，整个提交回滚且无成功回执，允许现有 Hermes 工具纠错路径处理。Schema description/角色稿同步说明“步骤或明确等待”，不加入场景答案。不放宽 EVIDENCE_MISSING；不把 candidate-source/course-binding 冒充 evidence。对缺少可引用依据的历史责任，合法最小行为是保留未选中，而不是凭空修步骤或取消。

建议新写入在 strict 运行合同执行该校验；legacy 请求/历史回执应单列兼容，不宜通过新增 Schema 永久必填使旧 payload 在回执重放前失败。校验放在 `write` callback 中，保持 payload/digest 不变和原 receipt 重放；旧 K 历史不自动回填、不重写“已通过”。旧当天计划也不能靠再次 daily 覆盖。若需要修真实旧状态，应走有真实依据且原范围绑定的现有更新路径；本研究不申请或执行旧数据迁移。

## 5. 必要复验矩阵

| 反例/正例 | 必须核对 |
| --- | --- |
| 新建今日成员省略/显式 null 步骤，且非 waiting | 拒绝；新 Event/goals/Plan/candidate decisions/receipt/家庭版本全部回滚 |
| 旧空步骤责任直接选中；同次显式清空已有成员步骤 | 拒绝；旧版本、旧步骤不变；无隐性待办丢失 |
| 同次为所选原 Event 有据设步骤；沿用旧有效步骤不写 event_updates | 通过；原 ID 不变、没有要求无意义证据更新 |
| 所选明确 waiting（含无 review_at） | 通过且不 step/主动发；到期 review 不自动解除；具备实际恢复依据后才恢复执行 |
| 未选旧空步骤/等待责任、空今日名单 | 可保留；完整责任目录仍覆盖，零意外主动联系 |
| progress 清步骤/恢复 waiting/完结 | 活动今日成员必须动作或等待；done/cancelled 正常；只处理绑定原报告/原事项，不增今日成员 |
| 老 receipt、相同请求重放、同日第二提议 | 老摘要兼容、duplicate 无版本变化；不同新提议仍 DAILY_ALREADY_COMMITTED |
| 上述两条真实路径重新冻结模型验证 | 核 actual commit 参数与最终 Event，再由隔离程序推导队列；不能只看 plan 存在、工具 ok 或模型总结 |
| 当前门禁/调度兼容 | daily/passive 零主动发送、full 内部 verify、暂停/窗口、旧 step obsolete、20 次无变化/重启不风暴 |

应将“提交时有效今日成员有动作或真实等待”做成所有 daily 场景的共用程序审计，而不是再只补某个 due_today 特例。真实语义仍须独立核对动作目标/等待理由是否有据，程序结构通过不替代该判定。

本轮只定位并复现，没有实现修复或赋予 K 新通过结论；4B/4C 原关口状态不变。
