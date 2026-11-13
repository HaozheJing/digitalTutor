# digitalTutor 开发者技术说明与接手指南

公开文档整理日期为 2026-11-13；业务实现核对日期为 2026-11-12。本文描述本仓库的 Linux DSH fastloop 实现，业务核对基线为 253503e124126e9a3f69318109ad34cc9e739a7b。该基线之后的目录整理与业务开发分别管理，阅读时应同时核对提交、工作区和运行配置。

本文面向接手开发、维护接口、排查服务问题及准备运行环境的人员。内容依据源码、配置读取逻辑、文件指纹及已有隔离验证记录编写，重点说明代码如何协作、状态在哪里、哪些条件还需要接线。

本文只包含架构、代码入口、接口字段和占位示例。真实服务地址、账号密码、API 密钥、管理密钥、工作台授权和联系人路由通过公司私有文档交接。具体公开示例与注入方式见 [部署配置与凭据边界](reference/deployment-configuration.md)。

## 阅读导航

| 阅读阶段 | 章节 |
| --- | --- |
| 确认代码范围 | [整理提交与业务工作区](#整理提交与业务工作区)、[项目定位与实现范围](#项目定位与实现范围) |
| 理解架构 | [技术路线](#技术路线)、[技术栈与运行依赖](#技术栈与运行依赖)、[仓库结构](#仓库结构) |
| 理解运行过程 | [进程与调用边界](#进程与调用边界)、[启动与工作空间装配](#启动与工作空间装配)、[宿主生命周期与主循环](#宿主生命周期与主循环) |
| 跟踪业务 | [被动来信处理](#被动来信处理)、[主动服务、事项与排期](#主动服务事项与排期)、[学情、时间与完课事件](#学情时间与完课事件) |
| 修改方法和能力 | [Skill、动作与工具权限](#skill动作与工具权限)、[主要模型工具](#主要模型工具)、[知识与媒体](#知识与媒体) |
| 核对交付和状态 | [交付与回执](#交付回执与最后发送边界)、[人工核实与接管](#人工核实与接管)、[记忆与上下文](#记忆与上下文生命周期)、[SQLite 数据模型](#sqlite-数据模型与建表位置) |
| 准备运行和维护 | [配置与环境变量](#配置与环境变量)、[HTTP 接口与工作台](#http-接口与工作台)、[测试方法](#测试方法与验证口径)、[排障索引](#排障与维护索引) |
| 评估后续工作 | [已知缺口](#已知缺口)、[扩展检查清单](#常见扩展工作的检查清单)、[接手顺序](#接手阅读与修改顺序)、[Skill 索引](#skill-文件索引) |

## 整理提交与业务工作区

本次整理提交以 253503e 为父提交，单独记录目录归档、文档和 P0/旧 UI 退役差异。此前已有的业务代码、Skill 修改和退役继续保留在 Linux 工作区，等待独立提交。

本文的接口行为、49 个 Skill、121 个测试文件及验证结果描述 2026-11-12 工作区快照。单独的整理提交仍保留 50 个基线 Skill 目录，后续业务提交再承接相应变化。阅读共享源码时，应结合所查看的提交及工作区状态。

| 阅读目的 | 建议阅读内容 |
| --- | --- |
| 理解项目 | 定位与实现范围、技术路线 |
| 找到修改位置 | 目录结构、模块职责、维护任务索引 |
| 跟踪一次处理 | 进程边界、被动来信、主动服务、交付与人工交接 |
| 维护资料和方法 | Skill、工具能力、知识与媒体 |
| 配置和验证 | 环境、启动、测试、排障和已知缺口 |

## 项目定位与实现范围

digitalTutor 是面向阅读营家庭的阅读辅导智能体系统。它接收家庭消息、学习完成事件和定时唤醒，结合服务方法、课程资料、家庭状态及已有安排，组织回复或执行已获授权的服务。

主要服务包括阅读内容答疑、阅读邀请、完课鼓励、历史缺课补读、学习节奏关怀、周度回顾、互动、高光材料交付、已有安排变更，以及必要的人工核实。

| 内容 | 主要承担方 | 开发时关注的结果 |
| --- | --- | --- |
| 服务方法、表达规范和场景步骤 | Skill 正文及附件 | 本次需要阅读什么、依据什么判断 |
| 家庭、消息、事项和资料绑定 | DSH 插件与 Python 宿主适配器 | 当前所有者、上下文和权限 |
| 资料理解、具体措辞与受限工具选择 | 模型 | 生成内容和实际工具调用 |
| 排期、暂停、版本、交付和回执 | Python 业务层、SQLite、渠道适配 | 持久化状态、事务及发送结果 |
| 资料缺口与人工决定 | 人工交接账本及工作台 | 待办、接管、人工回复和处理记录 |

当前代码有以下接入边界。

- 学情装配主要通过 fixture_query、test_mode 和完课事件覆盖层完成。查询具有时间过滤、家庭范围和课程语义，默认数据来自本地合成夹具。
- completion_events 提供标准完课事件存储和模拟入口。真实学习系统还需要身份映射、权威学情获取及来源认证。
- 发送层具有模拟、允许名单测试渠道和家庭桥接等分支。实际运行采用的链路由配置和绑定决定。
- 根仓库的独立启动环境尚未配齐。入口指定的 Node、DSH 路径及 provider 配置需要部署时提供。
- 当前保留 49 个 Skill、36 个主动作编号和 121 个回归测试文件。各动作的接入和验证进度应分别核对。

源码见 [fixture_query.py](../adapters/fixture_query.py)、[test_mode.py](../adapters/test_mode.py)、[completion_events.py](../adapters/completion_events.py)、[service_catalog.py](../adapters/service_catalog.py)。

## 技术路线

项目采用 DSH 原生智能体运行时、Node.js 宿主插件、Python 受控业务工具、SQLite 持久化状态、OpenViking 公共知识检索，以及按家庭工作空间组织的配置。

~~~mermaid
flowchart TD
    I["家庭来信 / 完课事件 / 定时唤醒"] --> H["DSH + tutor-plugin"]
    H --> C["绑定家庭、事项、会话和上下文"]
    C --> L["模型"]
    S["Skill 正文与参考附件"] --> C
    K["OpenViking 公共知识"] --> Q["知识查询适配"]
    Q --> C
    L --> M["受任务约束的 MCP 工具"]
    M --> B["Python 业务校验与事务"]
    B <--> DB[("SQLite 状态与审计")]
    B --> D["交付适配与分段 outbox"]
    D --> R["渠道结果及后续回执"]
    R --> DB
    B --> A["人工核实与接管"]
    DB --> C
~~~

该路线的工程作用体现在以下方面。

1. 服务方法以可版本化文件维护，能够追踪某次判断使用的业务正文。
2. 宿主绑定家庭、事件和事项，模型沿当前上下文调用工具。
3. 关键修改通过事务、revision 和幂等键保存，重试时核对原操作。
4. 对客交付以渠道记录和回执为依据，生成文字与实际交付分别留有记录。
5. 公共知识、家庭状态和模型临时上下文具有各自的维护位置及生命周期。

## 技术栈与运行依赖

当前 fastloop 的处理单位是一次事件或一个到期事项。宿主装配上下文，模型沿 Skill 判断，业务工具核验并提交操作，回执再更新持久化状态，这构成当前的服务快循环。

夜间记忆承担证据整理和家庭记忆维护。当前工具契约将长期业务服务规划的慢循环保留为后续建设范围。接手时应分别评估当前事件处理、跨会话记忆和长期服务策略的实现及验证。边界见 [工具与配置契约](reference/tool-skill-contracts.md)。

| 层级 | 当前实现 | 说明 |
| --- | --- | --- |
| 智能体运行时 | @deepseek-ai/dsh 0.2.1-rc.2 | app/package.json 声明版本，并有依赖锁 |
| 宿主语言 | Node.js、ES Modules | 最近 Linux 对照验证使用 Node v22.21.1 |
| 业务工具语言 | Python 3 | 最近隔离验证使用 Python 3.14，尚缺统一依赖及支持版本声明 |
| 持久化 | SQLite | 标准库 sqlite3，模块按需建表和补充字段 |
| 模型工具协议 | MCP JSON-RPC over stdio | DSH 启动 Python 工具进程 |
| 宿主内部调用 | Python CLI + JSON | Node 同步调用脚本并解析 JSON |
| 公共知识 | OpenViking CLI | CLI 位置和凭据配置属于外部依赖 |
| 模型接入 | DSH provider 配置 | 模型名、兼容参数和预算按 profile 指定 |
| 操作接口 | Node HTTP、部分 Python HTTP 服务 | 工作台、事件接入、状态及内部控制 |
| 自动化测试 | Python unittest、Node test runner | 测试与实现相邻 |
| 媒体依赖 | 标准库及按功能引入的外部库 | 既有回归已暴露 Pillow 缺失 |

JavaScript 依赖由 app/package.json 和 package-lock.json 管理。profile 和插件还引用多个 DSH 子包，运行时需要版本一致的依赖目录。

源码见 [包声明](../app/package.json)、[依赖锁](../app/package-lock.json)、[profile 装配](../adapters/native_experiments.py)。

## 仓库结构

~~~text
digitalTutor-fastloop-dev-20261107/
├── app/                  DSH 插件、会话、定时器、启动和 Node 测试
├── adapters/             Python 业务、工具、状态、页面和回归测试
│   ├── legacy/           当前仍使用的名称与规则兼容实现
│   └── vendor/
│       └── digital_tutor/
│           ├── __init__.py
│           └── ark_media.py
├── skills/               49 个服务方法及附件、能力声明
├── ops/                  课程知识读取、工具、发布与隔离评估
├── knowledge/
│   └── curriculum/L2/    L2 课次和材料索引
├── assets/
│   ├── reading-guides/   20 份导读素材
│   └── weekly-review/    4 份周总结材料
├── config/               环境配置说明
├── runtime/              当前工作空间数据库、夹具与运行输出
├── docs/                 开发、业务设计和接口文档
├── legacy/               历史版本、演示代码、实验与验证记录
├── README.md
├── .gitignore
└── .git/
~~~

| 主要文件或区域 | 维护职责 |
| --- | --- |
| app/tutor-plugin.mjs | 宿主生命周期、轮询、会话、Python 调用和 HTTP 接口 |
| app/working-surface.mjs | 模型工作材料、任务结束清理及 DSH 压缩协作 |
| app/memory-runtime.mjs | 独立夜间记忆 preset 与提交后的完成登记 |
| adapters/mcp_server.py | 工具聚合、参数路由和调用校验 |
| adapters/service_workflow.py | 版本化安排、修改、履行、授权与恢复 |
| adapters/agent_plan.py | 任务领取、当前任务、Skill 读取及任务结束 |
| adapters/wechat_test.py | 入站、发送分段、出站状态及回执核对 |
| adapters/query_contract.py | 课程和业务知识的模型查询契约 |
| adapters/operator_alerts.py | 转人工待办、人工回复及交付关联 |
| ops/ | 课程知识的维护和隔离查询评估 |

目录职责见 [目录说明](DIRECTORIES.md)，按任务查找代码见 [开发指引](development.md)。

## 进程与调用边界

### DSH 主进程

app/start.py 读取 provider 配置，设置 DSH 环境，执行 Node 的 DSH Web 入口。tutor 插件向 DSH 注册业务上下文、工具约束、定时唤醒和消息处理逻辑。

DSH 提供会话、agent、消息队列、模型调用、工具执行、schedule、上下文压缩及命令能力。tutor-plugin 声明注入这些能力，再通过事件钩子协调业务。

### Node 到 Python 的两种调用

| 调用 | 使用场景 | 返回形式 |
| --- | --- | --- |
| 宿主 Python CLI | 状态、轮询、任务领取、资料装配和回执核对 | 单次 JSON 标准输出 |
| MCP stdio | 模型请求业务工具 | 带 JSON-RPC ID 的 MCP 工具结果 |

插件内部的 run(file,args,input) 使用 execFileSync 调用 Python。一般超时为 30 秒，部分发送和人工回复路径为 75 秒，对话上下文路径可到 300 秒。输出缓冲区一般为 1 MiB，工作台及实验快照可到 32 MiB。慢查询和大资料会直接影响该同步调用。

profile 中的 tutor-mcp 使用 python3 启动 adapters/experiment_result_mcp.py，工作目录为 adapters，工具调用超时配置为 150 秒。该适配层装饰业务结果后复用 mcp_server，基础 stdio 循环位于 base_mcp.main。

基础循环支持 initialize、ping、tools/list 和 tools/call。工具调用结果以 JSON 文本放入 MCP content，错误通过受控异常出口处理。

### 工具暴露

工具定义由多个模块聚合，实际可见集合还受配置、任务、Skill 能力声明及宿主退役规则约束。排查模型能力时，需要核对目标工作空间的 tools/list、模型请求工具面和执行 guard。

源码见 [宿主](../app/tutor-plugin.mjs)、[MCP 结果适配](../adapters/experiment_result_mcp.py)、[工具聚合](../adapters/mcp_server.py)、[基础循环](../adapters/base_mcp.py)。

## 启动与工作空间装配

### 路径和状态

业务模块普遍从自身文件位置推导根目录。sql_tools.R 指向项目根，sql_tools.DB 默认指向 runtime/live.sqlite。移动模块或装配工作空间时，需要同时核对路径、数据库、配置和 profile。

| 路径 | 用途 |
| --- | --- |
| runtime/live.sqlite | 当前业务状态、消息、任务和交付记录 |
| runtime/service-session.json | DSH 服务会话及测试轮次、对话绑定 |
| runtime/context-lifecycle.json | 工作材料范围、清理意图和恢复信息 |
| runtime/dsh-home/ | DSH profile 和运行文件 |
| runtime/dependencies/node_modules | 代码直接引用的 DSH 依赖 |
| runtime/fixtures/ | 默认学情夹具及隔离验证资料 |
| config/ | 当前工作空间的功能及访问绑定 |

### 当前启动入口的实际条件

app/start.py 引用以下位置。

~~~text
config/provider.json
runtime/node-v22.21.1-linux-x64/bin/node
app/node_modules/@deepseek-ai/dsh/lib/bin.js
runtime/dsh-home/
runtime/agents-home/
~~~

该入口负责启动已装配的环境。profile 生成、插件装载、模型提供器、业务 MCP、工作台令牌和业务配置需要预先准备。当前根仓库尚未配齐上述独立启动条件。

native_experiments.profile() 展示现有 profile 的组成，包括模型 provider、persona、时间上下文、Skill 根目录、MCP、压缩插件和 tutor 插件。prepare/serve 进一步处理冻结工作空间、独立 HOME、依赖链接、端口、日志和模型代理，当前用于隔离验证及共享装配能力。

模型名通过 profile 参数传入。记忆处理有单独的 preset 和 provider 装配条件。实际模型、窗口、输出预算和推理参数需要与环境配置对应。

源码见 [启动入口](../app/start.py)、[装配实现](../adapters/native_experiments.py)、[配置说明](../config/README.md)。

## 宿主生命周期与主循环

tutor-plugin 中的主控制过程可按以下阶段阅读。

1. 核验 experiment、manual 和 family 绑定状态，确定 governed 工具面及上下文规则。
2. 注册工具约束、业务上下文、服务轮结束钩子和按配置启用的记忆 preset。
3. bootstrap 获取工作空间所有权、恢复必要状态并建立服务会话。
4. 每 5 秒执行 tick，结合忙碌状态、入站、工作流和服务时段选择处理项。
5. 调用 Python 领取或准备事件与任务，向 DSH 投递带 requestId 的消息。
6. agent、工具和会话事件回调记录结果，登记交付，结束或暂缓事项。
7. 任务稳定后清理临时工作材料，保留必要的继续对话信息和持久化证据。

requests、serviceRequests、passiveRequests、memoryRequests 分别关联不同请求的所有者。generation、draining、modelBusy 等状态参与抢占与并发控制。新增处理分支需要覆盖请求登记、完成回调、异常清理和重启恢复。

源码见 [工作空间所有权](../app/workspace-owner.mjs)、[宿主主循环](../app/tutor-plugin.mjs)。

## 被动来信处理

被动来信指家庭主动发来的消息。链路包括入站保存、合并等待、代次绑定、资料读取、模型处理和最终交付。

~~~mermaid
sequenceDiagram
    participant F as 家庭/入站适配
    participant DB as SQLite
    participant H as DSH 宿主
    participant M as 模型
    participant T as 业务工具
    participant C as 渠道
    F->>DB: 保存消息及来源
    H->>DB: 领取事件并绑定 generation
    H->>M: 当前家庭上下文与请求
    M->>T: 查询资料/请求交付或人工核实
    T->>DB: 校验所有者、版本、权限和幂等键
    T->>C: 交付已授权内容
    C-->>DB: 提交结果与后续回执
    H->>DB: 核对结果和处理状态
    H->>M: 清理本轮临时材料
~~~

### 连续消息与 generation

message_hooks 记录入站来源、合并期限、generation 和交付关联。默认安静等待为 60 秒。家庭桥接模式使用距最新消息 5 秒、距本批首条消息最多 15 秒的期限计算。

新的入站会使相应旧 generation 失效。模型沿用本轮 token，刷新和颁发由宿主控制。最终出站授权在 SQLite 事务边界内再次检查，减少旧回复在后续消息之后继续发送的机会。

HTTP 已发出的请求有自身的不可撤回边界。排查竞争条件时，需要同时核对 generation、出站授权和实际渠道记录。

### 对象与事实

家庭联系人、孩子、家长、消息说话者和业务账号有各自字段。称呼、档案、对话引用和服务层级需要保留来源。正式分层缺失时，相关逻辑返回 unknown 并沿当前规则处理。

family_session 负责会话范围绑定，family_lab_bridge 在已配置工作空间中提供家庭桥接。语音和图片理解结果作为派生资料使用，仍需关联当前消息、资产和任务。

源码见 [消息钩子](../adapters/message_hooks.py)、[被动上下文](../adapters/passive_context.py)、[会话绑定](../adapters/family_session.py)、[交付能力](../adapters/delivery_capability.py)。

## 主动服务、事项与排期

主动服务使用明确的服务机会、任务、事项和执行记录。定时唤醒、业务安排和最终交付分别由程序处理。

| 概念 | 内容 | 入口 |
| --- | --- | --- |
| 候选机会 | 触发事实、适用窗口、场景与约束 | proactive 和各 S 类模块 |
| 服务任务 | Skill、due_at、状态、尝试和请求 | agent_plan |
| 持续事项 | 目标、约束、任务关联和时间线 | service_ledger |
| 工作流版本 | revision、Skill 版本、去重键和执行事实 | service_workflow |
| 原生唤醒 | 到期重新检查已有事项 | workflow-schedules.mjs |

### 每日规划与到期处理

workflow-schedules 在 agent-workflow-v1 且时钟未冻结时注册 06:30 内部每日唤醒，再根据已有 planned/deferred 任务同步到期唤醒。内部计划任务有自己的处理范围。

service_workflow 的固定入口覆盖阅读邀请、零完课关怀、周三节奏检查、周五收尾与互动、周末补读、周总结及节点关怀。是否生成事项、是否联系家庭，需要结合当前事实、授权和频控。

agent_plan 的领取及准备逻辑记录 native request，装载业务正文和执行材料。执行前还会复核家庭约束、课次变化、历史交付、当前任务版本和时间条件。

### 状态关系

以下图示用于阅读主要分支，完整状态迁移以函数和回归为准。

~~~mermaid
stateDiagram-v2
    [*] --> planned
    planned --> running: 到期且领取成功
    deferred --> running: 重新具备执行条件
    running --> awaiting_delivery: 已有交付等待结果
    awaiting_delivery --> done: 所需交付均有 sent 依据
    running --> done: 内部提交或交付已核验
    running --> deferred: 暂缓并保留原事项
    running --> blocked: 人工处理或执行问题
    planned --> cancelled: 有效变更事务
    planned --> expired: 超出窗口
    running --> no_contact: 有依据地结束联系
~~~

读取 done 时需要同时检查 completion_scope。内部任务的依据是持久化提交，服务交付的依据是对应交付记录。问题解决、家长满意和学习效果还需要各自的业务证据。

### 修改与立即履行

模型按现有任务、revision、context token 和本轮原话提交安排变更。立即履行已有安排使用 workflow_fulfill，使本次交付与原事项关联。

workflow_read 的 inspect、amend、fulfill 和 refresh 有不同契约。workflow_commit 使用 run_id 与 request_id 保存幂等结果。任务版本、事项和状态投影通过 SQLite 事务及触发器关联。

源码见 [工作流](../adapters/service_workflow.py)、[任务](../adapters/agent_plan.py)、[事项账本](../adapters/service_ledger.py)、[执行投影](../adapters/execution_contract.py)。

## 学情、时间与完课事件

### 当前学情来源

fixture_query.FILE 默认指向 runtime/fixtures/mock_uid_00001_first_month.json。query 根据 as_of 和查询日过滤未来记录，投影课表、完课、缺课和档案。

test_mode.snapshot 要求元数据中已有相应业务时钟状态，再读取夹具、应用完课和家庭桥接覆盖，生成查询结果。开发时需要同时确认数据来源、轮次和 as_of。

夹具日期、实际墙钟和渠道发生时间分别保留。代码使用它们支持历史回放、等待及真实出站校验。service_hours 默认使用 Asia/Shanghai，普通对客时段为 06:30 至 22:00。个别家庭绑定的夜间被动处理有单独条件。

### 课程坐标

公共知识坐标、书内课次及学情提供器编号有不同的映射规则。

- level/week/day 表示全课程坐标。
- book_id/book_lesson_no 表示书内课次，按目录换算。
- 提供器的 book ID 需要正式映射到知识目标。
- execution_contract.context_projection 结合目录补充 curriculum_ref。

### 完课接入

标准字段包括 schema_version、event_id、event_type、uid、enrollment_id、lesson_id、completion_id 和 completed_at。completion_events 对事件、完成编号及本轮课次设有唯一性约束。

事件接收、存储与后续规划分别处理。聊天中的完课自述需要结合其来源和权威事实使用。

工作台机器接入路径 /workbench/api/course-completion 使用 completion-ingress 配置及来源 token。真实接入还需要消息真实性、家庭身份和权威学情适配。

源码见 [夹具读取](../adapters/fixture_query.py)、[时钟与快照](../adapters/test_mode.py)、[完课事件](../adapters/completion_events.py)、[学习事件适配](../app/learning-events.mjs)、[服务时段](../adapters/service_hours.py)。

## Skill、动作与工具权限

### 文件结构

~~~text
skills/<skill-name>/
├── SKILL.md
├── references/                 案例或方法附件
└── runtime-capabilities.json   部分 Skill 的能力声明
~~~

Skill 描述目标、触发条件、所需资料、步骤、表达和完成判据。实际家庭事实、对话和密钥由运行数据或配置提供。

service_catalog.ROWS 定义 S01–S11、I01–I05、H01–H13、Q01–Q07，共 36 个主动作编号，并有补充动作。多个编号可以共用同一方法，例如统一周互动。

动作目录还保存来源、依赖、接入路径和能力缺口。新增动作要同时考虑候选事实、权限、交付、频控、去重及回归。编写要求见 [动作规范](reference/action-authoring.md)。

### 能力校验

| 层级 | 作用 |
| --- | --- |
| 全局工具面 | 模式、配置与宿主 guard 决定模型入口 |
| Skill 和任务能力 | 代码维护的声明及读取回执约束当前可用能力 |
| 业务执行校验 | 家庭、事件、revision、token、媒体来源和回执决定实际操作 |

skill_capabilities 将能力与当前请求及 Skill 阅读记录关联。发布正文后，运行权限仍由上述条件共同判断。

adapters/legacy 中的名称和规则兼容逻辑继续承接已有调用。修改目录时，应同时核对兼容导入和相关回归。

源码见 [能力声明与授权](../adapters/skill_capabilities.py)、[工具面](../adapters/tool_surface.py)、[现代能力路由](../adapters/capability_surface.py)、[接口契约](reference/tool-skill-contracts.md)。

## 主要模型工具

下表介绍现代能力入口。目标环境是否暴露它们，需要以实际配置、任务和工具列表核对。

| 入口 | 作用 | 重要条件 |
| --- | --- | --- |
| skill_read | 读取目录、正文或附件 | 具体版本及装载回执 |
| knowledge_browse/search/read | 查目录、检索候选、读正文 | 登记坐标、文档 ID、来源和审核状态 |
| learning_query | 读取当前学情投影 | 启用状态及夹具/事件覆盖 |
| conversation_read | 查家庭对话 | 参数与范围由适配器校验 |
| evidence_read | 分页读取材料或快照 | 资料种类、版本和引用 |
| workflow_read | 查事项、任务和上下文 | inspect/amend/fulfill/refresh |
| workflow_commit | 提交安排和联系约束变更 | 原话、context token、request_id、revision |
| workflow_fulfill | 立即履行既有事项 | 明确 task_id 及原交付要求 |
| send_message | 请求当前消息或任务交付 | 宿主绑定家庭与事件，沿用本轮 token |
| request_human_confirmation | 将当前问题或任务交人工 | 宿主绑定目标，模型提供原因和分类 |
| memory_read、preference_commit | 读记忆或提交允许的即时控制 | 配置、主体、原话与并发令牌 |

精确课程资料读取的参数形态如下。执行条件包括当前工具暴露和对应资料可用。

~~~json
{
  "target": {"level": "L2", "week": 4, "day": 5},
  "parts": ["reading_qa", "course"]
}
~~~

返回结果应逐项核对 status、版本、引用及缺失部分。修改工具时，还需检查路由、错误、宿主自动步骤、能力声明和旧名称转接。参数定义见 [query_contract.py](../adapters/query_contract.py)。

## 知识与媒体

### 公共知识组织

课程目录位于 knowledge/curriculum/L2/catalog.json，业务知识还使用 adapters 下的 catalog。设计资料记录 L2 48 周、每周 5 课，引读问答覆盖 240 课，课程正文、导读及周总结资料集中在前 4 周。

实际覆盖、审核状态和版本需要结合当前登记文件与 OpenViking 正文读取核对。

### 查询流程

1. 明确课程坐标、资料类型或业务范围。
2. 通过正式目录定位书目、周、课次或文档。
3. 检索返回候选与引用。
4. 读取登记版本的正文，检查其是否支持答案。
5. 保存材料引用，按预算装载正文或分页读取。

knowledge_search 使用外部 OpenViking CLI。当前 CLI 路径在源码中指定，凭据通过 TUTOR_KNOWLEDGE_CONFIG_FILE 或约定配置提供。故障被转换为受控 unavailable 结果或异常。

query_contract 统一现代知识入口，data_readers 控制整体正文装载和材料引用。复用已有正文需要核对版本与当前问题。

### 媒体原件

assets/reading-guides 和 assets/weekly-review 保存实际文件，adapters 清单将素材关联到课次或周。S01、周总结、高光、节日图片和表情各有来源、授权和交付逻辑。

vendor 中保留的 ark_media 是独立媒体适配器。语音理解与图片输入分别由 voice_understanding 和 image_inputs 处理。派生内容需要与原始消息及业务事实一起核对。

源码见 [课程知识](../ops/curriculum_knowledge.py)、[知识查询](../adapters/knowledge_search.py)、[材料缓存](../adapters/material_cache.py)、[导读交付](../adapters/s01_delivery.py)、[周总结材料](../adapters/weekly_materials.py)。

## 交付、回执与最后发送边界

delivery_capability 根据活动任务或唯一被动事件，将统一发送参数转给已有业务分支。S01 日期与导读单由宿主绑定，主动服务沿用当前候选及执行 token，节日图片还需要对应 asset。

随后由业务层、消息 generation、渠道绑定和 outbox 共同完成授权与交付。

| 状态 | 含义和处理依据 |
| --- | --- |
| queued | 本地待处理 |
| accepted | 渠道已受理，后续结果仍需核对 |
| sent | 已有发送成功依据 |
| unknown | 结果未明确，查询原操作及后续回执 |
| failed | 结合具体操作和错误分类处理 |
| partial | 组合内容的分段结果尚未完整成功 |
| superseded | 被后续有效操作替代，投影按程序规则排除 |

execution_contract.delivery_task_status 将持久化交付投影为任务结果。所需交付均为 sent 时形成交付完成依据，排队和受理继续等待，需要核实的组合进入相应阻塞状态。

业务层用 action_key、operation_id、request_id 和 generation token 关联不同层级。最后发送前复核当前家庭、revision、原话、暂停、generation 及已有出站记录。

SQLite 事务和外部 HTTP 分属不同边界。超时或 unknown 后，应先查看原 outbox、请求编号、回调和对账结果，再按状态机继续。

### 正文与气泡校验

message_text 当前规定每条文字最多 50 字，单次文字、图片和表情合计最多 3 个气泡。多条文字按实际换行拆分，校验还检查完整语义及换行编码。旧测试样例超过当前限制时，应先核对样例适用性。

内部 generation 和 execution token 通过独立参数传递。正文及用户可见消息由执行层进行相应校验。实现见 [message_text.py](../adapters/message_text.py) 和 [execution_contract.py](../adapters/execution_contract.py)。

host_receipt 仅登记能关联到当前任务、request 和实际工具审计的结果。service-turn-completion 监听工具结果和请求所有权，再判断服务轮是否能结束。

源码见 [统一交付](../adapters/delivery_capability.py)、[渠道实现](../adapters/wechat_test.py)、[状态投影](../adapters/execution_contract.py)、[宿主回执](../adapters/host_receipt.py)、[服务轮结束](../app/service-turn-completion.mjs)。

## 人工核实与接管

request_human_confirmation 提交当前问题或任务。handoff_capability 根据当前活动任务或唯一被动事件绑定目标，校验模型提交的字段。

operator_alerts 保存待办、原因、来源消息、可选草稿、revision 和交付关联。当前分类包括 needs_confirmation、missing_information、unsupported_request、delivery_problem 和 other。

人工回复沿已有渠道与交付记录执行。草稿供人工审核，实际发送与处理状态分别记录。历史归档、重复提交、并发修改和已受理交付都有相应保护。

家庭桥接模式下，experiment_result_mcp 将请求关联到工作台 case 并执行接管控制。恢复 AI 由对应显式操作决定。

源码见 [转交绑定](../adapters/handoff_capability.py)、[人工账本](../adapters/operator_alerts.py)、[家庭桥接](../adapters/family_lab_bridge.py)。

## 记忆与上下文生命周期

### 三类内容

| 内容 | 位置 | 生命周期 |
| --- | --- | --- |
| 原始消息、工具和交付证据 | SQLite 及运行审计 | 按业务保留与归档 |
| 家庭偏好及陈述性记忆 | preference_memory 等表 | 按主体、证据、有效期及遗忘要求维护 |
| 模型当前工作材料 | DSH surface、working context、材料引用 | 按任务装配、压缩和清理 |

### 家庭记忆

preference-memory 启用后，即时入口处理明确遗忘及受允许的媒体操作控制。普通偏好和陈述性材料由独立夜间记忆流程处理。

memory_protocol 定义 context、evidence read、prepare 和 commit。prepare 校验完整批次，commit 按 prepared_token 原子应用并完成内部任务。模型结束信号需要与持久化记录对应。

记忆保存主体、来源、证据、适用范围、有效期和修订关系。新来信可以中断记忆处理，家庭对话拥有相应优先级。

### 模型工作材料

working_context 根据预算生成任务、事项和历史材料的工作集合，活动义务、失败和未确认交付有专门保留逻辑。

working-surface 记录可见顺序、任务边界及压缩事件。清理前检查任务结算和工具调用配对，先保存意图，再替换临时材料。重启后可沿记录继续。

request-budget 使用校准估计控制请求预算，默认窗口为 131072，另预留输出、安全余量和图像预算。实际模型能力还需要与 provider 对应。

源码见 [记忆协议](../adapters/memory_protocol.py)、[偏好记忆](../adapters/preference_memory.py)、[记忆会话](../adapters/memory_session.py)、[模型工作集合](../adapters/working_context.py)、[材料清理](../app/working-surface.mjs)、[请求预算](../app/request-budget.mjs)。

## SQLite 数据模型与建表位置

schema.sql 保存基础表。任务、记忆、交付和工作台模块会在各自 initialize 中建表、加字段或创建索引。维护数据库应同时搜索 CREATE TABLE、ALTER TABLE、CREATE TRIGGER 和 initialize。

| 数据组 | 代表性表 | 用途 |
| --- | --- | --- |
| 家庭与课程 | family、profiles、lessons、metadata | 家庭、课次和模式元数据 |
| 原话与入站 | messages、events、source_seen、source_checkpoints | 去重、游标、事件 |
| 基础交付 | deliveries、media_assets | 交付与素材关联 |
| 任务与计划 | service_tasks、service_plan_versions、service_links、service_skill_reads | 任务、计划、交付和正文读取 |
| 持续事项 | service_cases、service_case_tasks、service_case_timeline、service_case_constraints | 目标、任务和变化历史 |
| 工作流 | service_task_versions、service_workflow_contexts、service_workflow_commits、service_send_authorizations | revision、事务、幂等和授权 |
| 消息代次 | message_hook_inbound、message_hook_generations、message_hook_deliveries | 合并消息及发送边界 |
| 分段渠道 | test_outbox、test_outbox_parts、test_callbacks、test_delivery_bundles | 操作、分段和回调 |
| 完课事实 | completion_events | 标准事件与唯一约束 |
| 审计 | tool_audit、business_audit | 来源、输入输出、时间和状态 |
| 记忆与偏好 | memory_journal、memory_summaries、memory_prepared、preference_versions、preference_tokens | 证据、记忆变更与并发 |
| 材料与上下文 | context_materials、passive_materials、passive_material_heads、model_context_archive | 分页、缓存和版本 |
| 人工处理 | operator_alerts | 转交、草稿、修订及人工交付 |
| Skill 工作台 | versions、publications、assemblies、jobs、users | 文本版本、组合、权限和测试任务 |

可选共享控制库由 family_lab 管理，包含 runs、families、bindings、ingress、outbox、receipts、cases、generations 等表。查询时先确认目标是单家庭业务库还是共享控制库。

### 关键关联字段

| 字段 | 排障用途 |
| --- | --- |
| uid、family_id | 业务家庭和桥接家庭 |
| run_id | 业务轮次 |
| sessionId、conversation_id | DSH 会话和业务上下文 |
| event_id、source_id | 当前事件和原始消息 |
| task_id、case_id | 服务任务和持续事项 |
| native_request_id、request_id | 请求所有权和幂等请求 |
| revision、context_token | 事务和上下文版本 |
| generation_token、execution_token | 消息代次和执行授权 |
| action_key、operation_id | 业务交付和渠道操作 |
| document_id、material_id、asset_id | 正文、材料和媒体原件 |

关联字段分别出现在工具参数、数据库及审计中。定位问题时，需要沿同一家庭、轮次和操作链追踪。

源码见 [基础表](../adapters/schema.sql)、[任务表](../adapters/agent_plan.py)、[事项表](../adapters/service_ledger.py)、[工作流表](../adapters/service_workflow.py)。

## 配置与环境变量

### 主要配置

| 文件 | 作用 |
| --- | --- |
| config/provider.json | 启动入口读取 api_key，完整模型配置还由 DSH profile 提供 |
| config/service-workflow.json | 工作流启用条件 |
| config/tool-surface.json | 身份、知识、记忆、交付等工具面开关 |
| config/message-hooks.json | 入站合并与 generation |
| config/preference-memory.json | 记忆治理、慢循环和家庭绑定 |
| config/workbench.json | 工作台访问 token |
| config/completion-ingress.json | 机器完课接入来源及 token |
| config/completion-planning.json | 完课后规划 |
| config/highlight-events.json | 外部高光事件 |
| config/test-wechat.json | 允许名单渠道、路由及身份 |
| config/family-lab.json | 家庭工作区与共享控制库绑定 |
| config/manual-workbench.json | 手动工作区身份和授权 |
| config/experiment.json | 冻结实验的文件及运行绑定 |
| config/emoji-assets.json | 表情版本和使用条件 |
| config/service-lab-media.json、festival-media.json | 媒体提供器及节日素材 |
| config/student-tier.json | 正式服务分层的读取条件 |

各配置中的 enabled、版本、路径、家庭、作用域和哈希有具体读取规则。新增样例时，应依据相应读取函数编写并在隔离环境验证。

### 主要环境变量

| 变量 | 用途 |
| --- | --- |
| DSH_HOME、DSH_AGENTS_HOME | profile 和 agent 运行文件 |
| DSH_PERMISSION_MODE | 工作空间权限模式 |
| TUTOR_API_KEY | 服务模型环境密钥 |
| TUTOR_MEMORY_API_KEY | 记忆 provider 环境密钥 |
| TUTOR_NATIVE_PORT | DSH Web 端口，启动入口默认 19031 |
| TUTOR_WORKBENCH_PORT | tutor 本地 HTTP 端口，默认 19032 |
| TUTOR_KNOWLEDGE_CONFIG_FILE | OpenViking CLI 凭据配置 |
| TUTOR_ROOT | 部分工作台及适配器的显式根路径 |
| TUTOR_EXPERIMENT_* | 实验身份、profile 和 manifest 校验 |
| TUTOR_MEDIA_BINDING_SHA256、TUTOR_VOICE_BINDING_SHA256 | 媒体及语音绑定 |

真实 API key、工作台 token、联系人路由和私有回调地址通过环境及私有配置维护。文档和公开样例只描述必要结构。配置说明见 [config/README.md](../config/README.md)。

OpenViking 的业务访问使用 API key 配置，不应把业务密钥、服务管理密钥和网页登录密码混为一项。公开配置示例及当前入口的读取方式见 [部署配置与凭据边界](reference/deployment-configuration.md)。业务中的 context_token、generation_token、execution_token 等字段名属于协议说明；文档只保留字段含义，不记录实际运行值。

## HTTP 接口与工作台

tutor 插件的本地服务绑定 127.0.0.1，公开代理范围和认证由部署层明确设置。

| 路径 | 用途 | 读取依据 |
| --- | --- | --- |
| GET /health | 状态、会话、轮询及业务投影 | tutor-plugin |
| /workbench/ | 当前操作页面及静态资源 | workbench-route |
| /workbench/api/state | 当前状态投影 | 工作台 bearer token |
| /workbench/api/course-completion | 标准完课来源接入 | 来源 ID 和 completion-ingress token |
| /experiment/v1/* | 冻结实验内部控制 | experiment 配置、token 及运行身份 |
| /run、/test-clock、/schedule 等 | 保留的内部操作分支 | 具体模式和宿主分支限制 |

工作台提供当前状态、历史资料或预览。preview 用模拟时间读取资料，实际业务时钟和学习事实由各自控制入口管理。

教师/Skill 工作台还有自己的 handler、身份体系及状态库。维护时需要分别核对它与业务宿主 HTTP 的路由、token 和作用域。

P0 专用路由及归档演示启动器已移出主代码。当前接口见 [工作台路由](../app/workbench-route.mjs)、[教师工作台](../adapters/teacher_workbench.py)、[演示边界](DEMO-CLEANUP.md)。

## 测试方法与验证口径

### 环境检查

在选定的工作空间中，先核对解释器和依赖声明。NODE_BINARY 指向部署者选定的绝对 Node 路径。

~~~bash
python3 --version
"$NODE_BINARY" --version
python3 -c "import json; print(json.load(open('app/package.json'))['dependencies'])"
~~~

以下检查读取夹具及期望值，输出两个文件各自是否匹配。根据结果为隔离副本选择输入。

~~~python
import hashlib
import json
from pathlib import Path

root = Path.cwd()
name = "mock_uid_00001_first_month.json"
expected = json.loads((root / "app/test-fixture-sha256.json").read_text())[name]
for folder in ("runtime/fixtures", "runtime/fixtures/regression-approved"):
    path = root / folder / name
    matched = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected
    print(folder, matched)
~~~

### 测试组织

Python 测试主要位于 adapters/test_*.py，Node 测试位于 app/test_*.mjs 及 adapters/test_*.mjs。测试与实现相邻，保留既有导入和发现方式。

test_mode、test_scheduler 和 wechat_test 同时是实际业务适配模块。整理带 test 字样的文件时，需要核对职责及调用者。

### 完整离线入口

在配置完成、数据隔离且已准备匹配夹具的副本根目录执行。

~~~bash
python3 -B app/run-tests.py
~~~

入口先核对夹具哈希，再执行 Python unittest 和 Node test runner。日志位置为：

~~~text
runtime/test-results/python.log
runtime/test-results/node.log
~~~

基准合成夹具的 SHA-256 为：

~~~text
270839199ce69868873ced71ac86dfe31ea7ca075a535f98740940d9466c5f1c
~~~

现有原夹具与该基准不同，原文件已保留。匹配文件另存于 runtime/fixtures/regression-approved/。测试副本应把选定的合成夹具放到入口要求的位置，继续使用 app/test-fixture-sha256.json 核验。

### 隔离条件

1. 基于实际工作树构建副本，明确包含哪些未提交版本。
2. 使用独立 SQLite、config、HOME、DSH_HOME、缓存和端口。
3. 放入匹配的合成夹具及测试确实需要的固定规则文件。
4. 显式指定 Node 和版本一致的 DSH 依赖。
5. 使用模拟出口与受控替身。
6. 保存退出码、完整日志、源码指纹和输入版本。

仅按当前 HEAD 克隆会遗漏工作区修改。本轮验证在隔离副本中建立测试用 Git 快照，实际项目提交和暂存条目保留。

### 最近验证结果

| 批次 | 结果与覆盖 |
| --- | --- |
| 首轮 Linux 完整 Python 回归 | 1108 项，原始报告有 10 个失败和 12 个错误。副本补齐两份归档规则后对应两项通过，其余 20 项在整理前版本复现 |
| 首轮 Linux Node 回归 | 71 项，66 通过，5 失败，相应失败在整理前复现 |
| 演示整理的 Python 定向对照 | 整理前 183 项、3 个错误。整理后 182 项、181 通过、1 个既有 Pillow 缺失错误 |
| 演示整理的 Node 对照 | 整理前 71 项、66 通过、5 失败。整理后 70 项、66 通过、4 个既有计划展示失败 |
| 当前语法与目录核验 | 242 个 Python 文件、28 个 JavaScript 文件通过语法检查，保留 121 个测试文件和 49 个 Skill |

完整回归与后续定向对照的覆盖不同。当前结果仍包含依赖缺失、旧断言及需要核对的行为差异。

证据见 [首轮 Linux 整理记录](../legacy/cleanup-20261112/README.md)、[演示代码整理](DEMO-CLEANUP.md) 及相应 verification 目录。

## 排障与维护索引

| 现象或任务 | 优先检查 | 入口 |
| --- | --- | --- |
| 启动失败 | Node、DSH、provider、profile、根路径 | app/start、native_experiments、config |
| 不处理消息 | bootstrap、lease、lastError、lastPoll | tutor-plugin、workspace-owner |
| 来信未回复 | 去重、quiet deadline、generation、时段及人工接管 | message_hooks、live_runtime、service_hours、operator_alerts |
| 服务未到期执行 | run、状态、due_at、not_before_wall、暂停及版本 | agent_plan、service_workflow、service_ledger |
| 工具暴露异常 | 配置、Skill grants、retired 集合 | tool_surface、capability_surface、skill_capabilities |
| 安排修改未生效 | commit 回执、request_id、context token、revision | service_workflow、tool_audit |
| accepted/unknown 后有重发风险 | 原 action_key、operation_id、分段及回调 | wechat_test、service_send_authorizations |
| 课程误定位 | 坐标、书内编号、正式 ID 和正文 | query_contract、curriculum_knowledge、catalog |
| 知识读取失败 | CLI、凭据、版本及外部服务 | knowledge_search、curriculum_knowledge |
| 语音或图片失败 | 输入资产、事件、授权和依赖 | voice_understanding、image_inputs、ark_media |
| 转人工无后续 | 待办 revision、case、接管和人工交付 | handoff_capability、operator_alerts、family_lab_bridge |
| 上下文增长 | 工作集合、分页、配对、压缩和结束登记 | working_context、working-surface、context_lifecycle |
| 记忆未生效 | 配置、家庭、证据水位、prepare/commit | preference_memory、memory_maintenance、memory_session |

沿同一 uid、run_id、event_id、task_id、request_id 和 action_key 追踪，能够减少跨轮次拼接造成的误判。

observability.invoke 保存调用 origin、输入输出、业务时间、墙钟、状态和耗时，并清洗常见密钥字段。provider 异常通常只对外保留类型，受控业务校验可以附带业务原因。根因需要在对应环境内部核查。

源码见 [审计实现](../adapters/observability.py)、[工具错误出口](../adapters/base_mcp.py)、[执行约束](../adapters/execution_contract.py)。

## 当前保留与历史归档

以下名称仍带 lab、manual 或 experiment，但当前调用关系要求继续保留。

| 组别 | 当前关联 |
| --- | --- |
| family_lab、family_lab_bridge 及控制模块 | 家庭隔离、ModelSlot、回执、联系人和人工操作 |
| manual_workbench、manual_media | 工作空间校验、渠道约束和媒体发布 |
| experiment_runtime 等 | 冻结身份、宿主接口、资料和原生验证 |
| native_experiments | profile、冻结文件、独立运行及监督 |
| reply_lab、teacher_workbench、skill_studio | 认证、版本、组合、场景、测试及现有 API |
| reply_lab_presets | S07/S08 等有效业务回归的合成资料 |
| adapters/legacy | 名称、规则和旧材料兼容 |

独立演示启动器、固定旧报告、P0 验收入口、旧 UI 测试及方法原文收录于 legacy/demo-code-cleanup-20261112。其他旧实现和变更前备份继续保存在根 legacy。

恢复历史材料时，应核对版本、绝对路径、依赖、数据及当时模式。当前行为与数据位置由现有代码和配置决定。归档清单见 [演示整理记录](DEMO-CLEANUP.md)。

## 已知缺口

| 项目 | 当前证据 | 接手核对事项 |
| --- | --- | --- |
| 独立启动环境 | Node、DSH 和 provider 未配齐 | 明确装配方式、路径及版本 |
| Python 依赖 | 统一声明缺失，Pillow 影响回归 | 根据保留功能和导入建立清单 |
| 生产学情 | 默认夹具、覆盖层及标准事件合同已存在 | 接入权威提供器、身份映射和认证 |
| 知识可复现 | CLI 和配置引用外部路径 | 明确安装、版本及只读凭据 |
| 回归有失败 | 完整及定向验证有记录 | 区分有效断言、依赖缺失、过时假设和缺陷 |
| 能力接入不足 | 动作目录保留 gap | 按具体动作补齐接口和业务验收 |
| 历史归档较多 | 多轮备份并存 | 维护索引和恢复条件 |
| 工作树待提交 | 原开发与整理修改共存 | 保留审阅边界，形成可追溯提交 |

本文的源码核对和隔离结果对应各自范围。真实渠道、真实家庭、业务审核和上线接受情况，需要使用相应运行与人工证据确认。

动作目录中列出的具体接入缺口还包括成长视频生成与投递、微信语音投递、部分升级工单及问题闭环统计。generate_audio 已有能力入口，语音交付仍需要对应渠道实现。FAQ 返回内容的审核状态需要纳入答复判断。上述范围见 [动作目录](../adapters/service_catalog.py) 和 [能力登记](../adapters/skill_capabilities.py)。

## 常见扩展工作的检查清单

### 调整现有 Skill

1. 找到 service_catalog 中对应动作和实际 Skill 名称。
2. 核对当前 SKILL.md、references 及运行能力声明。
3. 明确改动影响触发、事实要求、表达还是交付完成条件。
4. 核对工作空间中的发布指针和实际读取版本。
5. 选择覆盖该业务的回归，检查正文版本失效与重新核验路径。

### 增加工具或能力

1. 在受控业务模块实现参数和对象校验。
2. 定义输入 schema、结果状态和受控异常。
3. 接入 mcp_server 及需要的现代能力路由。
4. 核对全局退役、Skill 授权、内部任务限制和执行 guard。
5. 覆盖直接调用、模型工具调用、重试及并发状态。
6. 更新工具契约和对应 Skill，检查已有调用者。

### 接入真实学情或学习事件

1. 定义真实账号到当前家庭、班期和课次的映射。
2. 明确权威数据来源、发生时间、获取时间和可见范围。
3. 将提供器课次映射到课程目录的正式坐标。
4. 按标准事件合同处理来源认证、幂等及顺序。
5. 校验重复事件、迟到事件、切轮和资料更新时的表现。
6. 在模拟出口核对主动服务，再按业务授权验证真实渠道。

### 修改数据库或渠道

数据库改动需要定位实际 initialize、字段迁移、索引及触发器，覆盖既有数据库和中途失败恢复。渠道改动需要覆盖分段、受理、成功、未知、失败、回调重复及迟到，并保持原操作关联。

这些检查应结合现有实现和实际变更范围执行。开发验证材料应记录输入版本、代码版本、配置范围、出口和实际回执。

## 接手阅读与修改顺序

建议第一次阅读按以下顺序进行。

1. 根 README 和 DIRECTORIES，建立目录与环境对应关系。
2. tutor-plugin，理解启动、轮询、请求所有权和回调。
3. mcp_server、tool_surface、capability_surface，理解模型能力。
4. agent_plan、service_workflow、execution_contract，理解状态与完成判据。
5. message_hooks、wechat_test、host_receipt，理解入站和实际交付。
6. service_catalog、相关 Skill、query_contract 和课程目录，理解方法和事实来源。
7. preference_memory、memory_session、working-surface，理解持续服务与上下文。
8. 对应回归测试与 verification 日志。

修改单项业务时，沿触发事实、Skill、工具、权限、事务、交付、回执、上下文和回归逐项检查。修改公共组件时，需要覆盖其他任务、工作空间模式及既有数据状态。

## 源码与验证依据

本文主要依据包括：

- [宿主](../app/tutor-plugin.mjs) 与 [原生装配](../adapters/native_experiments.py)。
- [MCP 入口](../adapters/mcp_server.py) 与 [工具契约](reference/tool-skill-contracts.md)。
- [任务](../adapters/agent_plan.py)、[工作流](../adapters/service_workflow.py)、[事项](../adapters/service_ledger.py)、[状态投影](../adapters/execution_contract.py)。
- [消息代次](../adapters/message_hooks.py)、[渠道](../adapters/wechat_test.py)、[回执登记](../adapters/host_receipt.py)。
- [学情](../adapters/fixture_query.py)、[完课](../adapters/completion_events.py)、[知识](../adapters/query_contract.py)。
- [记忆协议](../adapters/memory_protocol.py)、[材料清理](../app/working-surface.mjs)、[审计](../adapters/observability.py)。
- legacy/cleanup-20261112/verification 中的完整回归、整理前对照和文件保全记录。
- legacy/demo-code-cleanup-20261112/verification 中的受影响回归、归档映射和暂存条目核验。

## Skill 文件索引

以下按当前 49 个目录列出正文入口。正文标题保留原文，具体能力和可用性还需核对当前任务及配置。

| 目录 | 正文标题 |
| --- | --- |
| [address-learning-concern](../skills/address-learning-concern/SKILL.md) | Q03 效果质疑应对 |
| [answer-family-question](../skills/answer-family-question/SKILL.md) | 接住家庭当前的问题 |
| [answer-faq](../skills/answer-faq/SKILL.md) | Q01 FAQ自动应答 |
| [archive-question-outcome](../skills/archive-question-outcome/SKILL.md) | Q07 对话回流沉淀 |
| [care-calendar-day](../skills/care-calendar-day/SKILL.md) | I05 节日与考试关怀 |
| [celebrate-important-day](../skills/celebrate-important-day/SKILL.md) | I05/H10 生日祝福 |
| [celebrate-learning-streak](../skills/celebrate-learning-streak/SKILL.md) | S04 连续学习鼓励 |
| [celebrate-lesson-completion](../skills/celebrate-lesson-completion/SKILL.md) | S02 完课表扬 |
| [close-week-reading-gap](../skills/close-week-reading-gap/SKILL.md) | S08 本周最后一课 |
| [escalate-unanswered-question](../skills/escalate-unanswered-question/SKILL.md) | Q05 必要资料缺失转人工 |
| [explain-reading-content](../skills/explain-reading-content/SKILL.md) | Q02 阅读内容答疑 |
| [generate-reading-audio](../skills/generate-reading-audio/SKILL.md) | generate-reading-audio |
| [handle-refund-complaint](../skills/handle-refund-complaint/SKILL.md) | Q04 退费与投诉处理 |
| [highlight-book-completion](../skills/highlight-book-completion/SKILL.md) | H04 首次点亮书架一本书 |
| [highlight-course-start](../skills/highlight-course-start/SKILL.md) | H01 首日完课高光 |
| [highlight-expressive-answer](../skills/highlight-expressive-answer/SKILL.md) | H08 每周 AI 表达精彩作答 |
| [highlight-first-perfect-answer](../skills/highlight-first-perfect-answer/SKILL.md) | H02 首次课中题全对 |
| [highlight-growth-video](../skills/highlight-growth-video/SKILL.md) | H13 精剪高光视频 |
| [highlight-quiz-ranking](../skills/highlight-quiz-ranking/SKILL.md) | H07 周度课中题正确率冠军 |
| [highlight-ranking-progress](../skills/highlight-ranking-progress/SKILL.md) | H09 排行榜晋级 |
| [highlight-reading-milestone](../skills/highlight-reading-milestone/SKILL.md) | H06 21 天习惯小达人 |
| [highlight-second-week-attendance](../skills/highlight-second-week-attendance/SKILL.md) | H05 第二周全勤 |
| [highlight-seven-day-completion](../skills/highlight-seven-day-completion/SKILL.md) | H03 7日完课成就 |
| [inspect-family-image](../skills/inspect-family-image/SKILL.md) | inspect-family-image |
| [invite-reading-interaction](../skills/invite-reading-interaction/SKILL.md) | 周互动 |
| [manage-service-arrangements](../skills/manage-service-arrangements/SKILL.md) | 家长修改服务安排 |
| [plan-after-lesson-completion](../skills/plan-after-lesson-completion/SKILL.md) | 完课后的服务规划 |
| [plan-service-day](../skills/plan-service-day/SKILL.md) | 当日服务规划 |
| [plan-today](../skills/plan-today/SKILL.md) | 为今天安排必要的服务 |
| [plan-weekend-makeup](../skills/plan-weekend-makeup/SKILL.md) | S09 周末补读安排 |
| [prepare-growth-review-package](../skills/prepare-growth-review-package/SKILL.md) | H12 续费证据包（一封信） |
| [prevent-lesson-skipping](../skills/prevent-lesson-skipping/SKILL.md) | S06 阅读衔接邀请 |
| [proactive-service](../skills/proactive-service/SKILL.md) | 根据明确信号提供主动服务 |
| [rebuild-reading-rhythm](../skills/rebuild-reading-rhythm/SKILL.md) | S07 习惯拉升 |
| [remember-family](../skills/remember-family/SKILL.md) | 保存后续服务需要的事实 |
| [remind-historical-makeup](../skills/remind-historical-makeup/SKILL.md) | S03 补读邀请 |
| [remind-reading](../skills/remind-reading/SKILL.md) | S01 阅读邀请 |
| [reply-style](../skills/reply-style/SKILL.md) | 回复表达 |
| [respond-child-sharing](../skills/respond-child-sharing/SKILL.md) | Q06 孩子分享与求助 |
| [review-context](../skills/review-context/SKILL.md) | review-context |
| [review-learning-progress](../skills/review-learning-progress/SKILL.md) | H11 月度学情反馈 |
| [review-service-case](../skills/review-service-case/SKILL.md) | 持续事项的内部检查 |
| [send-festival-wishes](../skills/send-festival-wishes/SKILL.md) | 节日互动 |
| [student-service-tier](../skills/student-service-tier/SKILL.md) | 用正式分层事实选择已定义的服务 |
| [supervise-learning](../skills/supervise-learning/SKILL.md) | 督学动作路由 |
| [support-zero-completion](../skills/support-zero-completion/SKILL.md) | S05 阅读节奏关怀 |
| [tutor-main](../skills/tutor-main/SKILL.md) | 服务目标 |
| [weekly-learning-review](../skills/weekly-learning-review/SKILL.md) | S10 本周总结 |
| [welcome-back-to-reading](../skills/welcome-back-to-reading/SKILL.md) | S11 恢复阅读欢迎 |
