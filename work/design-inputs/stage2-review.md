# 第二阶段独立验收

日期：2026-10-03。审阅代理：stage2_review。范围：jobs、scheduler、outbox、send_policy、mock_channel、wechat、planning、application 以及阶段二执行细化。

## 首轮结论

首轮发现 4 个可复现问题，均已立即通知主控处理；在修复并完成服务器复验前不建议进入阶段三。问题属于可局部修复的实现缺陷，不需要改变 Hermes 架构。

独立回归：`backend/tests/test_stage2_review.py`，仅复用测试夹具，使用虚构家庭与临时 SQLite/模拟渠道；不访问真实学员数据或真实微信。本地 bundled Python 首次运行 4 项均失败。最终交付以主控同步后的服务器结果为准。

| 优先级 | 问题及复现 | 影响 | 修复要求 |
|---|---|---|---|
| P1 | 准备回复 → 21:00 dispatch 取消 → 次日新 request_id 回复同意图，返回旧 cancelled 操作 | 因 operation_keys 永久占位，未发起的回复/交付永久无法推进，改话术也被拒绝 | 仅明确 cancelled 且 dispatch_count=0 的操作可重新准备；保留旧操作审计，unknown/dispatching 不得借此重发 |
| P1 | progress handler 返回空结果而未 commit_progress，Worker 将 job 标 succeeded | report 仍未消费、Event.update_pending 持续、daily 受 REPORTS_PENDING 阻断，且没有待执行 progress job | 仅报告真实 applied/superseded 才能成功收尾，否则进入可恢复等待 |
| P1 | 持租约 worker → set_enabled(False) → set_enabled(True) → 原 token 调 message.interpret | 停用前旧模型权限恢复，旧运行继续修改当前状态；generation 字段没有实际校验 | 将 generation 纳入 lease/session/dispatch/finish 栅栏；重新启用后旧任务可重新领取但旧 token 不得恢复 |
| P1 | 渠道 send 调用未返回时先收到 delivered 回执，再由 send 返回 sent | outbox 无条件写回将 delivered 降级为 sent，可能导致已满足 delivered 的交付永远不能完成 | 统一单调状态合并，保留更强已确认事实以及冲突记录 |

## 非阻断提醒

首轮 Worker 默认 60 秒租约且无续租能力的问题，复验时已补 JobQueue.renew 和宿主心跳。独立测试使用真实墙钟、1 秒初始租约，handler 等待 1.4 秒后成功调用写入工具，结束后 token 撤销。续租复用 generation 和租约校验，未见已失效 token 续租绕过。

## 补丁复验

四个 P1 的本地独立回归全部通过。复查确认：取消重新准备只适用于 dispatch_count=0，旧审计记录保留；unknown/dispatching 没有得到新发起许可；progress 未消费时不再成功收尾；generation 在领取、校验、收尾和续租生效；发送返回与渠道回执共用单调合并。

主控报告服务器首批 57 项与两日模拟通过，证据目录为 `/root/autodl-tmp/digitalTutor/evaluations/backend-stage2/20261003T140722265634Z/`。本审阅代理没有单独登录核对该目录，服务器结果由主控统一核验。

补丁复验额外发现一个 P2：handler 在停用后抛普通异常时，Worker 的异常清理调用 finish 再抛 LEASE_LOST，异常逃逸 run_once。已增加回归 `test_handler_exception_after_stop_does_not_escape_worker`。主控已补获清理阶段 BusinessError 并返回 cleanup_error；四个 P1 和本项 P2 的修改均已完成。主控统一服务器最终 run `20261003T141601255327Z` 为 59 项全通过，包含全部 6 项独立回归。当前没有剩余阶段二阻碍，可进入阶段三。

## 已审阅的合理设计

- 家庭领取在 SQLite IMMEDIATE 事务内检查，过期 token 会被替代；普通来信优先于规划任务。
- 发起前重新检查控制、窗口、输入版本；外部渠道副作用在独立 SQLite 留痕，发送与本地事务之间不持有长事务。
- unknown/dispatching 使用原 operation 查询，现有恢复路径没有自动重发。
- 主动额度在准备事务中检查，并统计 queued/unknown 操作；内部复核 session 标记 no_contact。
- 进度提交精确覆盖关联事项、报告消费与 Event 更新在同一事务；update_pending 从未消费 report_links 派生。

上述审阅是代码及局部模拟证据，不替代最终服务器全量测试、两日模拟、真实模型及未来真实渠道验收。
