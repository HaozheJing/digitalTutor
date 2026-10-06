# Skill 元数据目录

> 2026-10-05最新交付：隔离L已实现日内承接、按时间执行与可空完成条件；test15服务器532项程序通过，native06新状态10案/52 native/384 API全部Flash、程序10/10通过。独立工程、语义及未覆盖范围见[实施记录](第四阶段日内承接与完成条件实施记录.md)文末；4B/4C整体验收未签通过，本轮停在4C。下文H/J/K及较早L结论为历史证据，主目录与亲测数据未替换。

> 日内承接方向更新（2026-10-05）：后续规划Skill需适配每日基线/受限接纳、执行Skill需读取已提交安排，依据[日内承接与按时执行设计](日内承接与按时执行设计.md)。本轮未改正式Skill、必读规范或批准指纹，也未新增真实材料授权或正式Skill语义通过声明。

K隔离适配补充：正式Skill、必读规范和材料批准pin均未改写。宿主返回及角色运行稿明确：课程正文可读不代替专业Skill必读规范；同一讲解意图不能改称“仅据正文/设计建议”绕过依赖并清未答。可读取的试点材料也不构成正式业务审核、版本、班期映射或权益确认。上述是运行边界适配，尚未覆盖的正式Skill不能借合成职责测试记为语义通过。

> 当前执行状态（2026-10-04）：隔离K实现及冻结验证结束，361项服务器程序测试通过，32个真实Flash案例完成独立评审；4B/4C关口未通过。反复出现未来通知承诺和教学方法来源错误，另有恢复计划缺下一步动作，按主方案第10条第4项暂停受影响分支并提交取舍。主目录H及亲测数据未替换，不进入4D。详见《第四阶段4C隔离K交付与待决记录.md》。





版本：0.3。核对日期：2026年10月4日。维护责任：开发主控维护索引与运行接入，业务负责人确认服务规则。与 [Tool 元数据目录](Tool元数据目录.md) 配套使用。

Skill记录何时采用一种服务方法、需要哪些资料与规范、如何判断完成。当前 `agent/skills/` 有 **41个目录：34个家庭服务规范入口、2个内部处理入口、1个业务扩展入口、4个名称别名**。这些文件已形成，但不能仅凭目录存在认定运行时已经加载。

## 元数据约定

| 字段 | 含义与权威来源 |
| --- | --- |
| `skill_id` | front matter中的`name`，与目录名一致；与Tool使用不同名称空间 |
| `title` / `description` | 正文标题与front matter使用场景摘要 |
| `kind` | 家庭服务、内部处理、业务扩展或名称别名 |
| `entry_conditions` | 具体适用条件、触发与等待边界，引用Skill正文；有触发不等于有发送许可 |
| `required_guides` | 原样读取`tutor-required-guides`，解析到`agent/guides/` |
| `data_dependencies` | 所需学情、课程、作品、家庭状态等；逐项绑定在后续接入中确认 |
| `tool_dependencies` | 区分正文已经声明的工具id、建议接入的能力和实际已验证绑定；当前尚未逐Skill冻结 |
| `output_and_completion` | 产物、允许去向和完成依据，正文为准；记录未做事项，不能用草稿替代发送 |
| `intended_role` | 建议运行角色及待适配说明，不产生工具权限 |
| `canonical_skill` | 别名的规范入口；别名不产生额外候选、事项或触达额度 |
| `status` / `evidence` | 文件存在、服务器同步、运行加载、场景验证分别记录 |
| `revision` / `source` | SKILL.md路径、发布记录或内容指纹；当前未逐Skill声明语义版本 |

本文是维护索引，具体服务步骤、阈值、话术及完成标准仍在对应SKILL.md；共用要求仍在guides；规则数值仍在 [service.yaml](../agent/config/service.yaml)。不把正文复制成第二份规范。目录变更本身不改变调度、角色或权限。

## 共同状态和角色

本版共同状态为：**41个目录库存不变；只读加载器已实现，原生读取探针通过；逐Skill真实模型执行与业务语义尚未全部验收；自动触发关闭**。已有关联的历史模拟记录见 [Skill核对与模拟报告](服务Skills核对与模拟测试报告.md)，不能转写为41项均已在本版后端运行通过。第一阶段33项程序测试验证工具和业务核心，未执行这些Skill的真实模型闭环。

服务器同步状态：本次逐文件核对41份SKILL.md、6份guides及service.yaml，共48个agent文件与指定服务器哈希一致。两份元数据目录同步到 `/root/autodl-tmp/digitalTutor/docs/`；此状态只确认文件同步。

加载器已将家庭服务、业务扩展及`close-service-case`限定给`service`；`planner_daily`与`planner_progress`只发现`plan-proactive-service`。后者必须使用当前运行绑定的daily/progress配方，不能让普通来信先经过Planner。`close-service-case`只能辅助service整理事实并通过授权工具报告，不能直接改Event或越权关闭事项。

两项内部Skill旧正文保留来源版本，加载结果新增`runtime_constraints`明确覆盖旧入口和写入方式：只有daily可新增正式事项和确定今日成员；progress只更新绑定报告关联原事项；service通过`progress.submit`报告，独立新需求写入待规划。该约束是当前后端合同适配，不代表已逐段改写并验收旧正文。

正文中的资料和工具要求不授予能力。六份必读规范依赖由项目加载器实际解析`tutor-required-guides`，随完整Skill正文一并返回；不是假设Hermes原生自动加载。

## 本阶段只读加载接口与证据

实现见[skill_loader.py](../backend/src/digital_tutor/skill_loader.py)，接线见[materials.py](../backend/src/digital_tutor/materials.py)。`skills.list`只列规范入口，`skills.read(skill_id)`仅接受批准编号，不接受模型提供的路径、角色或任意URI。

| 接入项 | 当前行为与验证边界 |
| --- | --- |
| 原生复用 | 调用Hermes原生`tools.skills_tool.skill_view(name, preprocess=False)`；不修改vendor，不开放原生包含`skill_manage`的整个skills工具组 |
| Profile与读取来源 | 独立进程固定`HERMES_HOME`与cwd，`skills.external_dirs`指向批准目录；核对原生返回`_source_path`精确命中和正文一致，拒绝同名遮蔽 |
| 预处理 | 强制`preprocess=False`，不执行Skill模板或inline shell；profile同时关闭inline_shell与自动安装 |
| 必读规范 | 完整返回声明的guides；Skill与规范保存SHA256，实例存续期间变更立即拒绝，不能悄悄换新正文 |
| 路径与别名 | 阻止绝对路径、`..`、目录分隔符及解析后的越界；4个别名映射唯一规范入口，不增加候选、事项或发送额度 |
| 角色发现范围 | service为36个规范入口；两种planner各1个内部规划入口；admin为37个规范入口。41是含4个别名的文件库存，不能与可执行能力数量混用 |
| 当前实际探针 | 服务器独立profile原生读取`explain-lesson`成功，1608字符，来源及全文与批准文件一致，SHA256=`570277f0e056fe8561d8e8263b8a9fe3a869457b62ae7c5722f8d54c3f21664c` |
| 真实模型状态 | 10月4日用户授权后，explain-lesson在service、plan-proactive-service在daily/progress实际加载，含必读guides；随后模拟回复及规划提交成功。见[补充验收](../evaluations/backend-stage3/stage3-materials-20261004/acceptance.json)。只证明这3条链路，未将全部41项标为语义验收通过 |

独立接口测试见[test_stage3_knowledge.py](../backend/tests/test_stage3_knowledge.py)，原生读取调用与指纹记录见[接线与探针交接](../work/design-inputs/stage3-knowledge-readers.md)。模型是否正确选择Skill、遵循其中规范、恰当处理真实家庭，需要另列运行证据与业务评审。

指纹口径：下方库存表沿用文件原始字节SHA256前12位；加载器和原生读取探针对UTF-8正文统一换行后计算完整SHA256。含CRLF的文件可能出现不同值，例如`prompt-backlog-makeup`原文件为`8834db95cdaf…`，加载正文为`c60e2505f99b…`，这不是正文变更。比较时必须保持相同口径。

## Skill 清单

下列描述和规范依赖直接取自当前文件；内容指纹为SHA256前12位，只用于识别本次快照，不能代替正式发布版本。共同状态和角色按上一节继承。

<!-- SKILL_INVENTORY_START -->

### 家庭服务 督学与学习安排

| Skill与名称 | 场景摘要 | 必读规范 | 内容指纹 |
| --- | --- | --- | --- |
| [remind-reading](../agent/skills/remind-reading/SKILL.md) · 今日阅读提醒 | 在当天课节尚未完成且进入允许评估的时间窗口时，结合当前课节内容准备一次轻量阅读提醒；也可承接老师或家庭提出的提醒请求。发送前核对暂停、请假、已有约定和近期触达，次日再核验实际学习结果。 | `service-basics`、`supervision` | f4e0bac402d2 |
| [celebrate-lesson-completion](../agent/skills/celebrate-lesson-completion/SKILL.md) · 当课完成鼓励 | 在确认孩子完成当天课节后，结合可核实的课程表现准备具体而克制的鼓励；完成记录不足以支持具体表现时只描述参与事实，并保留后续问题或学习证据需求。 | `service-basics`、`supervision`、`learning-feedback` | bc3adc3289a1 |
| [prompt-backlog-makeup](../agent/skills/prompt-backlog-makeup/SKILL.md) · 往期课程补读 | 当日新课已经完成而往期仍有缺课时，汇总累计缺课并提供原课节的补读入口与鼓励口信。已有约定或暂停时接续原事项，不重复提醒。 | `service-basics`、`supervision` | 8834db95cdaf |
| [celebrate-learning-streak](../agent/skills/celebrate-learning-streak/SKILL.md) · 连续学习鼓励 | 核实孩子连续三天及以上完课后，准备一次坚持鼓励和有兑现依据的勋章预告；连续计数口径未确认时仅整理事实，不把学习天数推断为稳定习惯或能力。 | `service-basics`、`supervision`、`learning-feedback` | 5bc87b7b589c |
| [plan-makeup-learning](../agent/skills/plan-makeup-learning/SKILL.md) · 补课规划与跟进 | 在零完课或连续缺课信号需要评估、家长提出补课请求、老师要求协助安排，或原补课事项到期复核时使用。结合缺失课节、已知原因、暂停和有效约定，协商可执行的补课安排并持续核实进展。 | `service-basics`、`supervision` | 6c5386dd32dd |
| [support-course-continuity](../agent/skills/support-course-continuity/SKILL.md) · 课程衔接支持 | 发现前后课节已完成而中间课节缺失的跳课模式时，帮助家庭接回原课程顺序；按原缺失课节核对资料和安排，不把所有缺课都当成跳课。 | `service-basics`、`supervision` | 9ab04860e90a |
| [rebuild-reading-rhythm](../agent/skills/rebuild-reading-rhythm/SKILL.md) · 重建阅读节奏 | 在上周参与偏低且本周尚未开始，或老师请求重新安排阅读节奏时使用。根据真实课表与完成记录设置一个可执行的小目标，向家长说明安排并在后续核验，不把一次提醒当成习惯已经恢复。 | `service-basics`、`supervision`、`communication` | 0f94323ea79f |
| [close-week-reading-gap](../agent/skills/close-week-reading-gap/SKILL.md) · 本周学习收尾 | 本周应上课程仅当天课节未完成、其余均已完成时，准备一次轻量收尾提醒和给孩子的冲刺口信；其他缺课范围进入相应补课服务。 | `service-basics`、`supervision` | 340df8bbef3d |
| [plan-weekend-makeup](../agent/skills/plan-weekend-makeup/SKILL.md) · 周末补课安排 | 周六补课评估节点发现本周至少一节未完成或零完课，或家庭主动请求周末补课时，整理逐课亮点、补课清单和有效二维码入口；发送与形成约定分别核对当前许可及家庭意愿。 | `service-basics`、`supervision` | 9a5c9c588862 |
| [weekly-learning-review](../agent/skills/weekly-learning-review/SKILL.md) · 一周学习回顾 | 在周度回顾节点，或老师、家长请求总结一周学习时使用。核对本周完成与缺课分层，结合下周课表和课程预告形成可核查的回顾与下一步安排。 | `service-basics`、`learning-feedback`、`supervision`、`communication` | 5c56b5b8ad23 |

### 家庭服务 互动

| Skill与名称 | 场景摘要 | 必读规范 | 内容指纹 |
| --- | --- | --- | --- |
| [share-reading-reflection](../agent/skills/share-reading-reflection/SKILL.md) · 阅读感受交流 | 当周互动轮换选中阅读感受交流、老师要求准备分享，或孩子回复既有分享时使用。老师先分享课程阅读感受，再开放提问，语音回应孩子并整理感受给家长。 | `service-basics`、`interaction` | 9339e2174dc5 |
| [quiz-course-knowledge](../agent/skills/quiz-course-knowledge/SKILL.md) · 趣味知识互动 | 当周互动轮换选中趣味知识互动、老师要求准备互动，或孩子回复既有题目时使用。围绕当周新知设计二至三个趣味问题，语音点评，再邀请孩子去考考家长。 | `service-basics`、`interaction` | 43d428f4b417 |
| [practice-reading-method](../agent/skills/practice-reading-method/SKILL.md) · 阅读方法练习 | 在当周互动轮换选中方法练习、老师要求准备练习，或孩子回复既有方法练习时使用。发起时依据当周课程示范阅读方法或句式，提供句式卡并邀请尝试；收到回答后沿用原练习要求，给出具体肯定和一个提升点。 | `service-basics`、`interaction` | 635aa39b7775 |
| [connect-reading-to-life](../agent/skills/connect-reading-to-life/SKILL.md) · 阅读与生活联想 | 当周互动轮换选中阅读与生活联想、老师要求准备情境问题，或孩子回复既有问题时使用。以故事情境提出生活化假设，接纳孩子表达并联系课程寓意。 | `service-basics`、`interaction` | bdda33f39f15 |
| [celebrate-important-day](../agent/skills/celebrate-important-day/SKILL.md) · 重要日子关怀 | 在孩子生日、适用节日或考试季等节点准备个性化关怀。结合孩子名字和阅读元素，通过家长私聊发送文字或贺卡，并与生日高光协调避免重复。 | `service-basics`、`communication`、`interaction` | 5ea2ca92c322 |

### 家庭服务 高光与成长

| Skill与名称 | 场景摘要 | 必读规范 | 内容指纹 |
| --- | --- | --- | --- |
| [highlight-course-start](../agent/skills/highlight-course-start/SKILL.md) · 首日阅读成长纪念 | 在开课第1天完成首课后，核实首课内容和孩子真实表现，为家长准备首日完课图文高光卡。 | `service-basics`、`learning-feedback` | 6082c293ddee |
| [highlight-first-perfect-answer](../agent/skills/highlight-first-perfect-answer/SKILL.md) · 首次课中题全对成就 | 在孩子首次课中题全部答对且题目与作答记录可核验时，准备全对成就图文卡，区分评分结果与独立掌握。 | `service-basics`、`learning-feedback` | 3221c2a5b13b |
| [highlight-seven-day-completion](../agent/skills/highlight-seven-day-completion/SKILL.md) · 七日学习成就 | 在明确的7日窗口内完成5天课程后，核实课节与完课事实，准备七日学习成就图文卡；不要求连续7天每天学习。 | `service-basics`、`learning-feedback` | b34c367341df |
| [highlight-book-completion](../agent/skills/highlight-book-completion/SKILL.md) · 完书成长纪念 | 在核实全书课程完成与书架点亮状态后，结合书名和孩子书内金句准备完书图文纪念；首本与每本首次的适用范围按已确认规则执行。 | `service-basics`、`learning-feedback` | 1f24cd73df19 |
| [highlight-second-week-attendance](../agent/skills/highlight-second-week-attendance/SKILL.md) · 第二周全勤鼓励 | 在班期第2周全部应上课节符合全勤口径后，结合第1周实际记录准备第二周全勤图文鼓励。 | `service-basics`、`learning-feedback` | c6659bcfd739 |
| [highlight-reading-milestone](../agent/skills/highlight-reading-milestone/SKILL.md) · 持续阅读里程碑 | 在连续21天完课达标的规则与记录核实后，准备习惯曲线、课内勋章和家长图文卡；100天或365天扩展仅在规则启用时使用。 | `service-basics`、`learning-feedback` | ea2298e1d27e |
| [highlight-quiz-ranking](../agent/skills/highlight-quiz-ranking/SKILL.md) · 周度课中题正确率冠军 | 在真实周课中题正确率榜单确认学生为第一名后，准备每周一次的冠军图文成就；不把任意上榜或内部排序当作冠军。 | `service-basics`、`learning-feedback` | 6a3ba3bf22de |
| [highlight-expressive-answer](../agent/skills/highlight-expressive-answer/SKILL.md) · 每周精彩表达记录 | 从本周互动语音的可靠转写中选取孩子精彩表达，结合课程语境准备每周一次的图文高光记录。 | `service-basics`、`learning-feedback` | b63c71786628 |
| [highlight-ranking-progress](../agent/skills/highlight-ranking-progress/SKILL.md) · 学习榜单晋级 | 在可核验的前后榜单档位显示学生晋档时，准备晋级图文反馈；普通名次波动不自行等同晋档。 | `service-basics`、`learning-feedback` | df8b1deec230 |
| [highlight-reading-birthday](../agent/skills/highlight-reading-birthday/SKILL.md) · 阅读生日纪念 | 在孩子真实生日到来时，结合其这一年的阅读足迹、书单或金句制作生日图文卡，并与重要日子关怀合并避免重复联系。 | `service-basics`、`communication`、`learning-feedback` | b5fd51aabf64 |
| [review-learning-progress](../agent/skills/review-learning-progress/SKILL.md) · 阶段学情反馈 | 在月度反馈节点，或家长、老师请求回顾一个明确学习阶段时使用。汇总该阶段高光、互动表达、作品与学习记录，区分参与事实和能力证据，形成面向家长的学情反馈与可执行建议。 | `service-basics`、`learning-feedback` | 69686b437490 |
| [prepare-growth-review-package](../agent/skills/prepare-growth-review-package/SKILL.md) · 成长回顾材料 | 在家庭进入课程决策期时，汇集全程高光、勋章、月报、金句、作品和学习数据，以一封信或学期成长报告合集支持私聊或会议沟通。 | `service-basics`、`learning-feedback` | 82800d685df0 |
| [produce-growth-video](../agent/skills/produce-growth-video/SKILL.md) · 成长影像制作 | 为已确认适用的高价值或决策期家庭，用孩子真实音频和高光卡模板化制作不超过60秒的成长视频，每学期一次；工具不足时保留脚本与待制作责任。 | `service-basics`、`learning-feedback` | 178e98d8844f |

### 家庭服务 答疑与人工衔接

| Skill与名称 | 场景摘要 | 必读规范 | 内容指纹 |
| --- | --- | --- | --- |
| [explain-lesson](../agent/skills/explain-lesson/SKILL.md) · 课程内容讲解 | 回应家长代问或孩子直接提出的阅读课程问题，包括故事理解、阅读方法和表达练习。读取适用课节，提供给家长的解释和给孩子的讲法，并引导举一反三。课程安排、系统操作和投诉按对应服务路径处理。 | `service-basics`、`communication`、`qa` | 570277f0e056 |
| [answer-course-service-question](../agent/skills/answer-course-service-question/SKILL.md) · 课程服务答疑 | 回应课程安排、学习入口、操作与技术等服务问题，读取适用 FAQ 给出简洁解释和下一步。学习内容、效果质疑及投诉退费按相应服务能力接续。 | `service-basics`、`communication`、`qa` | b9be2971168e |
| [address-learning-concern](../agent/skills/address-learning-concern/SKILL.md) · 学习效果沟通 | 家长表达“没效果”、想暂停或犹豫是否继续学习时使用。先回应情绪，再结合真实学情呈现证据，提供可选择的调整方案并安排二次跟进和必要的真人协作。 | `service-basics`、`communication`、`qa`、`learning-feedback` | 489631974552 |
| [handle-complaint-escalation](../agent/skills/handle-complaint-escalation/SKILL.md) · 投诉受理与人工衔接 | 家长提出退费申请或课程服务投诉时使用。先接住情绪，整理相关学情档案和事实时间线，交给真实配置的真人渠道，并跟踪接单和处理结果。 | `service-basics`、`communication`、`qa` | 9906ddc0a74b |
| [respond-to-child-sharing-help](../agent/skills/respond-to-child-sharing-help/SKILL.md) · 孩子分享与求助回应 | 回应孩子经家长手机或授权通道发来的非学习分享、情绪或求助。用简短儿童语态和语音回应，真实困扰时提醒合适成人关注，风险情况按实际人工流程处理。 | `service-basics`、`communication`、`qa` | 47e90b84419a |
| [follow-up-unanswered-question](../agent/skills/follow-up-unanswered-question/SKILL.md) · 待核实问题跟进 | 在普通咨询经确认意图与检索后仍缺少可靠答案、资料冲突需要核实，或已有核实事项收到结果或到期时使用。保留原问题，协调实际人工核实渠道，追踪可兑现的答复安排并回复家庭。投诉退费及其他高风险问题走相应真人处理路径。 | `service-basics`、`communication`、`qa` | bbbdd2c62485 |

### 内部处理

| Skill与名称 | 场景摘要 | 必读规范 | 内容指纹 |
| --- | --- | --- | --- |
| [plan-proactive-service](../agent/skills/plan-proactive-service/SKILL.md) · 主动服务规划 | 在接收时间触发、条件触发或到期事项时，整理需要评估的主动服务候选。核对最新学习事实、服务状态、暂停与频控，决定进入队列、等待、合并、暂停或转人工，不直接把触发当成发送许可。 | `service-basics` | 55ad0988b98f |
| [close-service-case](../agent/skills/close-service-case/SKILL.md) · 服务事项收尾与复盘 | 在一次答疑或服务执行结束后整理事项、回执和可复用内容时使用。记录真实处理结果、未解决责任、命中情况和满意度未知状态，提出待审核知识候选，但不把消息发送或草稿生成直接视为问题解决。 | `service-basics` | b73ed5e9026a |

### 业务扩展

| Skill与名称 | 场景摘要 | 必读规范 | 内容指纹 |
| --- | --- | --- | --- |
| [prepare-renewal-evidence](../agent/skills/prepare-renewal-evidence/SKILL.md) · 准备成长与续费材料 | 在家庭进入课程决策期、老师需要准备续费沟通材料，或家长询问孩子阶段收获时使用。汇集真实高光、勋章、月度反馈、金句、作品和数据，形成可核查的成长材料与可选学习建议。 | `service-basics`、`learning-feedback`、`communication` | 063a711f9457 |

### 名称别名

| Skill与名称 | 场景摘要 | 必读规范 | 内容指纹 |
| --- | --- | --- | --- |
| [recommend-catch-up-reading](../agent/skills/recommend-catch-up-reading/SKILL.md) · 名称重定向：往期课程补读 | 规范入口：[prompt-backlog-makeup](../agent/skills/prompt-backlog-makeup/SKILL.md)。名称重定向入口；需要往期课程补读时读取 prompt-backlog-makeup，不单独评估或执行触达。 | `service-basics`、`supervision` | 9eb607413040 |
| [reinforce-reading-streak](../agent/skills/reinforce-reading-streak/SKILL.md) · 名称重定向：连续学习鼓励 | 规范入口：[celebrate-learning-streak](../agent/skills/celebrate-learning-streak/SKILL.md)。名称重定向入口；需要连续学习鼓励时读取 celebrate-learning-streak，不单独评估或执行触达。 | `service-basics`、`supervision`、`learning-feedback` | 7d9885937024 |
| [restore-course-sequence](../agent/skills/restore-course-sequence/SKILL.md) · 名称重定向：课程衔接支持 | 规范入口：[support-course-continuity](../agent/skills/support-course-continuity/SKILL.md)。名称重定向入口；需要课程衔接支持时读取 support-course-continuity，不单独评估或执行触达。 | `service-basics`、`supervision` | 19e09c869160 |
| [review-weekly-learning](../agent/skills/review-weekly-learning/SKILL.md) · 名称重定向：一周学习回顾 | 规范入口：[weekly-learning-review](../agent/skills/weekly-learning-review/SKILL.md)。名称重定向入口；需要一周学习回顾时读取 weekly-learning-review，不单独评估或执行触达。 | `service-basics`、`learning-feedback`、`supervision`、`communication` | e6383a5f400a |

<!-- SKILL_INVENTORY_END -->

## 共用依赖

| guide_id | 文件与职责 |
| --- | --- |
| `service-basics` | [服务基本要求](../agent/guides/service-basics.md)：事实、权限、暂停、接续和发送复核 |
| `communication` | [沟通原则](../agent/guides/communication.md)：情绪、表达、期待与承诺 |
| `supervision` | [督学规范](../agent/guides/supervision.md)：补课、督学、家长渠道与孩子口信 |
| `interaction` | [互动规范](../agent/guides/interaction.md)：课程锚定、互动轮换、反馈 |
| `learning-feedback` | [高光与反馈规范](../agent/guides/learning-feedback.md)：作品、能力证据与素材边界 |
| `qa` | [答疑规范](../agent/guides/qa.md)：身份、意图、知识、核实与人工衔接 |

`automatic_triggers_enabled: false`来自当前`service.yaml`。其中待确认的阈值、频率、时限保留原状态；本目录不重新定义它们。后端已接受的08:00至21:00窗口见Tool目录与后端配置，不能据此认定旧规则小样整体已生效。

## 工具依赖接入建议

这是**待逐Skill确认的能力映射**，不代表正文已经声明这些tool_id，也不是运行授权白名单。

| 服务需求 | 已有候选工具 | 尚缺的接入或验证 |
| --- | --- | --- |
| 当前消息与家庭状态 | `context.read`、`state.read`、`wechat.read_messages`、`message.interpret` | 三配方已实现；真实消息适配及逐Skill语义选择继续验收 |
| 学情事实 | `learning.schema`、`learning.query_sql` | 真实课程/课节绑定、覆盖时间及在线事实仍须业务来源确认 |
| 暂停与事项连续性 | `control.apply`、`planning.record_pending`、`progress.submit` | 意图和证据语义核验；不能直接用Skill正文改库 |
| 家长答复与文本交付 | `wechat.send_message`、`wechat.get_operation` | 模拟渠道与模拟频控已实现；真实渠道、媒体与语义审查待接入 |
| 内部规划 | `planner.commit_daily`、`planner.commit_progress` | 仅对应Planner角色；加载结果带V1合同约束，内部Skill模型使用继续验收 |
| 课程和产品解释 | `knowledge.list`、`knowledge.read` | 固定清单与限定正文读取已实现；未知真实课程绑定拒绝；第1课的授权模型读取链路通过，其他课节及产品模型解释仍需相应验收 |
| 方法与共用规范 | `skills.list`、`skills.read` | 原生只读加载、必读规范与角色限制已实现；逐Skill工具使用及服务效果待验收 |
| 语音、图片、成长视频、人工接续 | 当前业务注册表未提供对应工具 | 素材制作、授权交付、人工接单及结果查询；缺能力不能声称完成 |

`wechat.receive_message`和`wechat.mock_receipt`仅用于测试渠道，不分配给家庭服务Skill。Skill可以准备草稿或内部建议，实际动作必须经过该运行角色允许的工具。

## 后续维护

新增Skill时登记稳定id、用途、类别、必读规范、依赖、产物与完成依据、来源和状态；先确认是否已有同一服务目标的入口，避免按每种触发复制能力。兼容旧名称使用别名并指向唯一规范入口。

修改Skill时先修改SKILL.md或共用来源，再同步本目录的摘要、依赖和指纹；业务变化同时更新配置、适用场景与验证依据。后续逐项填写实际tool_id绑定和运行配方，不用“已规划”代替“已接入”。

发布核对目录名与name一致、规范及相对链接可访问、别名无循环且指向规范入口、待确认规则未被启用；再分别记录服务器文件同步、运行加载、模型执行和业务语义验收。历史失败和旧版本证据保留，不覆盖成新的通过结论。

变更记录：2026年10月3日建立0.1版，登记41个目录及6份规范。当天0.2版补充原生只读封装、必读规范与指纹、别名和角色限制、内部Skill当前合同覆盖、实际原生探针与真实模型待验收边界；没有改变库存或启用自动触发。

## 第四阶段4A材料接线（实施中）

新Worker Host固定批准explain-lesson、plan-proactive-service及原必读指南，隔离目录和正文均受固定指纹约束。未修改这些已批正文；其他正式Skill仍仅本地适配范围，未获新增API材料授权，也未签署语义通过。S链通过不得记为正式Skill通过。


### 第四阶段4A关口记录（2026-10-04）

4A D版仅验证原批准材料装配与实际读取；explain-lesson/plan-proactive-service原版本保持不变。新V1适配候选另存work/stage4/material-proposal，未获新增材料授权前不进模型、不计正式Skill通过。


### 第四阶段4B1通过与4B2范围

4B1 G通过仍仅使用原批准Day1、explain-lesson、plan-proactive-service及原必读规范，严格模型目的地与材料pin未扩展。4B2将验证已选Skill缺必读规范时的实际模型处理，故障只在隔离稳定点注入；未成功读取不算规范语义通过。四份V1正式Skill候选仍未批准、未进入API，C/D提案未应用。用户要求4B通过后暂停亲测。


第四阶段4B2 A/B仍使用原批准Skill及必读规范，正式文件与批准pin未变。新增source_index属于宿主通用来源定位，不向模型预填测试答案。B16类程序通过尚非正式Skill完整语义通过；未来承诺等缺陷仍修复。未批准Skill候选继续只保存在work提案目录，未进入模型；家庭、微信、作品、学情为合成，真实Excel只做服务器只读程序验收。


### 4B最终冻结交付与暂停

4B的Day1课程服务按实际读取的原explain-lesson及必读规范完成本批语义验证。plan-proactive-service仍在原批准可读范围内，这不代表其daily/progress专业链已验收。学情/作品/入口为虚构来源，不能当成其他正式Skill已通过；修订候选与新增指南仍未批准、未进模型。daily/progress专业完整链及媒体能力未验收。 详见第四阶段交付记录与gate-decision。

2026-10-04 Flash G：仅flash/多多老师；SQL绑定、材料来源及失败回执合同已修复，批准材料正文未改。240回归和4B1工程通过，回答内容与用户共同评审，Flash 4B2未复验。完整变更、证据及限制见第四阶段交付记录和G fix-manifest。

2026-10-04 Flash H：4B2已完成冻结复验和双评审；逐意图未答合同补入发送参数，真实政策欠项误清已修复。244项回归和更正解析后的16案程序检查通过；内部暂停说明与教学完成条件仍有语义问题，关口未通过。Skill正文/批准范围不变，合成/本地材料不等同正式Skill语义通过。完整证据、各项限制及对话见《第四阶段4B2重点核对记录.md》，停在4B2。


## 4C独立开发与4B遗留联合计划（2026-10-04）

4C计划继续优先原批准R链，保留plan-proactive-service/explain-lesson及指南原pin。新增daily候选/Schema接线不代表新正式Skill正文获准入模型。S材料装配仅在确需合成正文时做独立清单和范围验证，正式新材料仍按具体差异处理授权。方法来源遗留和真实Skill语义评分保持未通过项。

详见[联合实施计划](第四阶段4C独立开发与4B遗留修复计划.md)。


## 4C联合实施进展（隔离J，2026-10-04）

隔离J仍使用原批准R材料、正式Skill/guide/pin原字节。修改的是service/planner_daily/common角色运行稿和宿主来源边界；未修改Hermes vendor或原生Skill读取。新增合成候选规则与周期文字职责不是新正式Skill语义通过；4B方法来源和daily实际使用正在同版Flash复验。

详见[实施进展](第四阶段4C实施进展.md)。旧批证据与未通过项保持原结论。


### 隔离K实现与验收限制（2026-10-04）

候选/消息阅读门禁/材料恢复/控制范围/有限daily及安全导出已实现于隔离K，主目录仍为H。361项程序通过不等于语义验收；K实际发送仍有未安排回告承诺和方法来源错误，且恢复计划下一步、报告remaining_work存在欠项。正式Skill与批准材料字节不扩充、不把模拟规则当正式政策；本轮不新增Planner入口。详见[当前交付与待决项](第四阶段4C隔离K交付与待决记录.md)，4B/4C gate=false。


## L完成说明的配置口径（2026-10-05）

服务动作可以补`completion_condition`文字说明；未配置时Service依据原请求、当前动作和实际结果自主判断，不强制澄清或生成占位说明。动作条件只约束动作；事项的课节、交付件及回执硬标准保持原样。新接纳保存采用的有效说明，后补模板不追溯覆盖旧责任。

本轮适配位于角色运行稿、候选/事项/步骤/报告合同与宿主链路。正式Skill、必读规范正文及R材料批准pin不变；后续改正文或增加正式材料进入模型仍按具体材料范围核对。该字段接通不等于尚未批准Skill已获正式语义验收。
