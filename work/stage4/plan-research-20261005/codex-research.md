# Codex 计划机制调研（2026-10-05）

依据当前官方文档、本机已安装 `@openai/codex` 0.144.0 包元数据及该二进制生成的实验协议。未运行模型、未启动持续任务，也未把当前桌面版本推定为这份CLI版本。安装包仅带启动器/二进制，本次未审Rust内部实现；以下明确区分公开合同与工程推论。

## 三个相关但不同的机制

1. **Plan mode**：在实现前组织方案、确认边界。官方提供 `/plan` 模式切换；App Server的`collaborationMode`选择模式及其内置指令。不能把“有计划模式”解释成已经存在业务任务队列或计划执行器。[模式与目标区别](https://developers.openai.com/blog/mastering-codex-remote-for-engineering)、[命令](https://learn.chatgpt.com/docs/reference/slash-commands)。
2. **执行中的步骤清单**：`update_plan`输入步骤文字、状态和可选解释；工具描述要求同时至多一个in_progress。官方推荐工作后更新步骤、结束前逐项核对，阻塞/取消需说明原因。此描述本身不证明后端会自动校验实际代码完成；公开Schema也不包含测试/业务证据字段。[工具和使用约定](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide)。
3. **持续目标Goal**：保存线程级目标、生命周期及预算，在安全空闲边界决定是否继续，完成要核证据。官方说明plan-only不触发Goal自动续行，无工具调用可抑制连续空转。对digitalTutor有参考价值的是保留目标、基于新状态续行与避免空转；不意味着应为项目再建一套Goal运行框架。[官方设计说明](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)。

## 协议与本机核验

只运行本地Schema生成命令：

```powershell
codex app-server generate-json-schema --experimental --out work/stage4/plan-research-20261005/codex-schema-0.144.0
```

实际产物：

- `v2/CollaborationModeListResponse.json`：ModeKind为plan/default。
- `v2/TurnPlanUpdatedNotification.json`：threadId、turnId、plan必需；每项仅step、status；状态为pending/inProgress/completed。这是客户端协议枚举，不应和工具参数中的in_progress拼写混用。
- `v2/TurnStartParams.json`：collaborationMode设置与sandbox/approval等配置属于不同字段；不能推断模式名替代底层权限检查。

官方App Server同时区分`turn/plan/updated`结构步骤更新，以及Plan模式的`plan`文本item。流式`item/plan/delta`是中间片段，最终`item/completed`的plan文本才是权威结果；执行命令、工具调用另有自己的结果事件。这是“计划提议”和“实际动作结果”分开的公开合同。[协议](https://learn.chatgpt.com/docs/app-server)。

## 对digitalTutor的具体启发（方案推论）

- 今日事项可以按有序工作清单交给智能体；今天选谁、先后顺序与事项是否最终完成分开存储。现有Plan.items、Event及Report已经提供这些边界，先补合同，不再建第二份可独立写入的计划状态。
- 提交成功应代表“当前版本有可执行入口或明确等待”，而不仅是名单写入成功。失败返回具体事项及字段，允许同一原生运行修正提议；不能落半份计划，也不能占用当天唯一成功名额。
- 计划作为上下文：每次恢复从业务数据库重建已完成、当前动作、等待与证据；不要依靠模型压缩摘要或过期工具清单还原业务事实。
- 完成依赖实际结果：TODO被勾选、一次run结束、提醒已发，分别不是课节完成或交付完成。现有可信学情/回执合同继续负责这一点。
- 无新状态时不继续；新来信让行后保留业务目标，从新快照产生新提议。可以保留原草稿作参考，但必须重新验证，不能直接恢复旧能力或旧提交。

限制：以上没有证明Codex自身对任何外部业务提供强事务/完成证明；没有读取本机私人会话、配置或凭据。官方文档若未说明的持久化细节，不据此猜测。
