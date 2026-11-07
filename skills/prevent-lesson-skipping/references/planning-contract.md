# 统一事项规划契约

仅用于 agent-workflow-v1。保留原有适用条件、联系限制、时点、窗口、优先级。晨间与完课规划仅提交单次任务，执行只处理当前任务，完整最终发送成功才完成，不追加跟进或七天复查。旧模式仍按原调度运行。正文与结构化约束不一致时停止安排并报告缺口。

```json
{
  "skill": "prevent-lesson-skipping",
  "enabled": true,
  "mode": "agent-workflow-v1",
  "allowed_triggers": [
    "lesson.completed"
  ],
  "readiness": "existing_adapter",
  "completion": "all delivery parts finally sent",
  "dedupe": "existing candidate dedupe_key; one matter and one task",
  "contact_policy_ref": "existing published constraints; no numeric changes",
  "amendment_scope": "same task only; never change Skill; no follow-up",
  "required_facts": "current family, conversation batch, learning, constraints, task revisions and deliveries",
  "timing_mode": "existing_completion_event_window"
}
```

# S06 后台执行约定

供完课规划和工程核验读取，普通表达任务不预装。

沿用completion_service/completion_planner现有资格：同一报名课程序列，最近一个已完成的前课和本次已完成后课之间，完整序列中仍有已解锁未完成课节。休息日和缺失行不能推成缺课。窗口沿用本次实际完课起至30分钟内，且不跨业务日；不改变资格算法、S03合并、事项账本或稳定课节去重。

同一缺口模式一次，同课跨动作每自然周最多两次；联系暂停、静默窗口、日额度、候选/任务绑定和执行令牌沿用现有交付约束。候选同时返回前课依据，宿主每次查询并装载前后课、中间缺课、近期交流及一节待补课程正文。模型拟消息并调用deliver_proactive；常规结果由host_receipt依据真实回执登记，accepted保持等待，sent才确认送达，未知或部分交付停止而不重发。

消息送达不完成账本里的学习目标。缺课补齐须真实完课事件；电话、真实课程入口/二维码、复约与人工转交没有工具回执就不声称已执行。隔离试跑使用合成开发夹具和模拟出口，不代表生产或自然路由质量验收。
