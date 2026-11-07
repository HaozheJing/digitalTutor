---
name: plan-service-day
description: 每天06:30在已发布服务范围内提交一次性事项计划，不直接联系家庭。
---

# 当日服务规划

提交的 request_id 使用当前内部任务 ID 作为前缀；同一次提交重试保持相同 ID 和内容。新一次增量规划不能复用其他任务的请求 ID。

只读取本次上下文中的已授权Skill、事实、任务与交付。内部规划不依赖service-policy。
一个动作实例对应一个事项和一个任务，不追加回访、七天复查或自行发明事项。
06:30不能预测未来完课。完课事件继续由plan-after-lesson-completion专项规划。

调用workflow_context取得当前资料与context_token，再调用workflow_commit原子提交。
内部规划不传顶层evidence；该字段只接收本轮家庭对话的source_message_id和quote。当前事实依据写入每项reason。
decisions每项包含skill、decision、reason。每个catalog Skill至少给出一项判断。
- create还需opportunity_id和run_at，只能来自当前eligible机会。
- keep、reschedule、cancel还需task_id和revision；reschedule需run_at。
- skip表示事实支持不安排；unknown表示缺少依据。没有适用服务也保存有依据的空计划。
固定时点不能自行改成任意窗口；已错过的时点不补造准点执行，不更改频控或跨日规则。
已有任务优先keep或局部修订；同一实例不得重建以绕过去重。Skill变更需重新读取后核验受影响计划。
工具冲突时重读，只有committed回执才算保存。最后service_task_finish完成内部规划。
不能使用旧service_schedule/save_plan路径，也不能发送微信或为规划建立一个对客事项。
