# 主动动作数据接口

部署在独立实验数据库 `runtime/live.sqlite`，单个已绑定家庭。工具只有读取与受控交付能力，模型不能修改触发事实或联系策略。沿用真实消息增量读取、只读 OpenViking、原生会话与调度，原 Hermes 不变。

`proactive_facts(kind, scope, day, body, observed_at, valid_until, source)`：body为JSON对象，day/valid_until为上海业务日期，observed_at为带时区ISO时间。source在底层保留business_api或sql_mock。不同班期、书目版本、周/月和业务节点应使用稳定且独立的scope；更新同一记录用相同kind/scope/day。没有可靠数据时保留null、coverage_complete=false或缺行，不能填零冒充已核实。

|kind|body 必需信息|
|---|---|
|learning_day|expected、completed非负整数，coverage_complete布尔值，lesson_titles；完课事件须有带时区completed_at，缺课提醒须有missing_lesson_ids。一个自然日一条；聚合数据不能替代多课逐课事件。休息日明确expected=0，缺行不等于休息|
|enrollment|start_date、active；scope是班期；H01另需first_lesson对象的id、completed=true、completed_at，不能以当日全课完成代替首课身份|
|seven_day_window|start、end；恰好七个自然日，窗口由业务配置，不自动推断|
|calendar|kind=holiday/exam、title、applicable；day是实际节点日期|
|book|title、scope_confirmed、coverage_complete、shelf_lit、completed_on、lessons逐课completed；scope包含书目和版本|
|quiz|history_complete、attempts；每次含day、total、correct、question_answers逐题question/answer/correct及support；scope明确首次判断范围|
|ranking|final、published_on、ranking_type=quiz_accuracy、rank；晋档另需same_metric、higher_tier_is_better、previous_tier、current_tier；scope包括榜单周期与指标|
|expression|teacher_selected、transcript_verified、quote、lesson_context；保留精确原话|
|rotation|week_start、form（四种已定义轮换之一）、lesson_material|
|decision|confirmed、review_on、learning_evidence；scope是具体业务节点|

生日沿用family.birthday的ISO生日字段，源缺失时由本实验SQL fixture补齐并在business_audit记录字段来源。其他档案继续通过原profile接口。学习接口与已有lessons的今日完成数冲突时返回未知，不能因此产生完课或连续学习成就。

19个判断函数统一位于proactive.py。action_candidates会调用代码生成、刷新候选，planning只选择eligible；未知数据、日期不符、已交付、被更具体动作覆盖分别返回不同状态。模型可见上下文包含依据及观察日期对应的业务范围，底层来源标签不进入业务提示词。

proactive_candidates保存候选与条件依据；proactive_receipts保存动作范围去重及交付键；proactive_pending保存延后恢复；所有实际发送仍写deliveries模拟账本，不接真实发送器。发送前在同一SQL事务中重新判断业务条件、当日暂停、交流状态、时间窗口、全局次数和间隔。

联系策略位于proactive_settings的contact记录，默认非必需daily_limit=2、min_gap_minutes=60。独立S01/S02课堂消息和即时回复不占非必需额度；S02与其他可选动作合并仍计额度，暂停、去重和间隔仍生效。缺课提醒通过稳定课节ID跨S08/S09共享每周两次上限，历史缺少ID时阻塞；新增S03/S05/S06/S07接入前必须复用相同计数逻辑。

service-samples-20261020-v2 固定入口为06:30规划、07:00 S01、周末09:00补读/周报、09:30日历检查、19:00晚间复查。冻结测试用service_tasks业务时钟；非测试用原生排期，旧07:30/18:00固定排期按确切标题迁移。条件刷新发生在检查及已排执行点，尚无逐条完课事件实时唤醒；有completed_at时S02/S04候选给出完成后30分钟窗口，无时间保持unknown，不能宣称准时30分钟服务已经实现。S02只在首周，S04自然周一次；H01首日首课完成时间作为窗口起点。首月图片仍按已核验素材candidate_at/expires_at可用性，不提前使用未来图片。

非测试SQL适配交付形式为文字模拟回执；UID00001首月已接真实微信文字/已有高光图片，核对outbox与平台sent。birthday与原生日高光合并一个节点；语音、勋章、视频与真人交接仍需独立能力，不把文字回执等同于这些产物已完成。

## SABCD只读接口

`student_tier()`只读当前绑定UID，无配置返回unknown。运营配置文件为`config/student-tier.json`（忽略、不入Git），包含`endpoint`（HTTPS）、可选`headers`、非测试时的`uid`。GET追加uid，5秒超时，不跟随重定向，响应上限64KiB。JSON必须返回`uid,level,source,observed_at,valid_until`，level仅S/A/B/C/D；source为非空短字符串，时间必须带时区且覆盖当前业务时间。例：`{"uid":"00001","level":"A","source":"student-tier-api","observed_at":"2025-10-13T00:00:00+08:00","valid_until":"2025-11-16T23:59:59+08:00"}`仅说明格式，不是已配置测试事实。

当前没有收到真实endpoint/字段映射，接口状态为未配置。2025冻结时钟不会接受2026才观测到的评级；历史查询需要业务接口返回相应历史版本。接口返回等级不自动构成分层策略，各级动作差异尚待业务定义，不能用排行榜tier或课程L2推算。
