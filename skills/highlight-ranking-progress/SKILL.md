---
name: highlight-ranking-progress
description: H09 排行榜晋级高光；按最新服务动作打样核验事实、时间、素材与实际交付。
---

# H09 排行榜晋级

## 目标

用明确的前后档位呈现已确认晋级，不把普通排序浮动或服务分层混入成就。

依据：用户提供《最新数字员工服务动作打样.md》第 158 行；高光公式见第 137–144 行，完成/有效标准见第 168 行。源文档示例不是当前孩子事实。

## 依赖与适用事实

先调用 `service_skill(name="service-policy")` 取得跨动作禁忌、分层、频控、合并及回执规则，再调用 `service_reference(name="service-policy", path="references/highlights.md")` 取得高光四要素和素材规则。主动触达同时加载 `proactive-service`，组织家长文案加载 `reply-style`。

本动作不以儿童 S/A/B/C/D 服务层排除资格；需要分层事实时通过 `student_tier` 获取，unknown 不猜层。四要素中缺金句、比较基线或能力证据时明确缺口，宁可只陈述可核验事实。用户要求使用已有高光图片接口，不调用生图；mock 旧图中的其他名字不作为当前 UID 的文字事实。

专属取证：同一榜单指标和范围、定稿的前后档位、档位方向、晋档事件 ID/时间；努力解释需真实学习过程证据。

## 触发与频率

同口径前后榜单确认发生晋档，按该晋级事件一次。核对指标、周期、前后档位、档位方向与正式发布状态；普通名次浮动不能自行解释为晋档。

去重范围：UID + 正式晋档事件；同一事件一次，后续真实晋档另核验。

## 取证与执行工具

1. `daily_context(day)` 查询业务日期及当前学情，再用 `action_candidates(day)` 找到本动作候选；读取 `check_proactive(candidate_id)` 的证据、时间窗、去重范围及当前联系约束，补齐下列专属事实。
2. 以本动作事实组织共享四要素。只有 eligible 才执行；not_due 保留真实窗口，unknown/blocked 记录具体缺口；already_delivered 关闭主动重复。不得编造候选、素材 ID 或外部执行回执。
3. 主动交付使用 `deliver_proactive(candidate_ids, body)`，按真实候选合并正文。家长当前请求查看或重看时，用 `highlight_lookup` 取得当前可见素材，再 `reply_with_highlights(event_id, body, highlight_ids)` 向绑定微信发已有图片；被动重看按当前事件去重，不能借此伪装新的主动高光。
4. 检查工具返回的文字/图片及其他附件最终状态；按共享规则登记已发、等待、阻断或部分完成。unknown 不盲目重试。

排行榜档位与儿童SABCD服务层次是两个字段，不可混用。只写真实前后档位及已观察到的努力，不将晋档扩展为全面能力进步或保分承诺。

判定边界：只有名次数值变化、指标变化或服务层 S/A/B/C/D 变化不能判排行榜晋档。

## 输出、完成与有效标准

图文卡动作需有当前可用图片及最终送达回执；只有文字时记为文字降级/部分完成，不称高光图已送达。 accepted 仅是受理；最终 sent 才是实际送达，模拟 mock_sent 仅证明测试链路。保存事实来源、统计范围、动作/候选 ID、素材 ID、交付回执及未完成项。

有效效果另需家长或孩子的实质回应，如对具体表现的认同、积极情绪或后续交流；送达不自动等于认同、能力提升、习惯形成或续费意愿。

## 案例

文案组织时读取 [源文档短例与边界模拟](references/examples.md)，工具调用为 `service_reference(name="highlight-ranking-progress", path="references/examples.md")`。原文输出形式和实现改写分开保留；无本动作专属长案例时，不把其他动作案例移来冒充。
