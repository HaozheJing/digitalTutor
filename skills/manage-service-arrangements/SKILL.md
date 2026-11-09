---
name: manage-service-arrangements
description: 家长要求取消、暂停、恢复或调整服务安排时，核对实际事项并提交有回执的局部修改。
---

# 家长修改服务安排

本流程处理当前家长原话要求的局部修改，不执行每日规划，不重列整个服务目录，不创造新任务。

1. 调用 workflow_read(view=context,purpose=amend)。读取当前 tasks、pause_targets、原话来源和 context_token；旧聊天或之前回合的令牌不能复用。
2. 只修改与原话对应、状态允许修改的实际任务。沿用其 task_id 和整数 revision；取消用 cancel。未受影响的任务不要提交，保持现状。
3. workflow_commit 必须包含 request_id、context_token、decisions 和本轮 evidence（source_message_id、逐字 quote）。每项决策包含 skill、decision、reason、task_id、revision。不要将内部规划任务或业务Skill目录当成实际任务。
4. 暂停某类服务必须独立保存 contact_constraint，不能只取消已有任务。即使 tasks 为空，也从 pause_targets 选择对应 skill，提交 scope={kind:"skill",skill:该服务名}；家长说今天时传 period="today"，由宿主确定北京时间从当前时刻到今天23:59:59。此约束覆盖有效期内现有及后续新建事项，不影响其他服务。仅登记约束时 decisions=[]，仍须 request_id、context_token 和本轮逐字 evidence。单个类别暂停不能扩大成 scope=all；只有本轮家长明确要求暂停全部主动服务时，才用 scope={kind:"all"} 保存同一授权时间范围的全部暂停，即使当前没有执行任务也必须提交。不编造任务；固定服务时点不能自行改写。
5. 返回 committed 且 contact_constraint 回执范围/有效期对应请求（任务修改则核对 decisions），才能向家长确认修改成功。失败后用 workflow_read(view=context,purpose=amend)刷新；view=refresh只用于正在执行的主动任务。
6. tasks 为空不妨碍保存类别暂停。类别不明确或不在 pause_targets、事实不足或提交失败时，不能确认已暂停，必要时使用 request_human_confirmation。已发送和在途交付不能撤回；新约束只管尚未授权提交的发送。不要发送“已取消/已暂停/以后不会发”等无成功回执的承诺。发送成功不代表安排修改成功。
7. 只有本轮回复要完整履行一个已有事项时，先用 workflow_read(view=tasks) 找到实际事项，再用 workflow_read(view=context,purpose=fulfill,task_id=实际ID) 获取专用上下文。此时仅开放 workflow_fulfill，沿用该事项ID、整数revision、context_token，并先读对应业务Skill。不能履行空账本、内部规划或节日图文；不再额外 send_message 重复交付。同一次上下文只用于修改或履行，切换用途须重读；纯查看用purpose=inspect。
8. 修改安排后最后使用 send_message 回复实际结果；事件收尾由宿主完成。本流程不调用 service_task_finish。


## 第一批查询接口迁移说明（开发候选）
当前模型查询入口如下；旧实现仅作兼容保留，不是本轮调用指令：
- knowledge_browse(scope="curriculum") 查看正式书目；book_id 查看书内课次映射。简称由智能体判断，不登记别名。
- knowledge_read(target={level:"L2",week:全课程周,day:周内日},parts=["reading_qa"或"course"]) 精确读取；各部分缺失分别处理。书内课次使用 target={book_id,book_lesson_no}。
- knowledge_search(query=问题,scope="curriculum"或"faq"或"product") 返回候选，须用 knowledge_read(target={document_id}) 读取后判断依据。不得将未审核FAQ作为正式政策。
- conversation_read(source_id=消息引用) 读取原话；不传source_id则读窗口，续页保留snapshot和offset，不能混用两类参数。
- workflow_read(view="context") 默认仅查看；明确修改已有安排时使用 purpose="amend"，履行已有事项时使用 purpose="fulfill"及task_id。读取不证明修改或交付完成。
以上仅替换查询接口，不改变触发时机、联系权限、服务强度或业务停止条件。

## 执行与回执边界

- 查看时结合任务的 execution_state，分清已排期、等待发送回执、发送结果待核实及等待人工。wakeup 只描述既有事项，不授权新增服务时间。
- due_at 是原定执行时间，不是实际送达时间；本轮提前履行后仍可保留原 due_at。sent 只支持“已经发送”，缺少明确送达时间证据时，不补写“几点已送达”，也不把交付创建时间当送达时间。
- workflow_fulfill 返回 bound 只说明原事项已绑定交付；检查 delivery 的实际状态。queued/accepted/partial/unknown 均不能当成发送完成。重复请求沿用原 request_id 和原参数，回执仍属于同一交付，不补调 send_message。
- 固定时点不能因家长要求就擅自改写。改期不被当前契约允许时如实说明，不能自行创建替代任务、把请求改成取消或扩大暂停范围。
- 人工转交后本轮停止模型发送；人工回复或撤回仅结束该转交，不证明业务问题解决，不自动重跑原事件。后续只按新事件和当前记录继续。
