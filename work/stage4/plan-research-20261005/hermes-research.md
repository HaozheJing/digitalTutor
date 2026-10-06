# Hermes 原生 plan / todo 只读核验（2026-10-05）

结论：保持现有 Hermes 原生 harness。本轮可先不接 todo，优先修既有业务提交和语义合同。原生 `/plan` 是一轮规划提示，`todo_list` 是运行内工作清单；两者都不能替代 digitalTutor 的 daily/progress 合同、家庭权限、租约、发送回执或业务完成证据。若以后接 todo，先限单 run 内记事，并独立验证压缩、持久化和审计；不为此改 vendor 或自动升级。

## 版本与证据范围

- **服务器实际锁定版**：`/root/autodl-tmp/digitalTutor/vendor/hermes`，`git rev-parse HEAD` = `f97608f178d1ffeca59860195ab7da295f7c8e5f`；提交为 `2026-09-24T03:08:47-07:00 chore: release v0.21.5 (2026.9.24)`。两次 `git status --porcelain --untracked-files=no` 无输出，仅表示跟踪文件未修改，不声明全部未跟踪环境文件为空。原始结果在 `server-inspect.txt`、`server-dispatch.txt`。
- **官方最新 main 的本次观察**：`git ls-remote` 的 HEAD 与 refs/heads/main 均为 `af8839df1038cc026075fb254afa22edc19d911d`。已按这个 commit 下载少量官方源码，不把浮动 main 当服务器版本。GitHub API 返回限流 403，故不猜该 commit 的发布日期或 release tag，见 `official/latest-version.json`。
- **K 无漂移**：按 `4c-development-k/evaluations/backend-stage4/4c-integration-k1/source-manifest.json` 的完整名单，对服务器逐文件重新计算 SHA；153/153 与 manifest、本地镜像完全一致。见 `k-source-verification.json`、`server-k-hashes.txt`。首次工具只枚举了 104 个代码/提示文件，随后改为 manifest 的完整 153 项复核；不能把初次未枚举的材料误报为漂移。
- 全过程服务器只读；只下载源码到本目录、写本地调研文件。没有模型 API 调用、模型模拟通过声明、vendor/源码/配置/旧数据修改。SSH 密码只经临时环境变量，finally 清除。未读取 provider 密钥。

以下锁定版路径以 `hermes-source/` 为本地只读镜像、服务器 `vendor/hermes/` 为原路径；行号来自实际下载文件。各文件 SHA 在 `research-source-manifest.json`。

## `/plan` 的实际能力及限制

`agent/plan_prompt.py:1-5,11-26,53-71` 明确：`build_plan_prompt` 生成普通 turn 文本，要求该轮只规划、仅写 `.hermes/plans/...md`，保存后询问是否执行。`hermes_cli/cli_commands_mixin.py:1917-1942` 将其通过 `_queue_prompt_turn` 排队为正常请求；Gateway 的 `gateway/run_inbound.py:881-885` 和 TUI 的 `tui_gateway/methods_tools.py:692` 同样是 prompt 改写入口（服务器 grep 证据）。

这不是新增 Agent 循环，也没有原生“计划已批准→恢复全部权限”的持久状态机。该函数不移除工具、不建立审批记录、不强制拦截写操作；不执行的约束主要是该轮提示。进入执行是后续用户授权的普通 turn，并非提交正式业务 Plan。相对路径是文件交付位置，不等同 SQLite 计划。

官方当前 `agent/plan_prompt.py` 与锁定版去空白行后逐行相同。官方 [Skills System 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/) 也说明 `/plan` 已是内置命令，以工作目录下 markdown 为输出。

另有官方目录记录 `plugin-catalog/plan-mode.yaml:1-29`，名称为 plan-mode、tier 为 **community**，指向外部维护者插件，描述了 `plan_mode` 工具与 hook 约束。这是可安装的社区扩展目录项，不代表 Hermes 核心已提供相同权限保证。未访问第三方仓库、未安装、未验证其执行效果，不能将目录说明当本项目已具备能力。

## `todo_list` 的真实结构、权限与完成语义

`tools/todo_tool.py:26-49,79-126,191-212,214-216,221-284`：每个 AIAgent 一个内存 TodoStore；统一 `todo_list` 调用，无 todos 参数读取，带 todos 写入，默认整单替换、merge 按 id 更新；返回完整清单、revision、状态计数。支持 parent 嵌套，最多 256 项、单项内容 4000 字符。只有实际状态变化才递增 revision。

原生状态为 pending/in_progress/completed/cancelled。schema 的描述要求验证后完成、同时只一个 in_progress，但存储代码只做形状/状态规范化，不查询实际工作证据，也不提供家庭授权、业务截止、发送幂等或 Event 绑定。模型能直接写 completed。故 completed 不能证明补读完成、实际外发、图卡交付或取消成功；单 in_progress 也不能当并发排他锁。

`tools/todo_tool.py:280-284` 注册 toolset `todo`；`toolsets.py:131` 提供工具集；check 函数恒 true 仅表示无外部依赖。真正是否暴露仍受 `agent/agent_init.py:1060-1088` 初始化工具选择控制。`model_tools.py:612-618` 还保留旧名 todo→todo_list 的分发兼容。它不是业务工具能力令牌校验的替代物。

## 持久化、压缩与中断恢复

1. **活体状态**：`agent/agent_init.py:1146-1181` 每次初始化创建新的 TodoStore；session_db 是调用方可选提供的对象，不是 todo 自动建立的独立数据库。
2. **压缩**：`agent/conversation_compression.py:3064-3140` 从活体 store 生成快照，去旧快照，保留 pending/in_progress 及有活动子任务的父项；折入消息尾或新增带 synthetic 标记的 user 消息。已完成项一般不再注入；此机制维持模型上下文，不核验业务真伪。`tools/todo_tool.py:98-126` 是具体渲染逻辑。
3. **历史恢复**：`agent/turn_context.py:662-667` 仅在传入 conversation_history 且 store 空时调用 `_hydrate_todo_store`。`run_agent.py:1055-1119` 倒序寻找配对的 assistant 工具调用及 tool 回执，限制结果大小，按 revision 恢复；不会只凭孤立 tool 文本恢复。
4. **落盘入口**：`agent/session_persistence.py:388-427` 的 `_flush_messages_to_session_db` 才会保存消息；没有 `_session_db` 立即返回。`save_trajectories=False` 还关闭独立 trajectory JSONL（同文件:439-446）。普通 todo 函数本身无文件/数据库写入。
5. **中断**：`agent/interrupt_control.py:109-192` 提供 soft/hard interrupt、工具中断传播及压缩提交栅栏。中断不使外部业务原子回滚，也不自动重领本项目任务。当前项目已复用 `agent.interrupt(hard_cancel=True, tool_reason=...)`；授权撤销、外发未知与重试仍归业务 Host/Worker。

**锁定版源码发现，尚未真实新进程模型验证**：当前 schema 是 todo_list，但锁定版 `run_agent.py:1113-1119` 恢复配对仅比较旧调用名 `todo`。这可能使以 canonical todo_list（或 Tool Search bridge）记录的历史不能通过该判断。官方本次 main `run_agent.py:1115-1122` 改用 `is_todo_tool_call`；`official/tools/todo_tool.py:251-291` 统一识别新旧名及 bridge。当前项目没有启用 todo，也从业务 DB 重组上下文，因此此发现不证明当前运行丢失业务数据。不能据此擅自改 vendor/升级。最新 todo 另移除了锁定版字符串 JSON 入参宽容转换，见 `official-diff-todo_tool.txt`，接入时不能假定版本行为完全相同。

## digitalTutor 接线现状与审计接点

K `backend/src/digital_tutor/hermes_runtime.py:62-75` 只给 `enabled_toolsets=[TOOLSET]`，随后 `actual==expected`；TOOLSET 为 digital_tutor。原生 todo 没有启用，单纯改 prompt 让模型用 todo 不会凭空获得工具。现有 service、planner_daily、planner_progress 的业务工具权限仍生效。

K `hermes_host.py:49-50,70-86` 每 run 单独 work/profile、新 Python 子进程；`hermes_runtime.py:67-69,88` 使用新 run_id 作为 session_id，不传 session_db，也不传 conversation_history。因此即使只加入 todo 工具集，也只有当次 run 内 store；跨 job/租约恢复不会自动继承上一进程 todo。正确恢复依据仍是既有 DB 中原事项、未答输入、operation/receipt/report，以及新快照。

审计必须另接：`agent/inline_tool_executors.py:1-7,235-239` 把 todo 作为 agent-level inline 工具，直接使用 `_todo_store`，绕过普通 registry handler。当前 `backend/src/digital_tutor/hermes_tools.py:17-56` 的 trace/on_tool 只覆盖注册的 dt_* 包装，因此启用原生 todo 后，**不会自动进入现有业务 tool_trace**。原生 `inline_tool_executors.py:29-57` 提供 terminal post_tool_call hook（best-effort），同文件:62-84 提供结果变换接点；可在将来通过原生 hook/会话记录机制观察，但不能把 best-effort hook 当业务写入原子审计。本轮不实现接线，也不承诺已有覆盖。

## 最小复用建议

| 能力 | 判断 |
|---|---|
| AIAgent 原生 loop、工具调用、压缩、interrupt | 继续复用当前 harness；无需第二循环、任务队列或工作流框架 |
| `/plan` 的分解/验证写法 | 可作开发规划提示参考；其代码实现导向不宜直接替换教辅 daily 配方 |
| `todo_list` | 可选的单 run 内工作记事；本轮不接也不阻塞业务修复 |
| 原生完成状态 | 只能说明模型更新了清单，不能提交 Event/Plan、核销未答或完成交付 |
| 跨 job 恢复 | 继续由本项目队列、租约、业务 DB 新快照承担；不依赖未接线的 native history |
| plan_mode 社区扩展 | 不纳入当前方案，不自动安装或扩大权限/工具集 |

若以后确需接 todo，变更应限定工具 allowlist 与原生审计接点，并验证：实际工具集合、同 run 压缩保留、取消后无业务授权延续、跨 run 不误承接、工具结果审计完整、completed 不影响业务完成标准。是否做跨 run 记事另行定义，不顺便引入第二业务事实库。

## 官方定位链接

- [锁定版 plan prompt](https://github.com/NousResearch/hermes-agent/blob/f97608f178d1ffeca59860195ab7da295f7c8e5f/agent/plan_prompt.py#L11)
- [锁定版 todo 工具](https://github.com/NousResearch/hermes-agent/blob/f97608f178d1ffeca59860195ab7da295f7c8e5f/tools/todo_tool.py#L26)
- [锁定版历史恢复](https://github.com/NousResearch/hermes-agent/blob/f97608f178d1ffeca59860195ab7da295f7c8e5f/run_agent.py#L1055)
- [本次 main 的 todo 名称兼容](https://github.com/NousResearch/hermes-agent/blob/af8839df1038cc026075fb254afa22edc19d911d/tools/todo_tool.py#L251)
- [本次 main 的 plan prompt](https://github.com/NousResearch/hermes-agent/blob/af8839df1038cc026075fb254afa22edc19d911d/agent/plan_prompt.py)
- [官方目录中的社区 plan-mode 声明](https://github.com/NousResearch/hermes-agent/blob/af8839df1038cc026075fb254afa22edc19d911d/plugin-catalog/plan-mode.yaml)

可访问的源码已保存本目录供复核；网络部分只使用官方 NousResearch/hermes-agent 仓库及 Hermes 官方文档，未将外部 issue 提案当作已实现依据。
