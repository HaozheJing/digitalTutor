# 统一事项规划契约

仅用于 agent-workflow-v1。保留 Skill 正文和原有适用条件、联系限制、时点、窗口、优先级；此附件不增加业务动作。
晨间与完课规划仅提交单次任务；动作执行只处理当前任务。完整最终发送成功才完成，不追加跟进或七天复查。旧模式仍按原调度运行。正文与结构化约束不一致时停止安排并报告缺口，不能猜测放宽。

```json
{
  "skill": "invite-reading-interaction",
  "enabled": true,
  "mode": "agent-workflow-v1",
  "allowed_triggers": [
    "daily"
  ],
  "readiness": "existing_adapter",
  "completion": "all delivery parts finally sent",
  "dedupe": "existing candidate dedupe_key; one matter and one task",
  "contact_policy_ref": "existing published constraints; no numeric changes",
  "amendment_scope": "same task only; never change Skill; no follow-up",
  "required_facts": "current family, conversation batch, learning, constraints, task revisions and deliveries",
  "timing_mode": "fixed",
  "fixed_time": "19:00",
  "weekday": 4
}
```
