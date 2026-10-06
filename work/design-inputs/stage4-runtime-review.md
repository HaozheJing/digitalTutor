# 第四阶段运行架构独立讨论稿

日期：2026-10-04。作者：独立运行审阅智能体。状态：只读设计审查；未修改业务代码、未运行模型API、未启动服务器任务。本稿基于当前本地源码与阶段三交付记录，不把拟议能力记为已实现。

## 结论与建议拆分

支持主控的4A→4B→4C→4D→4E；采纳用户补充要求，将4B拆成必须先通过的4B1被动问答链路、4B2被动服务行为。先证明知识正确进入回答和实际模拟回复，再开放每日规划与主动步骤。阶段三能力探针保留原结论，不能替代这一关口。

最小架构继续是：原生cron唤醒现有Scheduler；轻量宿主驱动现有Worker/JobQueue；Worker绑定家庭和租约；HermesHost装配三配方；原生AIAgent选择工具；既有业务事务、Outbox与回执决定实际效果。只增运行装配、内部结果和必要来源桥，不增第二调度器、模型循环或通用工作流框架。

## 已观察到的源码事实

| 依据 | 已有能力与当前缺口 |
| --- | --- |
| `backend/src/digital_tutor/hermes_host.py:41,106` | 独立子进程/profile/cwd、预算与取消存在；Worker入口只把build_context序列化，没装配角色正式运行稿或材料选项。返回no_contact由review任务类型直接推定。 |
| `backend/src/digital_tutor/hermes_runtime.py:13,37,51,62` | 原生AIAgent、实际工具集合核对和interrupt存在；默认材料关闭，短通用SYSTEM，知识绑定只能由显式宿主options传入。 |
| `backend/src/digital_tutor/context.py:25,31,104` | 三配方与必需消息覆盖存在；daily摘要不含goal_key，普通来信无event选择时不带事项定位索引，材料默认not_loaded。 |
| `backend/src/digital_tutor/jobs.py:42,51,61,98` | 家庭串行租约和优先级存在；优先级不抢占正在运行的Planner。waiting无retry_at且家庭版本不变时不再领取；finish记录的是结束时家庭版本。 |
| `backend/src/digital_tutor/scheduler.py:51,76` | 心跳、Worker事实门禁存在；daily检查实际计划，service检查解释和回复覆盖，progress检查报告消费，step检查报告或终态。review仍依赖handler传来的no_contact。 |
| `backend/src/digital_tutor/planning.py:131,151` | progress实际提交会消费报告，并提前将对应job写为succeeded。 |
| `backend/src/digital_tutor/hermes_cron.py:13,50` | 原生no_agent任务只调用Scheduler.tick；没有常驻Worker与原操作核实执行入口。 |
| `backend/src/digital_tutor/application.py:135`、`send_policy.py:20,32` | 入站原子持久化并建service任务；主动事项发送遇到未解释来信会被拦截，新消息也使旧家庭版本失效。 |
| `backend/src/digital_tutor/outbox.py:13,52,76` | 外发最终校验与原操作核实存在；核实可在停用/窗口外进行；需要宿主实际驱动，不能只依靠模型读get_operation。 |
| `backend/src/digital_tutor/learning.py:21`、`course_binding.py:49`、`application.py:230` | SQL有源行和可信绑定；add_evidence是宿主入口，不向模型开放。SQL读取本身没有形成可引用完成证据的桥。 |
| `backend/scripts/probe_native_model.py:43,58,64,94` | 阶段三探针给定工具、目标或修改字段，直接open_session/run，不经过带租约的Worker。证明接线，不证明自主闭环或Worker竞争正确。 |

阶段三交付记录为93项回归、6条基础API、3条授权材料API、原生材料/cron验证；它明确保留第四阶段未开始、真实家庭绑定及真实微信未接入。此次没有重跑这些结果。

## 对主文档的五项必要修改

以下针对所读`docs/教辅后端第四阶段开发与验收方案.md` V0.1；主控正在补入4B1/4B2，故这一项是合并检查而非重复要求。

1. **被动关口必须覆盖实际发送终点和旧队列。** 在4A只做显式装配冒烟；4B1/4B2运行使用可信、持久且默认被动的模式。禁止daily/主动step/触达式review领取，不仅禁止创建；发送prepare及dispatch都校验模式和有效、未答的入站reply_to。已有queued操作、重启、模式切换和伪造action_kind=reply也要被拦。原已发操作仅查询；内部草稿不外发。4B2可消费已有事项来信报告的progress，但不能因此放开主动step。以“预存主动任务及queued操作→被动启动/重启→外发数不变”为负例。
2. **增加progress成功后自撤权竞争的明确修复与验收。** `planning.py:151`提前写job=succeeded，`jobs.py:20`要求leased，`hermes_host.py:81`每0.1秒auth。实际Worker接线后，成功消费报告可能被监控误判LEASE_LOST并取消；现有直接session探针测不到。建议业务提交消费报告并保存receipt，运行租约由Worker统一结束；或精确识别同一租约已成功提交，不能全局忽略LEASE_LOST。验证提交后故意延迟模型返回，结果不重试、不取消、不重消费；另验证真正过期租约仍拒绝写入。这是源码推导出的竞争，尚未运行复现。
3. **把抢占后的可恢复性与普通等待分开。** 新来信先入库，撤销旧Planner/受影响主动step能力，原生interrupt，有限清理，释放原租约，再Service；不等待Planner成功。抢占任务应保留原daily/report身份、以新快照可重新领取。若直接沿用finish(waiting)记录当前家庭版本，会把刚到的新输入当成已看过，可能一直不恢复。暂时API失败也不能无retry_at无限等待。4A就验取消后恢复、无新输入的有限重试、重试耗尽可查询、原操作未知先核实；不能全推到4D。
4. **冻结内部outcome的事实约束，而非仅入口名称。** 推荐受控`service.record_outcome`（拟议），不采用最终自然语言/JSON作为唯一可靠结果。按本次job/attempt/run/lease绑定，引用当前家庭已持久的操作/报告/待规划，保留remaining_coverage与恢复条件；记录本身不bump家庭业务版本，不唤醒自身。review必须实际提交显式no_contact，禁止Host按job.kind合成；允许它附内部报告，no_contact只表示本轮无对外联系。Worker仍逐项检查责任，不由action_taken直接推导成功。发送已落库但outcome缺失时，只补收尾，不能重新发送。JSON可以是展示/导出格式，不是第二套解析驱动的行动协议。
5. **证据桥必须同时有可信生成、可读发现及更正唤醒。** 主稿已提出宿主适配，需落实模型怎样读到可引用evidence_id/version、来源失效怎样定位原done事项并生成受限复核机会。当前state/context不会列出所有新宿主evidence，单独add_evidence并bump不足以形成闭环。SQL源行+批次+精确课程/教学日映射+覆盖/时区+判定规则才生成lesson_completed；含糊的家长自述不升格。接受/取消需引用具体提议与原消息，不用“引用存在”替代语义。明确不了就澄清，不做通用自然语言证明器。

## 最小运行装配与恢复合同

- **一个业务队列：** cron只做时间唤醒；薄执行循环做可执行任务领取和原操作核实。入站由现有事务独立落库，不能排在正在运行的模型后面。进程监督使用既有服务器方式，不为此建设常驻Agent平台。
- **一个可信配方装配点：** role从session取、任务种类从job取、材料范围从批准配置和家庭绑定取；model不能传路径、修改模式或自己升级课程绑定。明确simulation绑定与真实unknown分支，选中Skill调用原生只读reader并保留必读guides哈希。
- **一个轻量运行记录：** 模型启动前关联job/attempt/run_id、租约代际、非敏感profile标识、输入/提示词/材料版本、开始时间；结束补退出原因、outcome/receipt引用和恢复条件。记录过程不改变业务输入版本。不另造Event状态机。
- **重启恢复从事实开始：** 查询原operation、报告resolution和提交receipt；已经消费的不再消费，已发的不再发。未完成责任用现有job、Event与Pending保留。新尝试新run_id，业务幂等身份保持稳定，不依赖恢复一份历史聊天来证明状态。
- **取消有边界：** 能阻止的是尚未通过dispatch最终准入的动作；已发起操作必须沿原号核实。宿主死亡后老子进程即使暂未退出也不能通过过期租约写入；需审计其终止/预算和新Worker恢复，不能把“主进程退出”等同“模型已停止”。
- **不把所有变化都当重跑理由：** outcome、心跳、无变化回执和运行日志不改变family input version。新来信/新回执/有效来源变化才触发相关责任复核。新报告只开放原关联事项progress，不开放任意日间规划。

## 各关口建议验收

| 关口 | 最小可验交付 | 必要证据 |
| --- | --- | --- |
| 4A | 实际Worker→Host→原生AIAgent→工具→事务；内部结果、抢占、租约与恢复可用 | 三配方显式冒烟；带租约progress提交后延迟；真失租拒绝；抢占再恢复；有限失败重试；vendor不变 |
| 4B1 | 被动模式的正确知识正文/Skill→有依据回答→模拟回复 | 自然问题触发实际读取；正确D01正文摘要及发送记录；缺正文/相邻课拒绝；预存主动任务及旧outbox仍不外发 |
| 4B2 | 多意图、暂停、未答、记忆/草稿/既有事项报告；被动运行边界保持 | 普通答疑0新增Planner；在途Planner让行；暂停+正常问题；原报告progress可消费但0主动step |
| 4C | 宿主来源候选→模型采用/关联/保留→唯一daily | 同目标不换key复建；全责任目录；空候选可空计划；新输入冲突新快照；unknown覆盖不推断失约 |
| 4D | 原步骤→实际操作→报告→progress及可信更正桥 | 双部分交付、未知回执、成功后退出补报告、目标型不以提醒完成、接受/取消歧义、更正原ID、截止时钟跨进程一致 |
| 4E | 冻结版本的两日自主闭环及独立语义评审 | 同一run关联完整链、独立渠道账本、S01—S14适用分支、全失败保留、无变化0API、API/程序/语义/业务接受分开 |

“非脚本化”指脚本只提供外部事实、自然问题、时钟和故障；不提供本轮正确目标、期望状态、提交字段或调用顺序，不代服务提交报告、不代Planner提交计划。正常系统角色合同与工具Schema仍必须清楚，不能为追求自主性删除必要边界。故障可挂稳定I/O或提交钩子，不靠随意sleep猜时序。

## 需主控讨论并冻结的取舍

1. **接入方式：** 推荐内部outcome工具；Planner以commit receipt终结业务，服务以outcome+实际责任门禁收尾。不要给Planner复制一套ServiceOutcome必填协议。
2. **新来信抢占：** 推荐同家庭Planner与旧主动step协作取消，Service连续来信先不统一强制抢占；它们由最新事实/最终发送门禁约束，并另验多消息覆盖。持续高频来信导致Planner让行是允许的用户服务优先，不通过并发写Planner规避串行。
3. **被动模式切换：** 推荐持久宿主模式与代际校验，切回被动使旧主动派发资格失效。4B1/4B2完成后才在独立模拟run启用主动能力。
4. **接受语义边界：** 推荐只支持可明确锚定原提议的确认，歧义进入澄清。宿主验证不能宣称普遍理解自然语言；需要模型提出引用、受限校验与独立语义负例共同验收。
5. **范围与复用：** 不必在本阶段使41个Skill都通过语义验收；按主稿首批与批准材料范围分别验，真实材料和合成夹具成绩分列。原生cron的静默唤醒证据可复用，业务新增入口和真实Worker闭环必须新增证据。

没有发现需要推翻两角色、三配方或修改Hermes vendor的理由。上述问题均可在既有边界内补齐；当前是设计发现，不是第四阶段已失败或已通过的运行结论。

## V0.2最终交叉结论

已只读核对主文档V0.2：被动capability与领取/最终发送约束、progress提交与租约结束分离、新来信撤权及fresh快照恢复均已纳入，支持进入后续逐关口合同细化；本轮不实施。

采纳主控进一步收窄的顺序：4B2只验证原事项报告持久化，完整progress业务执行留4D；4C用没有未消费报告的独立状态验daily，另验REPORTS_PENDING拒绝门禁；4D/4E再验报告消费后daily恢复。它覆盖了原建议的被动安全要求，又避免4C依赖尚未交付的4D。上文“4B2可消费progress”属于此前建议，不再作为最终关口要求。

无新增架构阻断项。保留两个实现合同检查点：被动模式须重新检查已存在queued操作的dispatch资格；证据桥须把生成的证据以受控方式提供给模型引用，并能从源更正定位已done原事项。这两点均可在现有模块边界内落实，不要求新增框架或在本轮继续扩展设计。
