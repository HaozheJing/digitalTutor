# Native06 剩余家庭请求与普通来信独立工程评审

依据 native06-daily-complete/family_requests 和 native06-behavior-complete 五案的新test15证据。六案各83个运行指纹与test15一致，12个闭合业务/独立渠道库完整性ok、immutable只读前后SHA不变。机器字段见 engineering-native06-remaining.json。

| 案例 | Native/API/实际sent | 工程结论 |
|---|---:|---|
| family_requests | 8/54/5 | 本次单pair正式决定确认缺陷已闭合；重复goal来信责任问题仍分列 |
| independent_need | 1/8/1 | 精确来源绑定等待通过，不提前清未答 |
| memory_correction | 3/27/3 | 同ID记忆更正及第三轮发送前读取实链通过 |
| missing_guide | 1/8/1 | 资料缺口仍保留，未进Planner |
| missing_policy | 1/15/1 | 缺口逐意图保留，未进Planner |
| work_sample | 1/16/1 | 普通来信工程权限范围通过 |

## 单pair正式安排确认

delivery pending:cbf28ba4b3d056f6145b96d49bc018776683a7f0718745ae1ad8f4b44242603b，原pair为 msg-c2e5b7608eae43a6b961c6c7f385c090 / intent-weekly-summary。model06实际commit_admission `req-admission-run-47f81ed5-weekly-summary` 创建 event-369924db35a74295b23b429811264d09，pending v2、decision_revision1、await_decision=false；真实receipt包含原grant。

model07（run-58b94db804a34ff3acac973550196f97）先有INVALID_TARGET及显式final确认的DELIVERY_RESPONSIBILITY_DELEGATED拒绝，随后合法partial确认成功：operation-fb59bbc1e4bd4e79af6a9c8c153da119、sent、dispatch_count1。其宿主phase逐字段绑定原pair/pending/决定/request/revision/Event/grant，同run host acknowledgement精确引用该operation。独立渠道中存在相同正文、收件人、reply_to与实际发起时间。

原登记operation-52706dccbb414752993db429f43dae4b等无phase；登记purpose与正式确认不同，未修改旧账或把原“等待安排”当确认。确认正文准确表达已正式承接的时间窗口；具体内容语义由独立语义审查。原future intent最终refs仍空，实际交付责任由原Event保留，没有拿安排确认伪装交付完成。稳定探针没有重复确认发送。此案未注入unknown，未知重试证据仅来自test15程序测试。

原第二条goal来信“请帮我明天补读第1天”仍在决定前通过 operation-01b852806aa643b09bd5392a10d5e01b 的final引用结清，实际文字仍说等待安排。正式goal确认operation-206ed60b7e144cb1a3819ae50cc5b68c没有为该第二pair补正式确认引用。这是保留的来源/语义责任P1，不能用同目标去重或第一条已确认自动豁免，也不在本次窄修范围内擅自新增alias工具。无重复Event不等于所有输入责任都已完整覆盖。

## 普通来信与记忆

四ordinary实际mode=full；持久Job均service，无daily/admission/progress，无planning_requested、Event、Plan、Report。missing_guide与missing_policy未答意图各自保留真实partial，未被待决定新规则误算为已履行。

independent_need实际passive。当前候选source摘要、原授权片段、pending精确pair/v1、真实partial双账、current waiting outcome与admission attempt0全部满足新后验检查；不是简单改allow_unanswered。旧native05 false和外部裁定留存，本次是新冻结的新实跑通过。

memory第三run为run-5c062557b5ed4beaba0feb0c96d05bec。实际工具轨迹memory.read成功先于wechat.send_message；返回更正后的同ID memory-8a543c35a88f45babf5848513e55cefe v2，原v1保留历史，active只有一条。发送前保存assembly精确投影亦是该v2，独立导出记录assembly SHA26192826…、prompt SHA0de1f78d…与run绑定；本地取得的是带来源SHA的精确子集，不冒称已对未下载的全assembly重算SHA。实际回复沿用“午饭后十二点半、晚上不便、一次一个问题”。此结论有发送前读取和装配证据，不从final DB倒推。

以上为分案限定工程结论。整体4C不因全部program_checks=true而签署；上述重复请求责任与回答内容问题仍按用户授权记录并供共同评审。

补充定性（核对 model02 完整轨迹）：run-740e82f79ef24802b70032a1a7dee3f1 将第二来信解释为 service_kind=reply、requires_reply=true，随后显式给第二 pair resolves_intent=true；同一条发送对第一条已有 pending 的原 pair 明确使用 false。该轮没有调用 pending.propose_makeup / pending.propose_service / planning.record_pending，也没有持久 alias，所谓“同一事项”只在模型解释文本中。故这是模型在未建立第二来源责任绑定时自行判已答的业务责任/解释 P1，不能缩成措辞问题；现有精确来源宿主门禁保护了已绑定的第一 pair，没有证据表明宿主已经持有第二 pair 的受限确认责任却漏执行守卫。若要强制覆盖重复来源须另行明确来源绑定合同，本次不自动推断alias或扩改工具。
