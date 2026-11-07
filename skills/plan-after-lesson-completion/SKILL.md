---
name: plan-after-lesson-completion
description: 完课后的服务规划
---

# 完课后的服务规划

你负责内部规划，不向家庭发送，不依赖 service-policy、tutor-main、plan-today 或 proactive-service。先完整读取本 Skill 和 references/trigger-checklist.md；宿主已预加载正文和附件时直接使用。通过 completion_plan_context 读取当前完课事件、逐课事实、既有事项、家庭反馈、交付与定时任务。引用资料中的真实 event_id，不使用示例或隐藏推理。

依触发清单分别判断 S02/S03/S04/S06，S05 不在范围内。每项 decision 为 schedule、keep、skip、cancel 或 unknown；reason 简述可见事实和取舍，evidence 至少包含当前 event_id。缺少证据时 unknown，不推定未完成。S02是首周逐课鼓励，先读候选的planning_requirement：required_task为true时，须为本次完课schedule，已有同事项任务则keep；暂停、人工待处理、同事项记录或窗口结束等可跳过依据由skip_basis给出。S01课前邀请、其他课的S02/S04、未来尚未送达的任务都不能覆盖本次完课。其余候选资格用于校验事实，联系必要性、家庭约定、时点和已有任务仍结合本次资料判断。

已有等价任务用 keep；不支持联系用 skip；有新依据且关联待执行任务不再需要用 cancel 并填 task_id。schedule 填带时区 run_at，须在候选窗口且不早于当前业务时间。先核对持续暂停/停止、历史反馈、已有目标和真实送达，不能用新完课信号自动恢复联系。近期每个事项只绑定一次行动任务；S03/S06 不跨新任务复用事项，不追加七天复查。等价任务沿用原事项及目标范围；最终 sent 完成本次建议交付，后续完课事实仅更新关联学情，不代表此前发送时已经补完。

用 completion_plan_commit(request_id,context_token,decisions) 一次提交完整四项判断。request_id 每次逻辑提交稳定；网络重试使用完全相同内容。资料冲突时重新读取，再判断；不要拆成四次提交，不调用普通 service_schedule、save_plan 或行动阶段的 check_proactive。completion_plan_context 已包含四项候选资格、时间窗、证据与持续事项，规划阶段以它作为事实核查入口。最后 service_task_finish(outcome="completed")。提交失败则不能宣称规划完成，可 blocked 并记录原因。
