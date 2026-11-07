---
name: supervise-learning
description: 按家庭来信处理请假、改期和补读咨询；按系统任务将已核实的督学候选路由到 S03/S05/S06/S07。
---

# 督学动作路由

## 家庭发来消息

本轮是家庭提出的请假、改期、补读求助，或家庭在回复一项督学安排时，先调用 `skill_read(name="supervise-learning", path="references/inbound-supervision.md")`。依据本轮原话和紧邻上文，选附件中适用的一类回应。家长只是商量办法时，回答眼前的问题；要求变更已有服务事项时，才转 `manage-service-arrangements` 核对可办事项与授权。只处理当前来信，不从来信生成主动督学任务。

## 系统唤起主动督学

沿用 service-policy。使用 daily_context、action_candidates 和正式 student_tier 获取事实，不根据家长一句描述自行生成可发送候选。

| 动作 | 适用事实 | 加载的独立Skill |
|---|---|---|
| S03 | 今日新课已完、历史已到期缺课仍在 | remind-historical-makeup |
| S05 | 当周连续两个应学日零完课 | support-zero-completion |
| S06 | 课程序列前后已完而中间缺课 | prevent-lesson-skipping |
| S07 | 正式阈值确认上周偏低、本周零完课、周三入口 | rebuild-reading-rhythm |

用 service_skill(name) 读取匹配动作正文，再按其候选与工具核验执行；没有候选/稳定课节ID/排期能力要保留具体缺口，不能借S01、S09或高光代发。多个动作涉及同一缺课时沿用共享计数、合并和家长约束。
