---
name: care-calendar-day
description: I05 节日或家庭已确认考试节点的轻量关怀；按自然日历核验适用性，生日交给统一生日Skill。
---

# I05 节日与考试关怀

依据：《最新数字员工服务动作打样》第121行。样例不作为当前家庭事实或执行回执。

## 目标

在适用节点提供带孩子名字与阅读元素的温暖关怀，避免额外学习负担。

## 依赖

先调用 `service_skill(name="service-policy")`，同轮同版本可复用；再调用 `service_reference(name="service-policy", path="references/interaction.md", start_line=1, max_lines=120)`。表达遵循 `reply-style`。若需正式分层则用 `student_tier`，unknown 不推定为D层；课程L2/L3、答疑服务升级层与S/A/B/C/D分开。

## 事实、触发与频率

自然日历证明当前节日，或家庭明确确认了考试节点，并且适用于当前家庭、日期是今天；每个节点去重。笼统考试季不能证明孩子今天考试，不能自行臆测家庭习俗。生日统一读取 `celebrate-important-day`，不从这里另发生日祝福。

## 工具执行

`daily_context` 读取真实昵称与节点，`action_candidates` 获取对应候选；主动发送前读 `proactive-service` 并 `check_proactive(candidate_id)`。只有eligible才 `deliver_proactive(candidate_ids, body)`，沿用共享频控、同日合并和暂停；节点不存在、未知或已送达就不发。

文字中带有合适的阅读元素，引用具体课程时必须有当前课程依据；不保证考试成绩、不借关怀催课。私聊文字本身是I05允许形式，有已有匹配贺卡时才能随实际能力发图；缺图不调用生图，也不谎称卡已做好。普通关怀不强行添加高光能力比较。

## 输出与完成标准

文字/已有贺卡实际送达才记相应交付，accepted仍待确认；unknown不盲目重发。纯文字可完整满足I05允许的文字关怀，不谎称图卡完成。内部任务 `service_task_finish` 留真实依据、结果或受阻原因；有效性另看家长/孩子实质积极回应。

## 案例

原文没有标注本动作的专属完整案例；按需读 [examples.md](references/examples.md)：`service_reference(name="care-calendar-day", path="references/examples.md", start_line=1, max_lines=120)`。其中仅为结构说明，不是知识库标准答案。
