# 主实验：真实消息增量接入

## 智能体每日计划与执行（2026-10-20）

本节替代下方观察版“测试调度器直接发送”的行为。首月测试启用 `agent_planning` 后，
宿主只提供 Skill 规定的四个检查入口及冻结时钟任务队列，实际取舍、时间选择、
计划文案与执行结果由同一个 Harness 原生智能体决定。旧的确定性高光发送器不再自动运行。

智能体通过 `service_skill` 读取正文与哈希，查询当前 `daily_context`、
`action_candidates`、`service_schedule_list`，按 `plan-today` 保存业务理由。
需要额外安排时先 `service_schedule` 取得真实测试时钟回执，再 `save_plan`。
无适用候选或固定检查足以覆盖时可保存空计划；不能编造未来学习事实或排期 ID。
模型创建的安排带到点复查条件；有效安排必须保留，或先有据取消。

执行由宿主通过 `sessionController.prompt` 唤起原生智能体，记录真实 requestId。
到点重新查学情、读相应动作 Skill、检查联系限制后才交付。内部任务须以
`service_task_finish` 记录完成、不联系、受阻或延期；没有交付回执不能宣称发送完成，
运行中断或漏交完成回执显示受阻，未知交付不自动重试。测试时钟任务 ID 与原生
墙钟 Schedule ID 明确区分；本次仅增加冻结时钟适配，不改线上日期的原生排期行为。

工作台“保留计划，推进到所选时间”只允许同日向前移动，保留 run_id、会话、
计划、历史交付与去重记录，触发重新评估和到点执行。换日或回退用“开启新一轮”；
旧轮次仍可只读回看。可显式重新制定计划或请求一次当前学情与高光检查。
自动执行关闭后固定执行与未来安排暂停，显式检查及内部规划仍可运行。
工具日志按服务 task_id 关联，计划区展示模型理由、复查条件、实际结果和微信回执。

首月现有高光仍使用接口中的已有图片；另根据当前可见逐课事实开放周六补读建议、
周日学习回顾两个既有 Skill 候选。S01 只核对今天，不用历史欠课替代今日未完成。
当前 mock 没有逐课可信全文绑定，未完成课的催课交付会记录材料不足；不借用旧
第1课资料冒充其他课正文。该限制不会阻止已有图卡或基于课程表的补读建议。

主要代码为 `agent_plan.py`、`tutor-plugin.mjs`、`mcp_server.py` 和工作台文件。
SQLite 新增 service_tasks / service_plan_versions / service_skill_reads /
service_links；每轮隔离，保留计划版本和交付关联。部署备份：
`backups/before-agent-plan-20261020-205941`。

本地及服务器63项Python测试、9项Node接口测试通过。真实原生模型验收：
10月14日18:59自主保存空非周期计划；同轮推进到19:00后复查已完课，未发无依据消息。
10月25日16:00根据第8/9/10课逾期决定由19:00检查提供补读建议；保留计划推进到
19:00，重新查询并实际交付一条文本，平台最终回执为 `sent`。周六S01复核为
`no_contact`，不拿历史欠课冒充今日未完课。当前测试停在10月25日19:00。

联调暴露过“模型只读自动注入上下文、未在本任务显式查询”的完成回执错误；
现已保持查询校验，并为自有业务校验异常返回可操作的具体原因。普通供应商异常
仍隐藏原文，避免泄露凭据。无法补齐前提时可明确记录 blocked，禁止同错反复重试。
旧失败记录保留，修复后使用独立复核任务验证，不改写原失败状态。
修复备份 `backups/agent-plan-guard-20261020-212455`。
验收记录 `artifacts/agent-plan-acceptance.json` 包含真实请求ID、Skill哈希、调用、
计划保留、历史回看、HTTPS静态文件与发送结果。未完成浏览器截图验收。

## 首月测试工作台

入口 `https://82.156.162.52/workbench/`，专属访问链接通过 `#key=` 携带工作台密钥。
页面读入后立即移除地址栏片段，在当前标签页的 sessionStorage 中保存认证值。
密钥放在服务器私有 `config/workbench.json`，不进入 Git；数据和操作接口均验证 Bearer。
公网代理只开放 `/workbench/`，Harness 的其他管理接口仍留在 loopback。

页面提供35天课程日历、13个推荐场景及问题、测试时间切换、同一时点重测、
当前学情、可用高光与交付回执。选择日期仅预览，点“切换并开始测试”才生效；
点击“发送当前高光到绑定微信”复用微信命令的发送与去重实现。
日期切换沿用归档和新会话逻辑，不改变真实时间。浏览器每5秒刷新当前状态。
过期页面的 run_id、智能体忙碌状态、日期范围会在服务端拦截；无自动重试外发。
场景预期只出现在操作工作台，不进入模型上下文。

观察版新增微信业务对话（含图片分项回执）、实际业务工具的输入输出和耗时、
每日服务安排、历史轮次只读回看。`observability.py` 在 MCP 业务工具边界记录调用；
模型、测试调度器、手动操作分别标记来源，不记录模型内部思考，也不补造旧日志。
日志按 run_id 隔离，日期切换前的聊天、计划、工具记录从归档库读取。
记录中的密钥字段和带 token 的地址会脱敏，过长返回值有截断标记。

切换日期时默认勾选自动高光，可在本轮独立开关。`test_scheduler.py` 每次轮询复查
当前冻结时间已经到点的高光，复用发送事务、联系暂停、频控与去重；不自动重试
发送结果未知的消息。它不随真实时间推进课程。旧轮次升级时保持原自动开关，
开启本轮自动执行后才会自动发送。微信日期命令沿用上一轮开关。
每日安排标注来源为测试调度器，显示执行状态、条件依据与回执；课程开始和截止
为参考节点，当前不自动发送催课消息。模型自行保存的计划另列，当前测试模式不
创建真实时间排期。计划预览不会把未来结果加入模型上下文。

观察版验收（2026-10-20）：本地和服务器51项Python测试、8项Node接口测试通过。
原生模型实际调用 `learning_query` 和 `highlight_lookup`，工作台记录了两项输入输出；
开启当前10月13日20:30轮次自动执行后，首日高光文字和原图均收到平台 `sent` 回执，
每日计划同步显示已发送。首日此前没有自动发送，是旧版只切换时钟、未启用执行，
不是图片发送失败。另修复了真实聊天静默期与冻结业务日期跨年份比较导致误判过期。
验收记录 `artifacts/workbench-observe-acceptance.json`，部署备份
`backups/before-workbench-observe-20261020-203051`。本轮自动执行现已开启。
浏览器自动化仍连接超时；接口和真实收发已核验，页面截图验收未完成。

实现：`workbench.py`、`workbench-route.mjs` 和 `workbench.html/css`、`workbench-ui.js`。
部署时 `.mjs` 放入 `app/`（与插件同目录），其他文件放入 `adapters/`。
接口测试：`node --test live/test_workbench_route.mjs`。

初版（2026-10-20）本地与服务器均通过46项Python测试和6项Node接口测试。
HTTPS现场检查通过认证、越界拦截、旧轮次拦截，并实际切换到10月24日核对
10课已排/6课完成/4课待补及微信读取同一时钟，随后恢复10月13日20:30。
此轮现场检查未外发微信消息；发图复用既有通道，新增入口通过模拟供应商验证。
验收记录 `artifacts/workbench-acceptance.json`；部署备份
`backups/before-workbench-20261020-200845`。浏览器自动化连接超时，未完成截图验收。

## UID 00001 首月微信测试

`test_mode.py` 提供冻结业务时钟，`fixture_query.py` 按时点读取已确认的侨侨 L2 数据。
真实收发和重试使用系统时间。测试期为 2025-10-13 至 2025-11-16。
启用后 `wechat_test.py` 直接读取验签成功且匹配唯一绑定联系人的 callback；
原自动回复服务停止，callback 接收服务保留。普通问题进入 Harness 原生会话，
回复经过独立持久化 outbox 发给绑定微信，超时不盲目重发。

微信命令：

- `测试日期 2025-10-24 20:30`：切换业务时间；省略时间默认 20:30。
- `测试状态`：查看 UID、时间、学情汇总与当前可用高光。
- `测试高光`：显式执行当前可触发高光。微信收到说明文字和原生图片消息，直接复用已有高光图，不生图、不只发链接。
- `测试重置`：以相同时间开启新一轮，可重复测试同一高光。

每次日期切换或重置先备份业务库到 `runtime/test-archives`，再清空本轮会话、
学情测试交付和频控记录，并创建新的原生会话。传输账本和 callback 游标跨轮保留，
防止真实消息被重发。测试日期不会随墙上时钟自动推进；原生周期任务在测试模式停用。
到点高光改由测试调度器执行；自动开关关闭时，仍可用“测试高光”显式触发。
管理端可通过本机 `POST /test-clock {"as_of":"2025-10-24T20:30:00+08:00"}` 切换，
模型运行时返回 409，避免时钟在同一轮回答中改变。

模型工具 `learning_query(day?)` 查询学情与课程表；`highlight_lookup(highlight_id?)`
返回当前可用素材；不把未来补课结果放进过去的上下文。素材数据放在被 Git 忽略的
`runtime/fixtures/mock_uid_00001_first_month.json`，绑定与通道配置放在私有
`config/test-wechat.json`。高光不调用生图工具。

高光通过供应商 `messageType=6 / payload.url` 发送原图，文字仍用 `messageType=7`。
一次高光可以包含多张图，文字和每张图分别持久化幂等键及回执。
只有全部分项确认才记为整组 `sent`；部分成功记 `partial`，未知结果不自动重发。
发送依据：[供应商发送消息文档](https://s.apifox.cn/d292e311-af7c-4a68-bfe4-416fd1d657b6/315430966e0.md)。

以下章节描述启用首月测试之前的模拟发送基线；测试模式只覆盖上述单联系人。

## 主动动作扩展

2026-10-20 已接入19类新增主动动作，连同原S01共20类。动作清单见[PROACTIVE_ACTIONS.md](PROACTIVE_ACTIONS.md)，SQL信号合同见[PROACTIVE_DATA.md](PROACTIVE_DATA.md)。

当前共有26个Skill；共享proactive-service负责候选选择、复查、合并和交付，19个业务Skill负责服务目的与表达依据。原生周期增加09:30日历关怀检查、19:00学习与周月节点检查，保留07:30规划和18:00 S01。新增候选也进入每日规划；对话优先和持久化延后机制继续适用。

条件代码和交付前事务复核位于proactive.py。主动联系默认每天合计最多2次、间隔60分钟（实验策略，含S01）；即时回复不受此额度限制。所有外发仍为SQL模拟记录，触发输入也经SQL读取，未改Hermes或官方npm框架。新增动作当前完成的是文字服务，图卡/语音/勋章/视频不因文字已发而视为完成。

本地与服务器34项测试通过，包含19类正向触发、19类证据撤回后的停止发送子场景，以及并发去重、未知/冲突数据、联系暂停、过期与交流后恢复。新增模型现场验收为原生定时生日关怀，其他新增动作暂以确定性条件与发送守卫测试为证据，不能据此宣称19类模型表达都已验收。

## 业务服务版本更新

当前使用 `business.py` 投影业务上下文，模型工具为 `reply_to_message` / `deliver_message`；底层发送仍固定原SQL隔离账本。原始来源不改，模型返回不携带物理环境标签。persona 与六个原生 Skill 分别承担身份、主流程、规划、S01、记忆、表达与内部复查（主流程及五个子Skill）。新会话记录在 `runtime/service-session.json`，旧会话保留。

被动事件交付回复后即可 `finish_event`，不再要求保存新计划。主动暂停工具 `pause_contact` 要求当天、精确且明确停止联系的原话；发送事务核验暂停状态。偏好等长期记忆字段尚未扩展，仍仅支持自报称呼。

`action_catalog` 保留内部配置动作；`proactive_facts`支持新增业务条件。`action_candidates`统一评估并生成幂等候选。`save_plan`核验候选对应的原生排期ID及窗口。没有有效动作仍允许空计划。

本次备份位于 `backups/pre-service-quality-v1`。下文是增量接入基线记录；其中模型可见模拟字段、旧工具名以及“每条消息必须重新规划”的说明已被本节替代。当前尚未完成30场景盲评，不据此宣称已达到85分。

部署位置 `/home/ubuntu/digitalTutor-dsh-lab`；服务 `digitaltutor-dsh-lab.service`。
此目录是当前主实验插件与适配器源码，`v2/`仅保留前一轮回放。没有修改官方 npm 包。

## 执行链路

原 Hermes 的 `context.json` → 当前 `replyer.sqlite` 只读游标 → 本实验 `live.sqlite` → 持久化事件 → Harness 原生 session prompt → 主 Skill → SQL 工具与子 Skill。

每5秒检查新行；对仍未sent的行继续读取，补齐稍后产生的历史回复。`context_id + message_id + role`去重；切换来源context时独立推进游标，只允许配置中明确绑定的一个recipient。其他联系人不会导入。首次导入已有原话仅整理记忆与计划，不补发历史回复。启动之后的新原话通过原生steer进入当前会话，先模拟回复，再整理记忆、刷新计划。

真实消息源是当前 Replyer 写入的记录，已包含入站 queued 消息；轮询存在最多约5秒延迟。原始回调中尚未进入 Replyer 的消息、附件、其他联系人暂未接入。源库按rowid追加，已sent行视作不可变；如业务日后允许修改历史已sent文本，需要增加修订号接口。

## 持久化与恢复

- `runtime/live.sqlite`：原话、游标、事件、记忆、档案、计划、上下文快照、模拟交付。
- `runtime/service-session.json`：当前主实验原生会话；`live-session.json`及旧experiment.sqlite保留。
- Harness原生存储：07:30每日规划、18:00 S01与临时延后任务。插件按标题核对安装，不另做cron。
- 事件：pending → dispatched → done。被动事件由finish_event核验回复交付即可结束；内部历史整理仍需记忆审查及随后保存计划。未完成事件在原生agent空闲或主服务重启后重试；最多3次，随后needs_attention。处理语义是至少一次，SQL模拟交付按事件键去重，不能称为模型执行严格一次。
- 家长消息原生steer优先；主动排期在pre-step门禁遇到交流时重新排期。发送事务再检查门禁；交流结束后120秒安静窗口，再复查今日完课。
- OpenViking仅沿用read工具，不向其写记忆。记忆持久化在实验SQL；当前仅支持有精确原话证据的自报称呼，不是通用画像抽取。

## 档案接口预留

MCP `family_profile()`提供 `family_id`、`child_nickname`、`guardian_role`、逐字段`source/as_of/verified`。
guardian_role为father（爸爸）、mother（妈妈）、unknown。接口读取SQL `profiles`；未来业务API适配器将可信结果归一后写入该表，智能体没有写档案工具。

预留上游记录合同：`id`为档案来源版本标识；`child_nickname`可空；`guardian_role`为上述枚举；`source`为business_api或sql_mock；`as_of`为UTC ISO时间。当前没有配置真实档案API地址，不能声称已经读到真实昵称和监护人角色。现有`family.get_profile`演示返回过child_nickname，但演示记录不当作当前家庭真实档案。

真实business_api字段优先于sql_mock；当前昵称沿用SQL模拟“小禾”，角色unknown。聊天“我是豆豆”只进入speaker.self_reported_name，不会覆盖档案。身份未知或只有mock时使用中性称呼。将来接入多家庭需要每家庭数据库或租户过滤与原生session映射，不能直接扩大recipient白名单。

## 数据与API边界

沿用既有base_mcp及vendor适配器：图片生成、TTS、图片理解、音频转写、API清单、OpenViking只读。凭据仍在实验config私有目录，报告不导出。嵌入模型由既有OpenViking服务管理。

当前课程全文来自既有OpenViking只读快照（源材料标注AI生成、未经业务审核）；10月20日绑定和未完成标志来自SQL mock。其他日期若无业务行返回unknown，不默认未完成。今天无非周期候选，不凭空安排活动。实验发送器只有SQL模拟账本，没有微信发送工具；原Hermes仍独立执行它原有的真实回复。

## 运维与验证

本地：`python -m unittest discover -s experiments/deepseek-harness-lab/live -p 'test_*.py' -v`。
服务健康：服务器本机 `GET http://127.0.0.1:19032/health`；原生排期 `POST /schedules {}`。只监听loopback。health含poll状态、事件积压和needs_attention；应同时检查三者，不能只看进程active。

迁移备份 `/home/ubuntu/digitalTutor-dsh-lab/backups/pre-live-20261020` 保存旧代码、Skills、profile、SQL快照和旧排期回执。回退应只停止此实验服务，恢复旧代码/profile，并用旧会话重新创建备份中的两条原生排期；不要移动或清理原Hermes目录。
