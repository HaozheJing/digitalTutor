# Tool 元数据目录

> 2026-10-05最新交付：隔离L已实现日内承接、按时间执行与可空完成条件；test15服务器532项程序通过，native06新状态10案/52 native/384 API全部Flash、程序10/10通过。独立工程、语义及未覆盖范围见[实施记录](第四阶段日内承接与完成条件实施记录.md)文末；4B/4C整体验收未签通过，本轮停在4C。下文H/J/K及较早L结论为历史证据，主目录与亲测数据未替换。

> 日内承接方向更新（2026-10-05）：[日内承接与按时执行设计](日内承接与按时执行设计.md)涉及待实现的接纳提交/读取、时间与安排修订、Service工具投影。当前接口不因此获得新权限，pending登记仍不等于正式接纳；具体schema和验证来源待同版实现时更新。

K隔离实现补充：`control.apply.event_id`省略或null表示家庭动作范围，非空ID表示单事项，空串/纯空白返回INVALID_SCHEMA且无写入。历史空白控制不迁移，读取时标记invalid_legacy及原值，对所列动作保守拦截，不能视为已确认的原家庭级授权。`pending.propose_makeup`成功仅登记可信来源的待规划请求，不创建正式事项、不安排未来通知、不授予主动联系资格。实际冻结验证状态见本页当前状态及阶段交付记录。

> 当前执行状态（2026-10-04）：隔离K实现及冻结验证结束，361项服务器程序测试通过，32个真实Flash案例完成独立评审；4B/4C关口未通过。反复出现未来通知承诺和教学方法来源错误，另有恢复计划缺下一步动作，按主方案第10条第4项暂停受影响分支并提交取舍。主目录H及亲测数据未替换，不进入4D。详见《第四阶段4C隔离K交付与待决记录.md》。





版本：0.3.0。核对日期：2026年10月4日。开发主控维护接口、状态和验证来源，业务负责人确认业务口径。本目录与 [Skill 元数据目录](Skill元数据目录.md) 一起作为能力维护入口。

当前注册 **22 个工具：11 个读取、11 个写入**。构成为第一阶段14个，加记忆3个、上下文1个、知识2个、Skill读取2个。微信仍为模拟渠道。第二阶段59项及两日模拟通过；第三阶段冻结版93项回归及6个纯虚构真实API场景通过。2026年10月4日用户补充授权后，指定课程/Skill进入模型的service、daily、progress三条联测通过。

## 元数据和身份约定

| 字段 | 维护含义 |
| --- | --- |
| `tool_id` | 规范注册名，Skill依赖、调用和审计统一引用；重命名记录替代关系 |
| `purpose` / `roles` | 服务用途和授权角色；角色由可信宿主绑定 |
| `input_contract` / `output_contract` | 参数模型和返回值；完整JSON Schema从实际注册表生成 |
| `effect` / `scope` | 读取或写入、家庭/uid/事项/报告范围及渠道动作 |
| `retry_policy` | 幂等身份、重试条件和未知结果核实方式 |
| `implementation` | 处理模块；未实现能力不得虚构工具名 |
| `status` / `revision` | 代码、适配器、运行绑定及对应修订的验证证据 |

权威来源：[注册表](../backend/src/digital_tutor/tools.py)、[核心参数模型](../backend/src/digital_tutor/domain.py)，以及新增模块各自的参数模型和处理函数。本目录采用0.3.0交付口径，程序版本以 [pyproject.toml](../backend/pyproject.toml) 为准；发布时核对二者及服务器源码，不能只改文档就认为接口生效。

**规范id保留点号。** [Hermes适配](../backend/src/digital_tutor/hermes_tools.py) 使用供应商兼容别名：`dt_` 加规范id中点号替换为下划线，例如 `wechat.send_message` → `dt_wechat_send_message`。执行和审计仍用规范id，别名不增加权限。原生工具集为 `digital_tutor`，每次只注册当前能力会话实际可用项。

表中 **R** 为 service、planner_daily、planner_progress、admin。admin不是全部业务工具的通行权限；写入角色按条目限定。模型不能通过参数选择家庭或角色。

成功返回 `{"ok":true,"result":...}`；业务拒绝返回 `{"ok":false,"error":{"code":...,"message":...}}`，字段错误带details。未知工具、越权和参数错误分别为TOOL_NOT_FOUND、ROLE_FORBIDDEN、INVALID_SCHEMA；不保证所有系统异常均已包装。

业务写入result包含request_id、outcome（applied/duplicate）、data、record_versions.family_input_version。幂等回放返回原请求结果；最新发送状态另查wechat.get_operation。

## 读取工具

读取不修改业务事实；SQL附加表可按需初始化，但查询不写入学情行。范围来自宿主绑定。

| tool_id | purpose / roles | input_contract | output_contract | implementation |
| --- | --- | --- | --- | --- |
| `state.read` | 当前状态；R | 无参数 | 家庭版本、Event、Control、计划、报告、待规划输入、消息、操作、时间 | [Application.state](../backend/src/digital_tutor/application.py) |
| `learning.schema` | 授权SQL表字段；R | 无参数 | tables、bound_family_uid范围、只读标识 | [PUBLIC_SCHEMA](../backend/src/digital_tutor/learning.py) |
| `learning.query_sql` | 家庭学情SQL；R | Query：sql，可选parameters/max_rows | columns、rows、数量、truncated、来源与适用性提醒 | [query_learning](../backend/src/digital_tutor/learning.py) |
| `wechat.read_messages` | 全部/最新/增量；R | MessageRead：mode/limit/after_seq/direction | 消息及序号、游标、has_more/has_older、水位；不标已答 | [read_messages](../backend/src/digital_tutor/wechat.py) |
| `wechat.get_operation` | 核实原模拟发送；R | OperationRead：operation_id | 操作版本、阶段、收件绑定、回执历史和发起次数 | [注册表查询分支](../backend/src/digital_tutor/tools.py) |
| `memory.read` | 有效家庭记忆；R | MemoryRead：可选event_id/limit | 当前有效修订、数量/has_more；排除过期和删除记录 | [read_memory](../backend/src/digital_tutor/family_memory.py) |
| `context.read` | 三配方上下文；service及两种Planner | ContextRead：service可选消息/事项；Planner不自选范围 | 状态、证据、覆盖标记、材料加载状态；progress绑定原报告/事项 | [read_context](../backend/src/digital_tutor/context.py) |
| `knowledge.list` | 绑定资料导航；R | 无参数 | 编号、摘要和来源；navigation_is_evidence=false | [KnowledgeCatalog](../backend/src/digital_tutor/knowledge.py) |
| `knowledge.read` | 完整资料正文；R | KnowledgeRead：document_id | 正文、固定指纹、来源与适用性；无模型路径参数 | [KnowledgeCatalog.read](../backend/src/digital_tutor/knowledge.py) |
| `skills.list` | 当前角色批准Skill；R | 无参数 | 规范id、别名、必读规范、指纹；不启用自动触发 | [SkillCatalog](../backend/src/digital_tutor/skill_loader.py) |
| `skills.read` | 原生读Skill和全部必读规范；R，另受Skill角色范围限制 | SkillRead：skill_id | 正文、规范正文、指纹及当前运行约束 | [SkillCatalog.read](../backend/src/digital_tutor/skill_loader.py) |

消息默认all；latest默认最新1条，limit可设N，按入库序号判断最新，仍保留发生时间；since按after_seq/next_cursor分页。all超过10000条明确报错。direction过滤后的游标继续用于同方向。不能传其他家庭id。

### SQL的8张投影表

| 表 | 数据与来源 |
| --- | --- |
| `learning_days` | 五类教学日状态、日期标签及来源单元格 |
| `assignments` | 作业状态、次数、作答、AI引导、轮数及质量标记 |
| `learning_batches` | 原始快照元数据；未知覆盖、时区和课程绑定为null |
| `learning_source_rows` | 8个sheet的当前uid原始行、表头、日期/题目元数据、分组及坐标 |
| `learning_summaries` | 分组、5类汇总原值、分子/分母及质量标记 |
| `assignment_details` | AI表达题类型/名称、分组、来源；按batch/uid/source_row关联作业 |
| `homework_days` | 按天作业配置、W标签、题目、原始/标准化状态及坐标 |
| `course_bindings` | 宿主确认的课程、版本、课节映射、覆盖与时区，和原文件字段分开 |

先按可信uid物理投影再执行SQL，不依赖模型自行写WHERE。禁止写入、ATTACH、PRAGMA、数据库元数据读取、扩展加载、多语句；有函数白名单、执行预算、行数及大小限制。旧数据库同文件重导仅补来源，保留原记录和导入时间。多批次显式选择batch_id；未知不等于零或当前未履约。

## 写入工具

除模拟入站外均要求request_id，完整字段以Schema为准。业务状态与请求回执在短事务中保存；发送分准备、发起、回执阶段，不在数据库事务中等待渠道I/O。

| tool_id | purpose / roles | input_contract摘要 | effect / output_contract | implementation |
| --- | --- | --- | --- | --- |
| `wechat.receive_message` | 模拟入站；channel/admin | Inbound：external_id/body/带时区occurred_at | 消息及待处理任务；返回消息 | [ingest_message](../backend/src/digital_tutor/application.py) |
| `message.interpret` | 多意图解释；service | Interpret：消息id/版本/intents | 保存逐意图待答信息，不代表已回答 | [interpret](../backend/src/digital_tutor/application.py) |
| `control.apply` | 即时暂停或解除；service | ControlCommand：动作范围/原因/来源消息及版本，可选事项/待解除Control | 保存Control，不等待Planner、不自动关闭Event | [apply_control](../backend/src/digital_tutor/application.py) |
| `planning.record_pending` | 独立新需求；service | PendingInput：来源消息/请求/剩余工作，可选原期限/已做部分 | 保存待规划输入，不创建正式Event | [record_pending](../backend/src/digital_tutor/application.py) |
| `progress.submit` | 原事项新事实报告；service | Report：报告id/revision、事项集、事实/证据、可选替代版本 | 持久报告和待处理任务，派生update_pending | [submit_report](../backend/src/digital_tutor/application.py) |
| `planner.commit_daily` | 当日唯一计划；planner_daily | DailyCommit：日期/快照、新事项/更新/引用/输入处置 | 原子保存Event和DailyPlan，返回映射及计划 | [PlanningMixin](../backend/src/digital_tutor/planning.py) |
| `planner.commit_progress` | 消费绑定报告；planner_progress | ProgressCommit：报告id/revision、快照、全部关联事项更新 | 原子更新原事项并消费报告，不新增目标或改完成规则/排序 | [PlanningMixin](../backend/src/digital_tutor/planning.py) |
| `wechat.send_message` | 模拟发送；service | Send：家庭版本/正文/动作、reply_to或event_id；交付带part_id，受控步骤可带step_id | 创建/复用Operation；sent/delivered后才存出站消息和回复覆盖 | [send_message](../backend/src/digital_tutor/wechat.py) |
| `wechat.mock_receipt` | 原操作模拟回执；channel/admin | Receipt：operation_id/stage | 单调合并回执，确认后幂等存出站消息，不再发送 | [mock_receipt](../backend/src/digital_tutor/wechat.py) |
| `memory.save` | 保存或更正记忆；service | MemoryWrite：家庭版本、来源消息/摘录、正文/类型，更正带记忆id/版本 | 返回记忆id/版本/状态；更正需新消息且发生时间不早于原依据 | [save_memory](../backend/src/digital_tutor/family_memory.py) |
| `memory.delete` | 删除记忆正文；service | MemoryDelete：家庭版本、记忆id/版本、来源消息 | 删除当前/记忆历史正文，明确保留原聊天、证据与备份 | [delete_memory](../backend/src/digital_tutor/family_memory.py) |

## 重试、调度和恢复

| 范围 | retry_policy / 边界 |
| --- | --- |
| 通用写入 | 同家庭同request_id同载荷回放；不同载荷IDEMPOTENCY_CONFLICT。失败整体回滚，换请求号不能绕过权限和版本。 |
| 入站 | 幂等身份为inbound:加external_id；同id不同内容/时间冲突。没有消息编辑协议。 |
| 规划 | 旧快照STALE_INPUT/STALE_EVENT，重读后由宿主开新运行。progress精确覆盖绑定事项集；daily先处理未消费报告。 |
| 家庭队列 | [JobQueue](../backend/src/digital_tutor/jobs.py)事务领取、家庭串行，宿主心跳续租；过期或generation变化后旧运行不能写入、续租或收尾。恢复开关不恢复旧权限。 |
| 触发 | [Scheduler/Worker](../backend/src/digital_tutor/scheduler.py)只建当前业务日daily，普通来信优先，过期主动任务不补发，review会话禁止外发。任务成功须有实际业务结果。 |
| 准备 | 原义务及受控步骤去重；准备事务预占模拟频控，queued/unknown也计入，额度不是正式业务规则。 |
| 发起前 | 默认08:00≤时间<21:00，再查暂停、停用、租约、事项、今日计划、输入版本与新来信；变化会取消未发起操作。 |
| 取消重准备 | 仅cancelled且dispatch_count=0可新请求重准备，旧审计保留，全部条件重查；unknown/dispatching不得变成新操作重发。 |
| 未知恢复 | [Outbox](../backend/src/digital_tutor/outbox.py)对dispatching/unknown/accepted查原渠道操作；未知不重发，停用/截止仍可只查回执。 |
| 回执乱序 | [merge_stage](../backend/src/digital_tutor/receipt_state.py)保留更强sent/delivered事实，迟到返回不降级，失败冲突需核实；发送不是已读、回复或解决。 |
| 材料 | 范围、版本或指纹不符拒绝；缺课不借相邻课补位。Skill必读规范完整加载，正文不授予新权限。 |
| 记忆 | 来源摘录可核对，过期不进入有效上下文；Control和事项仍以业务状态为准；迟到旧消息不覆盖较新记忆。 |

## 宿主入口与原生能力

create_family/open_session/add_evidence、Excel导入、[confirm_course_binding](../backend/src/digital_tutor/course_binding.py)、领取/续租、停用、渠道恢复与CLI配置均为可信宿主入口，不属于22个模型工具。课程确认要求admin能力、家庭批次关联和最新版本；省略字段保留确认，显式null清空。课程/版本改变后旧课节映射失效，不能无声沿用。

Hermes继续使用原生AIAgent循环、工具注册、只读skill_view、会话/取消和cron唤醒适配。每运行独立profile、目录和会话；不暴露通用终端、浏览器或其他家庭能力，最终文本走内部通道。知识使用既有OpenViking客户端及固定清单，不改vendor主循环。

## 状态与证据

| 能力层 | 当前状态 | 验证口径 |
| --- | --- | --- |
| 第一阶段核心/SQL/模拟消息 | 已有服务器验证 | [阶段一交付](教辅后端第一阶段交付记录.md)的33项对应其原修订，不能代替后续验收 |
| 阶段二队列/租约/停用/恢复/频控/两日模拟 | 已有服务器验证 | [59项测试](../evaluations/backend-stage2/20261003T141601255327Z/tests.json)、[两日模拟](../evaluations/backend-stage2/20261003T141601255327Z/scenario.json)、[来源指纹](../evaluations/backend-stage2/20261003T141601255327Z/manifest.json) |
| 阶段三22工具/SQL8表/记忆上下文/原生适配 | 冻结版93项程序回归通过，0跳过 | [验收汇总与源码指纹](../evaluations/backend-stage3/stage3-final-20261003/acceptance.json) |
| 原生Skill/OpenViking读取 | 服务器本地探针已通过 | 不能等同内部正文已完成外部API测试；探针记录由主控纳入交付 |
| 真实模型API | 基础六场景及授权后的材料三条链路通过，每场景记录源码指纹 | 基础验收排除材料；10月4日已授权第1课、explain-lesson及plan-proactive-service和必读规范，见[补充验收](../evaluations/backend-stage3/stage3-materials-20261004/acceptance.json)。家庭学情仍虚构，真实微信未启用 |
| 真实学情适用性、正式频控、真实微信 | 业务确认或接入未完成 | 只读导入不等于课程/渠道绑定；频控仍simulation；没有生产微信发送 |

服务器目录：/root/autodl-tmp/digitalTutor。第三阶段最终程序/API结果、探针和manifest由主控补入交付记录。媒体制作、正式人工转交等未注册能力，不能因Skill提及就声称可用。

## 维护流程

修改工具时同步规范id、参数模型、处理模块、本目录及Skill依赖；废弃项记录替代和停用状态。发布核对SPECS、ToolRegistry.describe的Schema、原生别名、远端源码及对应测试。样例维护于 [backend README](../backend/README.md)，业务设计见 [接口合同](教辅后端数据与接口合同.md)。

变更记录：0.1登记阶段一14工具；0.3.0增加8工具、SQL8表、阶段二恢复语义、原生别名及分层验收状态。

## 第四阶段4A增量（实施中，尚未签署验收）

新增 `service.record_outcome`，当前源码23个工具；service角色写内部run_outcome，严格绑定Worker run/job/lease并校验真实记录版本，不发送、不结案、不增加家庭业务版本。字段与五种结果见[运行合同](第四阶段运行合同.md)。知识及Skill的list/read在新Host仅开放固定批准清单，旧阶段记录不挪用为本次通过。


### 第四阶段4A关口记录（2026-10-04）

4A冻结D版：service.record_outcome为受控内部结束接口，返回真实记录引用；final文本不发送。参数化SQL、消息三模式及被动端到端链路继续在4B1验收，不能从装配通过推导业务通过。


### 4B1部分回复合同（2026-10-04，D批复验中）

wechat.send_message.reply_to每项新增resolves_intent:boolean（默认true，历史完整回复载荷摘要兼容）。false表示本次仅澄清/说明缺口，确认回执写partial_reply_operation_refs，不写完成答复的reply_operation_refs，原requires_reply责任保留。最终答案与澄清有独立去重身份，但任何未知原操作仍阻止同一意图再发。

service.record_outcome在存在实质待答时拒绝action_taken，必须waiting/needs_input及欠项/恢复条件；真实未知操作自动记awaiting_operation_ids用于同job无外发恢复。context.read增加service_continuations供后续看到原欠项。严格knowledge.list只返定位与来源属性，不返导航正文摘要；完整证据仍必须knowledge.read成功。以上均不增加角色、队列或Planner入口。


### 4B2消息读取与内部草稿接线（待关口验收）

wechat.read_messages在strict真实service Worker下记录all/inbound各自连续完整前缀到runtime_cursor。all完整读取或since从已知前缀连续分页才推进；latest、跳跃since、空页不伪造已读前缀。重复/无变化读取不增加cursor版本或家庭业务版本，不改变意图已答状态。context.read向service展示message_read_cursors；teacher_draft的text_reply=false，与既有禁止发送一致。没有新增业务队列或模型工具。


### 第四阶段4B2来源定位与读取结构（已接线，B语义验收尚未通过）

context.learning.source_index只读暴露当前家庭UID在learning_days/assignments拥有的最多12个批次定位、可解析覆盖时间、字段存在性；按真实batch_id稳定排序，total_count/has_more显示截断。该范围不是全部SQL来源全集，空索引不证明所有表无记录。metadata_structure只对现有可查询元数据的JSON对象给出有界顶层键名：输入最多16KiB、最多12键、每键最多64字符且结构标识符白名单；不递归、不复制值、不截短非法正文键，数组或非法结构仍未知。源字段未经业务核验，非空不等于已确认绑定/履约；实际内容仍由learning.query_sql读取。

learning.schema说明learning_batches元数据与course_bindings已确认字段的区别；SQL缺失元数据返回null、batch_id取imports真实主键。context材料loader不能覆盖宿主来源索引。索引不写业务表、不增加family version、不触发任务；19项针对程序测试及当前冻结回归通过。具体Schema和局限以源码、manifest及阶段记录为准，不能将索引定位视为正文已读。


C/D工具说明对齐：planning.record_pending仅登记未安排需求，original_deadline保留用户期望而非接单承诺；Send.body说明事实、假设、期限精度及局部活动不改变原完成标准。仅description，字段类型、默认值和权限/状态校验未变；真实语义仍单独验收。


### 4B最终冻结交付与暂停

4B冻结版提供消息all/latest/since和连续覆盖游标、受限SQL来源结构索引、待规划登记、旧事项报告与内部草稿/结果引用；程序工具验收与本批真实模型实际调用范围分别以最终工程报告为准，不将支持能力等同逐模式实链通过，也不强迫安全SQL采用参数化形式。Send.body和PendingInput.original_deadline只加强描述，不改原字段类型/默认值；pending成功仅代表登记。被动权限、窗口、暂停、幂等及回执均由程序检查，回复语义由独立评审；完整progress消费尚未开放。 详见第四阶段交付记录与gate-decision。

2026-10-04 Flash G：仅flash/多多老师；SQL绑定、材料来源及失败回执合同已修复，批准材料正文未改。240回归和4B1工程通过，回答内容与用户共同评审，Flash 4B2未复验。完整变更、证据及限制见第四阶段交付记录和G fix-manifest。

2026-10-04 Flash H：4B2已完成冻结复验和双评审；逐意图未答合同补入发送参数，真实政策欠项误清已修复。244项回归和更正解析后的16案程序检查通过；内部暂停说明与教学完成条件仍有语义问题，关口未通过。Skill正文/批准范围不变，合成/本地材料不等同正式Skill语义通过。完整证据、各项限制及对话见《第四阶段4B2重点核对记录.md》，停在4B2。

### Flash H发送参数的逐意图责任说明

`wechat.send_message.reply_to[].resolves_intent`默认仍为true；新增模型可见说明要求逐项区分完整实质回答与缺口说明。缺适用正文、政策、必读规范或让家长另行核实时，应为false；资料不足不属于权限拒绝。实际确认发送仅写该项partial_reply_operation_refs，不能借另一项已答清除本项。H真实双缺资料运行的两个intent均为partial且任务waiting；4项新回归禁止只剩一个欠项就整体通过。内存、控制、进展报告工具及授权不变。


## 4C独立开发与4B遗留联合计划（2026-10-04）

4C计划接口项（尚未实现）：候选/来源/决定及run版本绑定、daily来源合同校验、daily模式准备与发送门禁；明确新需求的受控提议复用隔离提案后再定Schema。当前运行Tool行为没有因本文改变。实施后同步更新实际Schema、权限、持久副作用、错误和回归证据。

详见[联合实施计划](第四阶段4C独立开发与4B遗留修复计划.md)。


## 4C联合实施进展（隔离J，2026-10-04）

隔离J已实现 pending.propose_makeup（仅service，完整原消息、唯一已确认课程映射，歧义保留）；strict planner.commit_daily新增候选逐项决定与新Event候选绑定，原合同/原始源指纹/同目标/历史H回执校验。wechat.send_message在prepare及首次dispatch要求本run实际消息读取。daily模式只允许授权待答回复。来源恢复为宿主只读观察，不是模型工具，最多恢复原job三次。

详见[实施进展](第四阶段4C实施进展.md)。旧批证据与未通过项保持原结论。


### 隔离K实现与验收限制（2026-10-04）

候选/消息阅读门禁/材料恢复/控制范围/有限daily及安全导出已实现于隔离K，主目录仍为H。361项程序通过不等于语义验收；K实际发送仍有未安排回告承诺和方法来源错误，且恢复计划下一步、报告remaining_work存在欠项。正式Skill与批准材料字节不扩充、不把模拟规则当正式政策；本轮不新增Planner入口。详见[当前交付与待决项](第四阶段4C隔离K交付与待决记录.md)，4B/4C gate=false。


## L日内承接与完成条件接口（2026-10-05）

新增`pending.propose_service`（Service）和`planner.commit_admission`（宿主绑定admission的planner_daily）。前者保存真实未答来源、时间及可空完成说明，不授予外发；后者只处理绑定pending/候选与关联原事项，原子提交版本化安排和有界承接资格。baseline会话不开放admission提交，progress不新增事项。

`message.interpret`的意图增加`service_kind`（默认reply，可选future_delivery）与`service_authorization`（未来文字交付的真实原文片段）；原意图不可覆盖。`pending.propose_service`必须引用已明确为future_delivery的同一未答意图；普通缺资料问题沿原Service责任恢复，不能以工具request参数自行改成新服务。旧持久pending/Event不迁移或删除，旧默认意图摘要及幂等回执保持兼容。仅类型/原文校验不能替代意图语义审查。

首次解释的future_delivery必须requires_reply=true，否则整次解释原子拒绝`FUTURE_DELIVERY_REPLY_REQUIRED`，无消息/回执副作用，可在同轮以原版本修正。strict发送的准备和真实派发还检查同家庭同源意图的planning_requested且await_decision：登记回复不能resolves_intent=true，拒绝码`PLANNING_DECISION_PENDING`。普通资料pending不受此守卫；正式goal决定后可答复安排并结清该消息，业务goal仍待可信完成；正式delivery继续交由原步骤和实际交付证据结算。

责任投影分别记录实际安排确认、真实交付开始与真实完成。原Event/要求修订/接收者/必要part及有效派发资格一致时，dispatching/accepted/unknown/sent/delivered表示交付已开始，由原步骤持有；不写成已通知或已完成。queued/prepared/failed/cancelled不算开始。未知确认与实际交付双账保留，原确认终态变化后由原Service任务只做内部补账；不重新发送或补同义报告。

正式接纳的delivery安排确认使用宿主派生的`decision_reply_refs`进入发送身份：绑定同一来源意图、pending决定request/revision、Event与承接资格，解决同一意图登记和确认的partial键冲突。模型不能提交该字段，也不能换正文/request_id制造新资格。原同pair未知操作仍预占；实际派发再核决定身份，旧queued登记不能升级成确认。新确认须有真实phase操作及同run宿主确认记录；既有宿主确认历史兼容保留，不凭旧登记文本补判确认。goal和未正式接纳分支未扩大此身份规则。

文字交付确认后，普通reply范围排除已移交的待履行意图；最终发送再核，旧queued不能绕过。原Event实际完成后才由宿主写入`delegated_completion`及真实操作引用。strict step在报告待消费或原业务未完成时保持明确等待，仅相关新事实有界恢复；这些是现有队列/工具接点，不新增模型工具或队列。

`CandidateSource`、`EventCreate`、`Step`的`completion_condition`为可选文字；空白归一null，空时Service自主判断。`Step.execute_at`是业务排期，不是任务退避；`Report.completion_assessments`记录实际条件、结论、原因、真实证据。`Report.remaining_work`省略/null表示未知，不默认无剩余。已有条件与硬完成合同不能被空默认值清掉。

`state.read`/`context.read`对strict Service提供当前执行投影与最小责任索引，需时按原ID补读；内部接纳决定与可外发正文分开。学情适配及源更正报告修订为宿主入口，不作为模型trusted写入工具。具体实现、未覆盖和证据见L实施记录，未签全关口通过。
