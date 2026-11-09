---
name: answer-family-question
description: 对需要业务答疑的家庭问题选择Q01–Q07，按需查证后直接回答当前问题；普通接话不启动完整答疑流程。
---

# Q01–Q07 答疑路由

依据：《最新数字员工服务动作打样》第180–225行。样例不作为当前家庭事实或执行回执。

## 目标与依赖

先 `skill_read(name="service-policy")`，再 `skill_read(name="service-policy", path="references/qa.md", start_line=1, max_lines=120)`。回答当前真实问题并交付可核验结果；FAQ正文只由OpenViking维护，不塞进Skill或案例。

普通问候、同意、致谢以及没有新增问题的确认，按 tutor-main 与 reply-style 自然短答即可，不为这些接话启动检索、全套答疑路由或新的建议。对实际业务问题使用下列判断。

## 五步判断

1. 根据当前原话识别家长本人、孩子或家长代问，采用适合对象的讲法。连续交流直接接话，不重复称呼；需要称呼时按 reply-style 使用已确认身份。对象不明但不影响答复时中性表达，只有影响解释或授权时才确认，不为称呼额外追问。
2. 确认课程安排/操作、学习内容、效果疑虑、退款投诉、分享求助等意图。不清楚且影响路径时一次问一个问题；明确退款无需反复确认动机。
3. 有情绪时认真处理具体问题，按需要表达体谅，不默认先复述或安慰。情绪承接不等于确认未核实事实。
4. 根据事实与风险选择下表的具体Skill，不机械按关键词选FAQ。原文第183行高风险Q06/Q07与表冲突，按表使用Q03/Q04；主体L2→L3/L3是服务升级标记，不是课程级别或SABCD。
5. `skill_read(name=所选Skill)`，按动作实际检索、组织、交付，并在答疑结束读取Q07处理内部结果。

| 动作 | 对应Skill | 时间目标/边界 |
|---|---|---|
| Q01 FAQ | `answer-faq` | 首次10分钟内；OpenViking命中须可用且适用 |
| Q02 学习内容 | `explain-reading-content` | 首次5分钟内、24小时闭环；完整课程、双层/儿童讲法 |
| Q03 效果疑虑 | `address-learning-concern` | 及时回应、48小时二次跟进；真实学情和A/B方案 |
| Q04 退费投诉 | `handle-refund-complaint` | 及时承接、真人为主；真实交接回执 |
| Q05 非高风险未命中 | `escalate-unanswered-question` | 30分钟内承接；只承诺可执行时限 |
| Q06 孩子分享/求助 | `respond-child-sharing` | 10分钟内、语音≤60秒；紧急困扰立即优先 |
| Q07 内部回流 | `archive-question-outcome` | 每次答疑后；不额外触达、不把未审答案入库 |

## 工具与交付

知识类实际调用 `knowledge_search(query, scope="faq", limit=3)`，必要时product范围或 `knowledge_read(target={document_id:文档标识})`。核验正文与来源、版本、审核状态和适用范围；导航命中/草稿不等于批准答案。事实学情查当前接口，课程问题读完整课程；示例及旧上下文不能代替检索。

当前回复用 `send_message`，已有高光须先授权读取并在同次发送传 highlight_ids；事件收尾由宿主完成。内部服务任务才使用 `service_task_finish`，接口不可互换。语音生成与微信投递、人工备料与真实接收、计划建议与成功排期分别留证；缺能力如实记录，不编造工单、电话或送达。

## 完成与有效标准

实际回复疑虑并兑现已承诺动作才算对应完成；用户明确理解、解决、感谢等另记有效回应。发送成功不等于满意，感谢不等于退款成功，沉默不是已解决。

## 案例

具体动作尾部均链接本动作的来源与表达结构。路由边界见 [cases.md](references/cases.md)，通过 `skill_read(name="answer-family-question", path="references/cases.md", start_line=1, max_lines=120)` 按需读取；它不是FAQ知识来源。
