# 第三阶段独立边界审阅

日期：2026-10-03。范围：family_memory、context、knowledge、skill_loader、materials、tools。验证使用虚构家庭与临时数据库，未接真实渠道。

## 发现与修复

发现 P2：更正记忆原先只要求新的 message_id；一条迟到的旧消息可覆盖较新的偏好。已通知模块作者，作者补充发生时间检查 `source.occurred_at >= old.occurred_at`，旧消息返回 MEMORY_SOURCE_OUTDATED；相等时间、不同消息仍可以更正。独立回归通过，原有 controls 不受记忆更正影响。

跨代理互审另发现课程绑定更正可能保留旧课程版本的课节映射，由学情模块修复：课程身份/版本变更时，未提供新映射则清空旧映射为 unknown，原映射保留在 history。该项在 `test_stage3_learning.py` 中验收。

## 独立验证

`backend/tests/test_stage3_review.py` 新增 3 项，本地全部通过：

1. 迟到旧消息不能更正覆盖较新记忆，原正文保持。
2. knowledge 工具按可信会话的 family 选择资料绑定；请求其他家庭的 document_id 被拒绝，添加 family_id 参数也被 schema 拒绝，拒绝前不会读取后端资料。
3. 会话撤销后，context、memory、knowledge、skills、SQL schema、微信读取均拒绝读取。

代码审阅确认：

- Context 的 progress Event 与 Report 来自绑定运行，不能通过 selector 切换到另一事项；日计划成员与操作集合按原事项筛选。
- 过期记忆从读取中剔除，controls 从当前业务记录独立读取。记忆删除移除当前及修订历史正文；原消息、证据和备份明确不属于此次删除。
- 知识模型参数仅接受 document_id；URI 来自固定 manifest，拒绝越界、编码穿越和多地址；正文 SHA-256 必须匹配，缺资料或内容变化不使用相邻课替代。
- Skill 目录编号、角色清单、文件范围及原生返回来源路径共同核验；必读规范有固定指纹，原文变化不会被静默接受。目录摘要不等同正文。
- 材料加载器不能覆盖可信 context 信封；工具不能接受家庭、角色或文件路径来扩大权限。

当前没有发现未修复的阶段三阻碍。上述仅为独立代码/局部模拟证据；服务器全量、真实模型工具调用及 Hermes 原生生命周期证据由主控统一记录。正式课程适用版本和数据覆盖仍未知时必须保持 unknown，模拟绑定不能当成真实业务确认。
