---
name: celebrate-important-day
description: I05/H10 合并生日关怀与生日高光；生日当天核验既有阅读旅程和图卡，每家庭每年一次主动祝福。
---

# I05/H10 生日祝福

依据：《最新数字员工服务动作打样》第121、159行。样例不作为当前家庭事实或执行回执。

## 目标

用孩子真实阅读足迹提供有仪式感的生日祝福，不夹带催课或续费任务。第159行“I09日常关怀”按实际表修正为I05，两个编号共用一次生日事件。

## 依赖

先调用 `service_skill(name="service-policy")`，同轮同版本可复用；再调用 `service_reference(name="service-policy", path="references/interaction.md", start_line=1, max_lines=120)`。表达遵循 `reply-style`。若需正式分层则用 `student_tier`，unknown 不推定为D层；课程L2/L3、答疑服务升级层与S/A/B/C/D分开。

H10图卡同时读取 `service_reference(name="service-policy", path="references/highlights.md", start_line=1, max_lines=120)`；主动流程读 `proactive-service`。

## 事实、触发与频率

登记生日月日等于当前业务日期，每家庭、孩子、生日年份只主动交付一次；I05与H10共用该去重事实，不能先发I05文字再另发H10图卡来计两次生日。使用接口提供的候选和去重结果，不自行伪造候选或幂等字段。2月29日不自行移到其他日期；生日未知不推测。

用 `daily_context` 取得昵称、生日及当前可见阅读记录，`highlight_lookup` 核验本次生日已有图卡。书单和金句要对应当前孩子；不足一整年就说实际覆盖期间，不能编造全年足迹。mock旧图片中的其他名字不当当前事实，正文按UID结构化信息生成。

## 工具执行

1. `action_candidates` 取生日候选，发送前 `check_proactive(candidate_id)` 核验日期、家庭、年份、触达窗口和历史送达。already_delivered 不另换I05/H10候选绕过；unknown/blocked 保留缺口。
2. 组织温暖祝福+可核验阅读片段。对比或能力解读缺证就删去，不为四要素补造退步/进步；生日祝福不要求孩子答题。
3. eligible 后 `deliver_proactive(candidate_ids, body)` 走现有图文交付；不调用生图、不把URL当已发图。无图卡且候选允许文字时仅完成文字关怀，H10图卡仍记部分完成/素材缺口。
4. 家长当前明确请求重看生日高光时可 `highlight_lookup` 后 `reply_with_highlights(event_id, body, highlight_ids)`，按该被动事件去重。主动年度去重不禁止这种被动查看。
5. 分别核验文字、图片回执；accepted不等于sent，unknown不盲重试。内部任务用 `service_task_finish` 的真实枚举及原因结束，当前消息用 `finish_event`。

## 输出与完成标准

交付一次家庭可见的个性化生日祝福；H10需实际图卡送达才完整。文字已发而图片缺失只说明文字已送达；不可整体冒报成功。家长/孩子明确积极回应才记有效，沉默不算认可。

## 案例

原文没有标注本动作的专属完整案例；按需读 [examples.md](references/examples.md)：`service_reference(name="celebrate-important-day", path="references/examples.md", start_line=1, max_lines=120)`。其中仅为结构说明，不是知识库标准答案。
