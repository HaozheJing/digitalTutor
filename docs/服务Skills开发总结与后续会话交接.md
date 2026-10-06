# 服务 Skills 开发总结与后续会话交接

记录日期：2026年10月3日。本文合并建设计划、两项打样、首批五项和全量清单，保留开发结果、已确认约定与接入入口。完整测试过程见[核对与模拟测试报告](服务Skills核对与模拟测试报告.md)。

持续维护的能力索引见[Skill元数据目录](Skill元数据目录.md)，可调用程序接口见[Tool元数据目录](Tool元数据目录.md)。本文保留建设与交接历史；当前名称、依赖和运行状态在元数据目录核对，具体规范仍以对应源文件为准。

2026年10月3日远端只读核对：`agent/` 的48个文件与本地一致；现有Hermes配置、启动环境及本地Skill目录检查未见本批教辅Skill的加载绑定，自动触发关闭，`agent/system.md` 尚不存在。见[本次核对记录](../deployments/docs-cleanup-20261003T021247Z.json)。

## 阶段结果与产物

建设经历两项打样、五项成套、全量扩展、原表核对和行为回归。现有 `agent/skills/` 为41个目录，包括34个面向家庭的规范入口、3个内部处理或业务扩展入口、4个名称别名。六份共用规范位于 `agent/guides/`，规则位于 `agent/config/service.yaml`。

首批范围是一个老师智能体服务一个学生及其家庭，先后端、后前端。触发形成评估机会；Skill提供业务方法，执行器承担状态、时间、权限和实际工具调用。

2026年10月3日上传新增51个文件、复用1份课程原稿，52份哈希一致；见[上传记录](服务Skills远端上传记录.md)和[逐文件回执](../deployments/service-skills-20261003T013936Z.json)。这次上传未修改Hermes运行配置或注册本批Skill。运行接入仍需实际加载与业务工具执行证据。

## 已确认的维护约定

- 保留人类动作原稿中的目标、条件、步骤、对象、渠道、频率、时限、物料和有用示例；成品Skill不附原动作表、动作编号或评审过程。
- 这批文件属于教辅业务能力；通用工具、内部规划收尾和开发运维能力按职责及可信配置控制可用范围，不要求拆成多个智能体或新建多套目录。
- 同一服务目标可由主动信号、家长咨询或原事项恢复进入，不为每种触发复制Skill。合并能力时须保留各自条件和完成依据。
- 共用规范维护一份，与Skills和规则一起发布。每次必读的指导由加载机制提供，不能依赖语义检索碰巧命中。
- 原动作表、课程和产品资料分别维护于 `knowledge/service/`、`knowledge/lesson/`、`knowledge/product/`；课程正文按原课节读取，来源快照不等于正式业务版本。
- 未确认的业务制度继续在配置中标记待确认；示例参数不自动成为生效规则。

## 规范入口

| 类型 | Skill 目录 |
| --- | --- |
| 督学与学习安排 | `remind-reading`、`celebrate-lesson-completion`、`prompt-backlog-makeup`、`celebrate-learning-streak`、`plan-makeup-learning`、`support-course-continuity`、`rebuild-reading-rhythm`、`close-week-reading-gap`、`plan-weekend-makeup`、`weekly-learning-review` |
| 互动 | `share-reading-reflection`、`quiz-course-knowledge`、`practice-reading-method`、`connect-reading-to-life`、`celebrate-important-day` |
| 高光与成长 | `highlight-course-start`、`highlight-first-perfect-answer`、`highlight-seven-day-completion`、`highlight-book-completion`、`highlight-second-week-attendance`、`highlight-reading-milestone`、`highlight-quiz-ranking`、`highlight-expressive-answer`、`highlight-ranking-progress`、`highlight-reading-birthday`、`review-learning-progress`、`prepare-growth-review-package`、`produce-growth-video` |
| 答疑与人工衔接 | `explain-lesson`、`answer-course-service-question`、`address-learning-concern`、`handle-complaint-escalation`、`respond-to-child-sharing-help`、`follow-up-unanswered-question` |

## 编排与兼容

- `plan-proactive-service`：只生成候选、等待、合并、派发或人工接管结果，不越权发送。`prepare-renewal-evidence` 是项目续费支持扩展入口，使用成长证据但不属于原动作表 H01-H13。
- `close-service-case`：统一写回事项、消息、操作回执、剩余责任和知识候选；Q07 对话回流作为公共收尾处理。
- `recommend-catch-up-reading` → `prompt-backlog-makeup`。
- `reinforce-reading-streak` → `celebrate-learning-streak`。
- `restore-course-sequence` → `support-course-continuity`。
- `review-weekly-learning` → `weekly-learning-review`。

上述别名只作名称导航，不代表已有生产触发器；不得与规范入口各执行一次，沿用规范入口的原事项及去重依据。

`prepare-renewal-evidence` 属于业务扩展；`plan-proactive-service` 与 `close-service-case` 属于内部处理。别名不重复产生候选、事项或发送机会。

## 当前文件与职责

```text
agent/
  skills/<能力名>/SKILL.md     独立服务能力，当前41个目录
  guides/                     六份共用服务规范
  config/service.yaml         集中维护的服务规则小样和待确认口径
knowledge/
  service/                    原动作资料，含FAQ及全局规则
  lesson/                     课程维护原稿
  product/                    产品维护原稿
evaluations/service-skills-audit-20261003/
                              本轮核对、模拟、回归及冻结输入
docs/                         交接入口、阶段成果和测试记录
```

| 组成 | 当前职责与边界 |
| --- | --- |
| [服务基本要求](../agent/guides/service-basics.md) | 真实资料、暂停、事项接续、发送复核、证据与时间精度、对客表达；设计上每次运行提供。 |
| [沟通原则](../agent/guides/communication.md) | 正向沟通、情绪、事实、期待与可兑现承诺；答疑必须读取，其他需要的能力明确引用。 |
| [督学规范](../agent/guides/supervision.md) | 补课、督学、家长渠道和孩子口信。 |
| [互动规范](../agent/guides/interaction.md) | 课程锚定、互动轮换、语音回应及邀请频率。 |
| [高光与反馈规范](../agent/guides/learning-feedback.md) | 高光组织、作品与能力证据、适用输出与素材边界。 |
| [答疑规范](../agent/guides/qa.md) | 身份、意图、情绪、问题类型、知识与表达，以及核实和人工衔接。 |
| [服务规则](../agent/config/service.yaml) | 规则参数集中维护，含待确认项；当前 `automatic_triggers_enabled: false`，不是已运行的触发表或调度器。 |
| System Prompt | 在本次核验的 `agent/` 下尚无 `system.md`；已有设计建议不能视为已交付、已部署的系统提示词。 |
| 家庭业务状态 | 暂停、约定、未结束事项、负责人和执行回执应由后端可靠存取，不能只放进长期记忆或聊天上下文。 |

## 依赖加载约定

每个 Skill 使用 `SKILL.md`，具有 `name`、`description` 和项目依赖元数据。例如现有课程讲解能力声明：

```yaml
metadata:
  tutor-required-guides: "service-basics,communication,qa"
```

`tutor-required-guides` 是本项目自定义字段。生产加载器需要实际解析它并提供对应正文；不能假设 Hermes 或 OpenViking 原生自动执行这个约定。正文同时保留规范直链，供检查与人工演练读取。依赖及配置应与 Skill 一起发布，当前相对链接还依赖项目目录布局，不能孤立复制一个文件夹后默认所有引用仍有效。

正文中的知识入口表达业务匹配要求：学员级别、书目、课程版本、原课节和适用范围。后续可绑定真实 OpenViking 资源或课程查询工具，但须返回适用正文，不能把检索摘要或相邻课节当作已读取目标资料。

## 接续时不能丢掉的业务区别

- 时间触发和条件触发都只是候选入口；内部复核也不自动获得再次联系许可。
- 暂停按对象、动作范围和恢复条件生效。暂停催课不阻断当前答疑；补课完成或一次新咨询不会自动解除暂停。
- 补课始终关联原课节，不能按补课当天换用另一课。课程版本与书籍出版版本分别维护。
- 草稿、媒体成品、发起发送、送达、阅读/收听、理解分别记录。没有播放记录是“收听未知”，也不能写成“未收听”。
- 发送结果未知先查询原操作；不能换草稿ID再发一份规避重复检查。
- 完课支持参与描述，理解、表达和迁移需要实际作品或作答；有提示的回答不能冒充独立掌握。
- 查询到已完课时，记录核实时间；实际完成时刻缺失就保留未知。
- 人工请求已提交、真人已接单、承诺反馈进展和最终解决是不同状态。电话由真人执行；退款咨询被接收不等于批准退款或到账。
- 同一消息可以合并多个目标，但各事项完成依据分别判断。关闭分享或一次交付，不能顺带关闭补课、退款或未答问题。
- 对客消息简短自然，详细核验依据、内部字段和工具故障留在事项记录；对用户有影响的未完成部分仍需如实说明。

## 已记录的缺口与待确认规则

### 记录和运行缺口

模拟中主动候选仅以字符串保存，部分目标Skill、来源事件、规则版本与去重依据缺失；一项待交付语音未显式保存负责人；语音恢复补发没有留下必要性判断依据。此次没有导致实际模拟漏办，但后端接入要通过持久化与校验补齐，不能只写进提示词。

尚未覆盖生产中的并发新消息、发送前状态竞态、跨进程中断恢复、不同草稿的重复语义、人工拒接/超时、真实媒体时长、真实消息回执等。榜单更正、21天勋章失败、成长视频超时长等动作分支也未实际演练。

### 业务口径

以下以 `service.yaml` 的待确认标记及最新业务结论为准，不由开发人员自行补成制度：

| 问题 | 需要确认的内容 |
| --- | --- |
| 连续缺课 | 项目说明的连续3次计划课节未上课，与动作表中连续2天零完课的适用关系；请假、跨周与计数口径。 |
| 补课48小时 | 从未回应、约定结束还是其他时点起算；与效果疑虑48小时复核分别处理。 |
| 待核实问题30分钟 | 指首次响应、核实流转还是最终解决，不直接承诺30分钟解决。 |
| 连续学习与里程碑 | 自然日/教学日、休息日如何计入；不能把阈值数字直接等同可执行判定。 |
| 整书首次高光 | 首本书还是每本书首次完成。 |
| 互动与全局频控 | 周范围、醒着时段、每日两条的适用范围、例外、合并计次和即时回复边界。 |
| 人工与分层 | 实际真人队列、严重缺课阈值、适用联系窗口及可兑现承诺。 |

规则待确认不妨碍不受影响的咨询、内部查询或草稿准备。已批准规则只有在执行器、状态和权限均接通后，才能支持自动服务。

## 后续会话入口

业务状态方案仍以[教辅数字员工服务状态与规划设计交接](教辅数字员工服务状态与规划设计交接.md)为准，本次不修改该计划。环境操作见[远程服务器与Git交接](远程服务器与Git交接.md)，课程读取见[课程知识记录](OpenViking课程知识优化与测试.md)。

接手时核对本次任务涉及的文件、实际加载配置和运行证据；开发完成后更新结果、证据和剩余责任。文件同步、运行注册、服务重载、验证、Git提交分别记录，不能互相代替。

修改Skill的检查顺序：原业务内容核对 → 必读依赖与链接检查 → 针对变化选择场景 → 模型执行与语义复核 → 保存新证据。历史运行及其失败不覆盖。
