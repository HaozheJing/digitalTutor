# 第四阶段4C隔离K交付与待决记录

当前结论：4C候选、每日规划提交及4B接线修复已落地到隔离K；361项服务器程序测试通过。冻结32案真实Flash和两路独立评审完成后，4B/4C仍不满足原验收标准，未签署通过、未替换主目录H。反复出现的关键语义错误触发主方案第10条第4项，暂停依赖这些输出的自动服务放行及关口推进。本记录不表示4C全部功能完成。

## 实际实现与合同

| 范围 | 实现及验证边界 |
| --- | --- |
| 模式与发送 | passive/daily在领取、准备、实际dispatch三处限制主动动作，reply_to必须属于授权待答范围；旧queued操作不能借旧消息绕过。未知发送仍可按原operation核实。 |
| 可信候选与daily | candidates、family_candidates接已确认合成职责源与原消息证据；候选在能力快照前刷新，提交重新核源指纹。同目标沿用，完整责任目录、候选/pending决定与计划事务关联；只有daily新增事项及确定今日成员。 |
| 来信、阅读与记忆 | service_context记录本轮实际阅读、消息范围/指纹，发送前必须覆盖授权输入；区分已装配与真实读取。三轮记忆更正后新回复读取v2。普通问答不调用Planner。 |
| 控制范围 | 新空白event_id拒绝；旧异常空值原样保留，标为invalid_legacy并保守阻止主动动作，不声称已确认家庭级原授权。正常释放按明确范围核对。 |
| 材料恢复 | material_recovery登记本轮实际批准材料失败，原job恢复时重新读取。有限探测/真实时间节流，无变化不改家庭版本；正文不能代替必读专业规范。两种恢复实际partial→原job→sent。 |
| 提示词与来源 | service、planner_daily、planner_progress原角色/配方结构不变；强化课程设计、学情事实、正式政策和模拟来源边界。K真实回复仍有来源/承诺错误，不能仅凭提示词存在判通过。 |
| 演示与导出 | 增加有限demo_daily，默认零调用、显式tick执行；只读脱敏导出明确关闭SQLite、独立新文件发布，原库及历史证据保留。 |

源码按职责分布在 `work/stage4/4c-development-k/backend/src/digital_tutor/` 和外部验收脚本。复用现有Scheduler、Worker、HermesHost、原生模型harness和工具调用；vendor未变，没有新增Agent循环、任务队列或工作流框架。正式Skill/课程批准字节未扩大，调整集中于角色运行稿、工具合同和业务工程。

## 服务器验证与版本

执行目录：`/root/autodl-tmp/digitalTutor/work/stage4/4c-development-k`。本地同相对路径保存源码和证据镜像。服务器主目录无Git，不能仅以本地HEAD冒充部署版本；逐文件SHA为本批版本依据。

```bash
export PYTHONPATH="$PWD/backend/src"
export PYTHONIOENCODING=utf-8
DIGITALTUTOR_TEST_WORKBOOK="$PWD/data/表格数据.xlsx" runtime/backend/bin/python -m unittest discover -s backend/tests -v
runtime/backend/bin/python backend/scripts/accept_stage4_passive.py --run 4b1-compat-4ck-20261004 --case all --budget-seconds 360
runtime/backend/bin/python backend/scripts/accept_stage4_source_recovery.py --run 4b-source-recovery-20261004-k --case all --budget-seconds 360
runtime/backend/bin/python backend/scripts/accept_stage4_daily.py --run 4c-flash-20261004-k --case all --budget-seconds 360
```

4B2的16案分别以 `accept_stage4_behavior.py --run 4b2-compat-4ck-20261004 --case <case> --budget-seconds 360` 执行，精确命令保留在 `work/stage4/run_flash_k_4b2a.sh`、`run_flash_k_4b2b.sh`。上述为已运行命令记录，不能在旧run上重跑覆盖证据。

| 冻结K证据 | 案例 | native运行 | API调用 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 4b1-compat-4ck-20261004 | 7 | 11 | 108 | 原程序断言通过；完整语义逐案见独立报告 |
| 4b2-compat-4ck-20261004 | 16 | 22 | 241 | 原程序15通过/1期限格式误报；独立评审另发现真实语义不达标，不能写全通过 |
| 4c-flash-20261004-k | 7 | 14 | 94 | 原程序断言通过；承诺及可执行计划完整性未达标 |
| 4b-source-recovery-20261004-k | 2 | 4 | 40 | 两案程序及恢复链通过，未覆盖所有正式材料 |
| 合计 | 32 | 51 | 483 | 总关口未通过 |

361项unittest全部通过（32.865秒，含真实Excel只读回归）。另有限手测CLI三次新进程：默认0调用；显式tick为1 native/12 API创建goal及delivery；resume为0调用。手测调用不并入上表，且不代表关口通过。

23个被动场景补充真实新进程快照恢复检查，每案20次无变化扫描、0 handler尝试/0模型/0新外发，家庭业务版本及业务记录不变。方式为`new_process_exact_snapshot_copy`，没有恢复或修改原封存验收库；原文件前后SHA一致。受/proc权限限制不声称证明全服务器无进程，仅核可见进程、稳定复制与无租约。首批探测不支持的结果保留，不能当成功。

源/提示词/测试/材料153文件清单见[冻结指纹](../work/stage4/4c-development-k/evaluations/backend-stage4/4c-integration-k1/source-manifest.json)。每案manifest另外包含实际材料pin、provider非敏感配置及vendor指纹；所有最终采集包核验冻结源/vendor，扫描凭据后导出。模型仅qwen3.8-flash，目的地maas.qianwenaiapi.com。家庭、聊天、作品、学情和规则均虚构；真实Excel没有发给模型。

## 独立评审及未闭合缺陷

工程评审：[engineering-final-k.md](../work/stage4/review-4c-k/engineering-final-k.md)。业务语义评审：[semantic-final-k.md](../work/stage4/review-4c-k/semantic-final-k.md)。原程序结果、审计误报与人工判定分开保留，不回写原布尔。

1. **P1，未来通知承诺跨J/K复现。** K family_requests实际sent两次出现“有进一步安排信息我会在这里回复您”“有确认信息我会在这里回复您”，发送时只是pending，没有安排未来回告。后来daily建补课goal不能追认当时已有通知合同，总结仍pending。见[J/K逐轮对照](../work/stage4/review-4c-k/future-notification-j-k-comparison.md)。
2. **P1，方法来源失真。** K message_coverage把“指谁、家长代说、点头”说成符合逐条原文核验要求，实际参与形式不足以证明完成取证步骤。保留整课未完成状态不能抵消已发送的方法误述。
3. **4C可执行计划缺口。** inbound_interleave恢复daily唯一今日delivery的next_action=null。已证明取消让行、新快照和合法候选提交，未证明该计划具备可执行下一步；没有在daily模式执行它，不称实际执行失败。不能借被撤销run的旧步骤补证。create_and_reuse次日历史成员也存在步骤完整性不足。
4. **P2但评分不达标。** work_sample额外否定“喜欢吃”的依据，但成功读取的材料明确有“爱吃水煮鸡”；事实与来源评分1，原门槛要求2，不能因P2就放行。
5. **报告一致性欠项。** report_gate报告文字及原event均未读完，payload.remaining_work却为空。报告门禁仍生效，没有结案；后续progress消费前需闭合结构一致性，不能删除原报告绕过。
6. 其他P2：重复近似回复、字面转义换行、解释过长/催促、无必要语音欠项、向家长暴露内部字段。逐案保留，交由用户结合全文评审。

期限`2026-10-05 下午四点前`的旧解析器误报、同一请求无新增合同的第二条追问沿用pending、deadline字段未逐字带“前”、latest完整前缀的旧审计误报，均经证据人工核对；不为迎合oracle增加重复责任或改业务数据。原错误断言文件保留。这些纠正不取消上述真实缺陷。

J的空控制范围、批准材料与正式映射混淆、必读规范绕过及导出关闭问题已修复并在K程序/对应真实链复验；未来通知承诺修复尝试未成功。J32案48 native/447 API仅为失败历史，不计作K通过证据。

## 证据与当前边界

[完整多轮对话](../work/stage4/review-4c-k/all-dialogues-final-k.md)供用户共同评审。四组最终镜像均位于 `work/stage4/k-final1/<run>-final1/`，包含逐案result、manifest、实际工具轨迹、脱敏业务数据库及独立渠道快照。补充恢复证据在 `work/stage4/review-4c-k/restart-second-final/`；手测独立渠道在 `manual-channel-final/`。汇总指纹索引另存 `evaluations/backend-stage4/4c-k-delivery/manifest.json`。

未覆盖/未开放：4D完整service→报告→progress与可信更正链、报告消费后daily恢复、4E重复两日综合验收、未批准正式材料语义、真实家庭/微信、原本必需的图卡或语音交付。真实模型daily创建goal/delivery已经证明，但不能说其已经完成持续执行闭环。本轮未启动常驻服务，未自动Git提交、未清理未跟踪文件、未替换主目录H。

## 暂停依据与供用户决定的选项

已批准主方案第10条第4项原文：“冻结测试反复出现关键语义违规，必须调整模型或任务范围；不能依靠无限重试凑一次成功。”目前停止受影响的自动外发放行和4C签署，不再微调prompt反复碰通过。其余已启动验证与证据交付完成。

- **建议：先由用户共同评审这几段实际对话，并决定是否对需求登记回告、教学方法认定增加发送前人工审核。** 保留Flash和Hermes原harness；业务源/完成合同不放松。审核属于新业务运行约束，当前尚未实现或默认启用。
- **缩小自动服务范围。** 先允许内部daily与草稿验证，问题类别暂不自动外发，明确交接责任；需用户确认受影响体验和责任，不能默默丢弃需求。
- **继续保持原自动服务范围，另评估用户批准的Flash配置或Flash模型。** 先约定固定失败集及原评分，不使用max、不换目的地或扩大材料授权，不以多次重试挑成功样本。

无论采用哪项，恢复计划缺下一步和报告结构一致性仍属于待修复工程缺陷；不能通过选择方案豁免。下一次有代码/提示词变更须冻结新版本并复跑受影响链及前置兼容，不挪用K通过项。
