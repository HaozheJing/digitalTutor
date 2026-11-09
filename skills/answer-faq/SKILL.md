---
name: answer-faq
description: Q01 课程安排、操作或技术问题命中适用知识库时检索取证并简洁回答；未命中走Q05，退款投诉走Q04。
---

# Q01 FAQ自动应答

依据：《最新数字员工服务动作打样》第211行，通用流程第180–205行。样例不作为当前家庭事实或执行回执。

## 目标

用适用、可追溯的知识回答眼前问题，而不是在Skill维护FAQ副本。

## 依赖

先调用 `skill_read(name="service-policy")`，同轮同版本可复用；再调用 `skill_read(name="service-policy", path="references/qa.md", start_line=1, max_lines=120)`。表达遵循 `reply-style`。若需正式分层只使用宿主已核实且适用的记录；当前未提供则为 unknown，不推定为D层；课程L2/L3、答疑服务升级层与S/A/B/C/D分开。

## 触发与事实

先完成共用五步判断：说话者、意图、情绪、类型、检索组织；身份不明用中性称呼，意图不清且影响路径时一次只确认一个问题。第183行高风险Q06/Q07为错指，按实际表使用Q03/Q04；Q06是孩子分享求助，Q07是内部回流。

## 工具执行

明确说话者和意图后用 `knowledge_search(query, scope="faq", limit=3)` 检索OpenViking；必要时 `scope="product"` 取产品资料，按返回/关联ID `knowledge_read(target={document_id:文档标识})`。核对正文、语义、document_id、uri、revision、status、business_status、publication_status与课程/活动适用范围；分数只用于排序。

命中内部导航或未审核资料只说明找到了问题线索，不等于批准业务答案。当前云端导航的approved_business_answers=0不能由关联产品草稿“互相证明”成已核实；以本次返回状态为准。政策、权益、订单、物流、退款进度仍需当前适用记录/接口，不把样例承诺写成事实。

只有本次证据足够才直接回答当前问题；必要条件保留，答案完整就结束，不默认追加延伸内容；no_match、unavailable、changed、语义不匹配或关键事实不足转 `escalate-unanswered-question`。不从旧上下文、Skill示例补齐FAQ，不因需要快速回应跳过检索。

## 输出与完成标准

10分钟内首次回应为服务目标。知识检索成功不等于答复送达，导航命中不记为已解决。

当前问题用 `send_message(body=body)` 交付（每条文字≤50字）；实际要求查看已有高光且素材可用时，同次传入已核实的 highlight_ids。当前 event_id 与接收家庭由宿主绑定，交付后由宿主核验并收尾。平台accepted只是受理，最终sent才记已发；unknown不盲目重试。普通回复不为完成动作而强行重排当天计划。
完成、实质回应、明确解决分开记录；未兑现后续事项仍保留缺口，不能用一段安慰话宣布全部闭环。

## 案例

原文没有标注本动作的专属完整案例；按需读 [examples.md](references/examples.md)：`skill_read(name="answer-faq", path="references/examples.md", start_line=1, max_lines=120)`。其中仅为结构说明，不是知识库标准答案。
