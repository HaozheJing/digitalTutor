# test15 / native06 实施范围核对

本次只读核对源码、冻结清单与闭合导出，不替独立工程/语义评审签署关口结论。源码路径以下均相对 `work/stage4/4c-development-l/`。

## 已实施合同与入口

- **Planner 规划、Service 执行。** `planning.py:94/131/135` 分别接 daily、日内 admission 与 progress；`admission.py:108` 将 admission 约束到绑定原 pending/决定版本，沿同日计划修订。`scheduler.py:17/68` 和 `jobs.py` 继续使用原唯一队列及 Worker；`scheduling.py:83/117/141` 核 execute_at、当前限制、实际步骤能力，并按执行时间、期限、rank、ID 排序。持续承接跨日依赖原 grant/source/范围，不能靠次日 baseline 延长原约定。`hermes_runtime.py:64–88` 仍实例化原生 AIAgent 并调用 run_conversation，没有新增 agent loop 或原生 todo 作为业务事实源。
- **完成条件贯通。** `service_requests.py:82–118` 核原文、保存 source/pending 条件；`candidates.py:97/254/282` 投影并核对候选条件；`planning_records.py:16–37` 保存 Event/Step；`domain.py:87/118` 保留两层可空条件；`execution_projection.py:9/70` 向 Service 展示。`report_contract.py:65/80` 校验 Service 的真实 assessment 与证据；`planning.py:67–73` 才将原 Event 结案。空条件允许 Service 自行选择判断依据，但仍须 positive assessment 和原硬条件：goal 要有匹配课程身份、有效可信的原课节完成证据；delivery 要原 Event/revision/recipient、全部必需 part 的实际 sent/delivered 回执。草稿、提醒、安排确认均不替代完成。
- **两层来信责任与真实外发。** `request_responsibility.py`、`wechat.py:85`、`send_policy.py:23` 区分待决定登记、真实决定后确认和原事项交付；确认 phase 由实际 receipt/来源/决定身份派生，准备与 dispatch 均复查。旧消息不能抢同事实报告，unknown 确认仍保留独立核账；真实 delivery+assessment+progress 后才结清原未来意图。普通问题不因登记而丢失，goal 安排确认可结清消息但不结案学习目标。

## 本版证据及边界

服务器 test15 为 **532/532，28.783 秒，无 skip**（tests.log 与 engineering-test15-freeze）。本次另逐项重算当前本地 L：189 个文件与 test15 source-manifest 完全一致。native06 三组闭合包及 `test15-model-runs.json` 记录 **10/10 程序通过、52 native、384 API，全 Flash**；该汇总仍 `gate_passed=false`，不把程序结果代作语义接受。

三条日内交付链验证了源请求→决定→真实确认→跨日 step→实际交付→assessment→原报告/progress→原意图结清。`daily-chain` 实际由 daily 创建 goal+delivery，运行完成与学情更正原 ID 重开，并恢复次日 daily。特别区分：`intraday-empty`、`intraday-continuous` 实际抽取了非空“第1天的文字学情总结”，不能仅按用例名称宣称空条件实测；真正 **null goal+delivery** 的模型判断证据来自 daily-chain 的配置、最终记录及 `null_goal_and_delivery_assessed` 检查。

continuous 的真实发送为 16:01:55.146530，比 16:00 起点稍晚约115秒，仍在原授权窗口内；本轮用户已明确接受，记录为体验事实，不新增阻断或时间优化任务。固定业务钟两案不冒充连续钟证据。

## 不等于原 4C 全量验收

主方案4C六项仍需逐条由独立评审确认。本次 native06 在 `accept_stage4_daily` 中只重新运行 family_requests；**create_and_reuse、due_today、empty、coverage_gap、report_gate、inbound_interleave** 不是本版同批完整真实模型矩阵。不得用旧版本通过挪作 test15 通过。具体尚不能由本批独立覆盖的是：全部旧等待/无 review_at/昨日未入选责任目录；空候选、覆盖不足及迟到数据的完整主线；报告运行中插入和新来信抢占的全部交错；未确认规则、候选注入和来源外 goal_key 的真实模型故障分支。532 程序回归可证明对应受测守卫，不能补齐缺少的本版语义轨迹。

用户后续授权已把“日内正式承接”纳入4C调整范围；这不授权把迟到学情当成任意追加主动事项的来源。当前完成/更正闭环是本轮集成范围，仍不宣布原4D所有改约、多媒体/多部分交付、unknown/停用/超时/窗口竞态或4E重复两日矩阵完成。正式 Skill/指南正文未因这些合成来源链自动获得新的模型授权或语义通过；未开启真实微信、真实家庭或第五阶段。

## 隔离与 vendor / H 证据限度

L 创建记录 `intraday-l-preflight/baseline.json` 记载从冻结K逐153项核验复制；服务器 vendor/runtime 为共享既有路径。test15 相对 test13 的差异为3业务源、4验收脚本、3测试，未改提示词；189项冻结清单不包含 vendor，不能据此断言整个 vendor 文件系统现状。

既有原生调研 `work/stage4/plan-research-20261005/hermes-research.md` 记录 vendor pin `f97608f178d1ffeca59860195ab7da295f7c8e5f`，当时跟踪文件 git status 为空。本次没有重新跑服务器 vendor 状态，故“本轮未提交 vendor 修改”以限定的实施路径/差异为依据，不扩展成最新整树无变化证明。

本次用既有 H 服务器基线清单重算**本地主目录 backend/agent 86项，零差异**；H 文档后续更新不在此“源码未替换”断言内，也未重新检查 H 全部服务器数据。机器核验见 `implementation-scope-final-checks.json`。所有原历史运行及失败证据继续保留。
