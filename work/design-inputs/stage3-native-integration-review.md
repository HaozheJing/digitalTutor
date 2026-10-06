# 第三阶段原生接入独立审阅

日期：2026-10-03。审阅 `hermes_tools.py`、`hermes_runtime.py`、`hermes_host.py`、`materials.py`、`probe_native_model.py` 及工具总注册表。本文形成时真实模型测试仍在运行，未据未完成结果给出全部通过结论。

## 已确认的合理边界

- Hermes 原生 registry 注册业务工具，AIAgent 原生循环执行；实际 `valid_tool_names` 与批准列表逐项比较，业务handler闭包绑定session token，模型不能选择家庭或角色。
- 每次运行独立进程、profile和cwd；关闭通用memory/context文件、背景review和lazy installs；取消时先撤销业务session，再调用原生interrupt，超时只回收宿主创建的进程组。
- Skill/知识通过现有ToolRegistry路由，真实未知课程绑定拒绝；Skill加载来源、全文、guide指纹及角色边界独立于模型文字判断。
- 原生cron在唯一隔离profile实际完成create_job/tick/script/Scheduler链路，静默记录与ledger证明成立；未向真实渠道发送。

## 已即时反馈主控的问题

1. `hermes_runtime.main` 的 `traceback.print_exc()` 会持久化原始provider异常文本，与不记录凭据/请求体的目标冲突。应仅记录异常类型、业务错误码或不含值的栈位置，不打印异常原文。
2. progress模型探针创建空 `evidence_refs` 报告，却要求变更status；后端对任何变更都要求依据，因此夹具本身不满足合法提交前提。应创建纯虚构消息/证据关联该报告，不能放宽真实证据校验。`original_goal_preserved` 还应比较原goal/completion/remaining_work，而不只是数量和status。
3. 当前探针明确 `exclude_materials=True`，但 `HermesHost.__call__` 未传options时默认允许配置材料工具。应将未批准的内部材料默认关闭，可信宿主明确启用，避免另一入口绕过当前材料外发审批边界。

以上为当前阶段可修复的实现或夹具问题，不要求修改Hermes第三方源码。主控已收到逐项说明；本审阅者不修改该主控负责代码。

## 验收结论的最小证据范围

原生cron、只读Skill正文和企业版课程正文已有真实读取证据，可分别计为通过。真实模型完成业务工具调用、A→B→A数据隔离、daily/progress实际提交和取消，需等待保存的对应运行产物，并以业务数据库结果验证。

内部Skill/课程材料外发分支当前自动审批受阻；纯虚构数据模型测试不能代替该分支。Skill库存41份、局部native读取成功、程序测试通过和41份Skill模型语义验收是不同结论，元数据目录已明确区分。材料权限未解决或该分支未实跑时，应保留明确待验收项，不能写全Skill上线或启用自动触发。

## 服务器结果独立复核

本验收者随后亲自通过只读SSH读取 `evaluations/backend-stage3/synthetic-api-20261003/` 下a1、b、a2、daily、progress、cancel及native-materials JSON，并核对当前服务器源码中的三项修复。

- runtime已移除异常原文/traceback输出；host与runtime缺省`exclude_materials=True`。
- progress夹具新增纯虚构入站消息作为报告依据，并比较goal_key、goal_text、completion、remaining_work原值，不放宽后端门槛。
- 6个真实API case均passed。a1记录8次API调用，SQL、消息解释、模拟发送、原操作查询实际调用；两次无效发送请求被schema/target拒绝后纠正成功，最终只1个sent操作，失败轨迹保留。
- A→B→A为3个不同run/profile/cwd；A两次SQL结果digest相同，B不同，输出分别命中本家庭校验词且不含另一家庭校验词。
- daily实际调用`planner.commit_daily`并形成1份计划/1个目标；progress实际调用`planner.commit_progress`，原目标保持、报告消费。
- cancel记录`interrupted=true`、`completed=false`，撤销后没有新增操作；所有case最终输出路由为internal，工具列表仅批准dt业务工具，没有材料或通用系统工具。
- server-local材料探针passed：3个原生Skill读取包括service、planner内部入口、别名归一，完整guides及SHA256留痕；2个知识正文包括课程D01和产品介绍，指纹匹配、相邻课拒绝。明确`real_model_used=false`、`internal_materials_sent_to_model=false`。

由此确认第三阶段原生基础链路已经实际成立；内部材料进入真实模型及全Skill业务语义仍不在通过范围内。尚未把这些历史结果绑定到后来冻结的代码清单：主控将冻结后在新的final目录重跑6cases并保存指纹，保留此批原始结果。

冻结前已建议加强两个断言：b/a2只读运行前后家庭版本和操作不变；a1模拟回复正文包含实际学情行数且没有另一家庭校验词。历史trace中b/a2实际上仅调用读取工具，但把这些条件编入测试可防止后续回归被误计成功。

## 冻结版本最终结论：第三阶段基础能力验收通过

最终证据目录：`evaluations/backend-stage3/stage3-final-20261003/`。本验收者再次通过只读SSH独立读取acceptance、manifest、6个真实API case，并实际重算指纹，而非只接受通过标志：

- 49个源码文件当前SHA256全部与manifest一致；8个测试/运行产物SHA256全部一致；6个API case各自保存的源码清单与同一manifest完全相同，差异数0。
- 93项程序测试全部通过，失败0、错误0、跳过0。
- 6个真实模型case全部通过，新增SQL返回3条本家庭uid记录、a1模拟答复有实际数据依据、b/a2不改变家庭版本及不发送最终文本等断言均为true。
- daily符合目标与唯一计划要求；progress保持原目标和完成条件、消费绑定报告；取消后未新增操作。
- server-local原生读取3个Skill与2份知识正文通过；原生cron沿用`20261003T143111Z-f1e33703`独立通过记录。
- Hermes revision仍为`f97608f178d1ffeca59860195ab7da295f7c8e5f`，本次现场检查vendor工作树为空。

与 `acceptance.json` 一致，结论为 `stage3_foundation_passed=true`。内部材料进入真实模型联测仍为 `not_run_pending_authorization`，未发送真实学员数据、未使用真实微信；第四阶段完整两日模型服务为 `not_started`。真实课程绑定、源时区、覆盖时间与生产频控仍待业务来源确认。通过当前基础能力门禁不自动代表这些待办或第四阶段完成。

本次只读复核没有新增API调用或扩大测试范围。审阅提出的问题均在冻结版本中闭环，无新的阻止基础能力验收的问题。
