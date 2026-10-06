# Hermes 原生能力核对（第二、三阶段实现前）

日期：2026-10-03。只读核对服务器 `/root/autodl-tmp/digitalTutor/vendor/hermes`，提交 `f97608f178d1ffeca59860195ab7da295f7c8e5f`。最后一次 `git status --porcelain` 输出为空。本次未修改远端源码或配置、未重启服务、未读取凭据文件、未执行真实模型调用。

本文的源码位置均相对此固定提交；以下“已观察”只表示源码合同已核对，不表示集成验收已通过。

## 结论

Hermes 已有本项目需要的模型循环、工具注册及过滤、Skill 目录和读取、cron 唤醒、取消及记录能力。应保留第三方源码，业务数据库继续作为家庭权限、事项状态、发送操作与幂等的权威来源。外围小模块完成适配，不另写模型工具循环或通用 cron 引擎。

建议边界：原生 cron → 受控脚本 → 业务 tick/工作领取 → 独立进程中原生 AIAgent → 项目工具注册桥 → 业务服务。Hermes 的会话锁不能替代业务工作租约；模型取消不能替代发送前租约校验。

## 已观察到的原生能力

| 能力 | 明确源码位置 | 观察结果与使用方式 |
| --- | --- | --- |
| 原生 Agent | `run_agent.py:261–306` | 构造参数包括 `enabled_toolsets`、`disabled_toolsets`、`session_id`、`cwd`、`max_iterations`、`run_budget_seconds`、`skip_context_files`、`skip_memory`、`skip_background_review`、`session_db`，以及 event/tool-start/tool-complete 回调。使用有限运行预算和可信宿主提供的角色/会话配置。 |
| 原生循环 | `agent/turn_facade.py:22–89`；`agent/conversation_loop.py:1605` | `run_conversation(user_message, system_message, conversation_history, task_id, ...)`；原生 facade 包含持久会话 turn lease。返回结构含 `final_response`、`messages`、`api_calls`，不完整运行有 `completed=False`、`partial`、`error` 等字段。最终文本不应自动成为微信发送。 |
| 插件扩展 | `hermes_cli/plugins.py:1–8,456–496` | 用户插件通过 `register(ctx)` 和 `ctx.register_tool(...)` 注册工具；无需修改核心。独立 bootstrap 也可调用 `tools.registry.registry.register`（`tools/registry.py:662`）。优先选一种方式，不双重登记。 |
| 工具选择 | `model_tools.py:283–342` | `enabled_toolsets=None` 表示全部；显式列表再减去 disabled。平台 bundle 的 disabled 有保留 core 的特殊语义，不能简单把 `hermes-cli` 当拒绝全部。运行器应检查最终名称集合是否等于批准集合。 |
| 工具执行权限检查 | `agent/turn_tool_round.py:95`；`agent/tool_executor.py:390–432` | 普通模型调用检查 `agent.valid_tool_names`；`tool_call` 延迟桥另检查本会话原始工具集。仍需业务处理器核验绑定 token、角色、家庭和版本；不要把隐藏 schema 当唯一授权边界。 |
| 只读 Skill 基础能力 | `tools/skills_tool.py:234,573–640`；`agent/skill_utils.py:359–386,420` | `skills_list` 返回目录；`skill_view(name,file_path,task_id,preprocess=True)` 读取正文及同包文件，支持外部目录 `skills.external_dirs`。原生有平台/禁用检查和名称碰撞拒绝。 |
| Profile 隔离 | `hermes_constants.py:112–120`；`tools/skills_tool.py:63–71` | `get_hermes_home` 顺序为 context override、`HERMES_HOME`、默认路径，Skill 根也按当前 profile 解析。用独立进程启动并固定 `HERMES_HOME`，避免多个家庭在同进程中动态替换环境变量。 |
| 原生 cron | `cron/jobs.py:1732–1765`；`cron/scheduler_tick.py:7–22` | `create_job` 支持 `no_agent=True`、`script`、`workdir`、`deliver`、`failure_deliver`；默认无 origin 则 local。`tick` 有文件锁和 due dispatch。项目只登记固定后台唤醒，不让服务模型新建 cron。 |
| 脚本模式 | `cron/scheduler.py:1483–1535`；`cron/scheduler_script.py:257–322` | no-agent 脚本不初始化 AIAgent。stdout 可能作为交付内容；空输出或 `wakeAgent=false` 为静默。脚本必须真实位于当前 `HERMES_HOME/scripts/` 内，绝对路径及 symlink 越界均拒绝；`.sh` 用 bash，其他用 Hermes 解释器，不能依赖 shebang 切换 Python。 |
| 取消 | `agent/interrupt_control.py:109`；`cron/scheduler_script.py:172–218` | `agent.interrupt(hard_cancel=True,...)` 可由另一线程请求中止；cron script 有进程树终止和管道有界回收。取消已发生的外部副作用不可能由此自动撤回。 |
| cron 运行记录 | `cron/executions.py`；`cron/jobs.py:3281–3295` | 原生有 execution 状态与输出文件，输出写入采用原子操作、0600 权限和数量清理。业务 job/result/op 仍需关联同一个 run ID，不能以 cron success 代替业务完成。 |
| 会话/轨迹记录 | `agent/session_persistence.py:258–273,388–439`；`agent/trajectory.py:37–56` | 原生 SessionDB 可持久消息；trajectory 默认相对 cwd 写 `trajectory_samples.jsonl` 或 `failed_trajectories.jsonl`，有追加锁。仅设置 `HERMES_HOME` 不等于 trajectory 目录隔离，必须同时固定 cwd 或关闭默认轨迹并由宿主保存运行结果。 |

## Skill 接入需要明确的两个细节

1. 不直接开放整个 `skills` toolset：`tools/skill_manager_tool.py:927` 的 `skill_manage` 与 `skills_list/skill_view` 同组，整体开放会允许修改技能。项目服务角色只需读取。
2. `skill_view` 默认预处理。`tools/skills_tool_plugin.py:113–120` 调用 `agent.skill_preprocessing.preprocess_skill_content`；后者 `:99–112` 在配置 `inline_shell=true` 时执行 inline shell（默认 False）。建议独立只读工具内部复用 `skill_view(...,preprocess=False)`，保留原生检索与正文读取，避免业务 Skill 内容获得命令执行能力。

审核后的业务 Skill 可以放入每个隔离 profile 的 skills 目录，或配置唯一 `skills.external_dirs` 并保持只读。清单、别名和内容指纹仍维护在项目元数据文档中；目录存在不表示模型已实际调用。只读适配必须限制到审批过的技能，不能暴露服务器通用私有目录。

## 建议最小外围模块

- `hermes_runner`：启动独立运行进程，设定绝对 cwd、profile、session_id 和预算；调用原生 AIAgent；收集结构化结果；优先原生 interrupt，超时后由宿主终止运行进程并核对未知操作。
- `hermes_tools`：把现有工具目录 schema 登记到项目专用 toolset；闭包绑定可信 session token，不让模型参数选择家庭/角色。注册返回标准 JSON；原生回调只做运行观测，不代替发送记录。
- `hermes_skills`：只读复用原生列表与正文读取；强制 `preprocess=False`；固定审核目录和别名映射。
- `hermes_cron`：在隔离 profile 原生登记固定 `no_agent` 唤醒任务，`deliver='local'`、`failure_deliver='local'`、无聊天 origin。profile 的 scripts 内放简短入口脚本，显式调用项目后端 Python。脚本只驱动后台工作，不向微信直接发送。

以上是模块职责建议，不要求创建框架或每项都独立进程服务。尽量用普通函数与明确数据合同，避免动态 monkeypatch、复制原生循环、给 vendor 写补丁或上千行万能模块。

## 必须由实际验收证明的事项

1. 隔离 profile 的原生 `create_job → tick → no_agent script → 本地输出` 真正运行；无人值守错误也不走真实聊天渠道。
2. 初始化后的工具名称只包含当前角色批准项；普通调用和 tool-search 桥都不能调用终端、文件写入、Skill 修改、原生发信工具。
3. 实际原生 Skill 列表和正文加载正确，重复名称不误读，`preprocess=False` 不执行 inline shell；不能只测试项目自写文件读取。
4. A→B→A 三次独立运行不串家庭工具、会话记录、Skill 可见范围与 memory；Hermes 根目录和 cwd 都需要核对。
5. 原生 AIAgent 使用真实模型完成至少一次项目工具查询/业务操作，并保存模型实际调用与业务结果关联证据。API 成功、模型自然语言声称成功不算业务验收。
6. 取消、预算超时和宿主进程退出后的工作租约恢复；已接受但结果未知的发送必须先查操作/回执，不能用重新跑模型代替对账。
7. 原生事件/回调字段及 result 的 success/partial/interrupted 判断需要运行验证，回调日志不能包含凭据；公开验收报告只保留脱敏摘要。

未发现必须修改 Hermes 第三方核心才能继续的设计阻碍。本次未宣称上述集成测试已通过。

## 第三阶段原生 cron 实际验收补充

2026-10-03 已新增外围 `backend/src/digital_tutor/hermes_cron.py` 和隔离探针 `backend/scripts/probe_native_cron.py`，并在服务器实际通过原生 `create_job → tick → no_agent脚本 → Scheduler.tick` 链路。

成功产物：`evaluations/backend-stage3/native-cron-20261003T143111Z-f1e33703.json`，已从服务器下载。本次使用唯一临时用途 profile 与 cwd、独立 business.sqlite；原生 single-shot 任务已 completed，没有启用生产常驻任务。现有服务/profile/vendor 未变更。

- 原生第一次 tick 执行1次，第二次0次；execution ledger 为 completed，delivery_outcome=suppressed。
- 脚本 stdout 为空，原生输出记录明确 silent (empty output)。
- 脚本调用业务 Scheduler.tick，生成1条 daily ready 工作；没有执行 Worker、模型或渠道，业务 operation/message 数均0。
- job 设置 no_agent=true，deliver/failure_deliver均local，origin=null。
- 安装函数默认 paused=True；行为配置与清单由可信宿主提供，不对模型暴露 cron 创建工具。

首轮产物 `native-cron-20261003T142948Z-c3a429b7.json` 保留为 failed：适配器误用 Path.resolve 解析venv解释器软链接，导致脚本使用无pydantic的基础Python。修复为保留venv入口的绝对路径后在新profile重跑通过；没有安装额外依赖或修改Hermes。失败记录没有覆盖为成功。
