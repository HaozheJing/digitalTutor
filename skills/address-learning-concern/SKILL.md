---
name: address-learning-concern
description: Q03 家长说没效果、想暂停或考虑不续时，接住情绪、查实际学情证据并提供可选调整方案。
---

# Q03 效果质疑应对

依据：《最新数字员工服务动作打样》第213行，通用流程第180–205行。样例不作为当前家庭事实或执行回执。

## 目标

回应真实效果疑虑，帮助家长选择可行支持，保留风险升级与二次跟进责任。

## 依赖

先调用 `skill_read(name="service-policy")`，同轮同版本可复用；再调用 `skill_read(name="service-policy", path="references/qa.md", start_line=1, max_lines=120)`。表达遵循 `reply-style`。若需正式分层只使用宿主已核实且适用的记录；当前未提供则为 unknown，不推定为D层；课程L2/L3、答疑服务升级层与S/A/B/C/D分开。

## 触发与事实

先完成共用五步判断：说话者、意图、情绪、类型、检索组织；身份不明用中性称呼，意图不清且影响路径时一次只确认一个问题。第183行高风险Q06/Q07为错指，按实际表使用Q03/Q04；Q06是孩子分享求助，Q07是内部回流。

## 工具执行

先回应具体失望/困难，结合宿主已装载上下文与 `learning_query` 获取当前可见学情，`highlight_lookup` 取已有高光，必要时 `conversation_read` 核实历史承诺。事实范围、完课和能力表现分开，不为挽留夸大效果，不拿其他孩子数据作比较。

基于实际缺口提出A/B两种低负担且能兑现的调整选择；让家长自主选择，不把继续试学或解释原因作为退出前置条件。明确退款/投诉转 `handle-refund-complaint`。原文主体L2→L3是服务升级标记，不能擅自调孩子课程级别或SABCD。

目标48小时内二次跟进，但当前未接通此业务的正式复约和跟进闭环。只按已授权的安排查看流程，先 skill_read(name="manage-service-arrangements")，再用 workflow_read(view="tasks") 核对已有事项；不虚构Q03候选，不借督学候选建任务，不因答疑自行排期。没有有效排期或真人接收回执就保留待安排，不承诺已预约电话或某时必回。内部人工转交可以通过 request_human_confirmation 绑定当前消息完成，但它不等于外部工单受理或已预约。

## 输出与完成标准

及时先回应。当前答复已送达、调整方案已选、48小时跟进已排期、真人已接收和问题已解决各自留证；缺后续能力只记部分完成。Q03是优先处理，不绕过暂停和真实工具拒绝。

当前问题用 `send_message(body=body)` 交付（每条文字≤50字）；实际要求查看已有高光且素材可用时，同次传入已核实的 highlight_ids。当前 event_id 与接收家庭由宿主绑定，交付后由宿主核验并收尾。平台accepted只是受理，最终sent才记已发；unknown不盲目重试。普通回复不为完成动作而强行重排当天计划。
完成、实质回应、明确解决分开记录；未兑现后续事项仍保留缺口，不能用一段安慰话宣布全部闭环。

## 案例

原文没有标注本动作的专属完整案例；按需读 [examples.md](references/examples.md)：`skill_read(name="address-learning-concern", path="references/examples.md", start_line=1, max_lines=120)`。其中仅为结构说明，不是知识库标准答案。
