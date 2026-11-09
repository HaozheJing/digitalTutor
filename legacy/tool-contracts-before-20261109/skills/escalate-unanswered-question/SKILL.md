---
name: escalate-unanswered-question
description: Q05 意图明确但知识库无可靠非高风险答案时如实说明核实缺口并尝试有回执的人工流转。
---

# Q05 未命中兜底

依据：《最新数字员工服务动作打样》第215行，通用流程第180–205行。样例不作为当前家庭事实或执行回执。

## 目标

承接未知问题，避免编造政策与无法兑现的处理承诺。

## 依赖

先调用 `service_skill(name="service-policy")`，同轮同版本可复用；再调用 `service_reference(name="service-policy", path="references/qa.md", start_line=1, max_lines=120)`。表达遵循 `reply-style`。若需正式分层则用 `student_tier`，unknown 不推定为D层；课程L2/L3、答疑服务升级层与S/A/B/C/D分开。

## 触发与事实

先完成共用五步判断：说话者、意图、情绪、类型、检索组织；身份不明用中性称呼，意图不清且影响路径时一次只确认一个问题。第183行高风险Q06/Q07为错指，按实际表使用Q03/Q04；Q06是孩子分享求助，Q07是内部回流。

## 工具执行

先核对意图是否清楚；不清楚一次问一个关键问题，明确的提问无需机械重复追问。保留已执行 `knowledge_search` / `knowledge_read` 的查询、来源、no_match/unavailable/changed/未核实原因，必要时调整一次相关查询。导航命中但业务事实未确认也属答案未核实。

告知需要核实的具体点，收集最少必要信息。效果疑虑转 `address-learning-concern`，明确退款投诉转 `handle-refund-complaint`；不能把高风险问题留在普通兜底等待。

只有实际人工接口接受请求、明确负责人及可执行时限，并留下接收/排期回执，才承诺答复截止时间；没有接口则把核实/交接记pending，不说“已记录，X小时必回”。原文这一句是有履约能力时的表达结构，不赋予系统自动生成工单的能力。

## 输出与完成标准

30分钟内先回应是目标，不是默认30分钟内问题必解决。实际承接、人工受理、按时回覆、用户解决分别记账；无人工回执不得记已升级完成。

当前问题用 `reply_to_message(event_id, body)` 交付（每条文字≤50字），实际要求查看已有高光且素材可用时才 `reply_with_highlights`，随后 `finish_event(event_id, day)` 核验。平台accepted只是受理，最终sent才记已发；unknown不盲目重试。普通回复不为完成动作而强行重排当天计划。
完成、实质回应、明确解决分开记录；未兑现后续事项仍保留缺口，不能用一段安慰话宣布全部闭环。

## 案例

原文没有标注本动作的专属完整案例；按需读 [examples.md](references/examples.md)：`service_reference(name="escalate-unanswered-question", path="references/examples.md", start_line=1, max_lines=120)`。其中仅为结构说明，不是知识库标准答案。
