# OpenViking课程知识优化与测试

本轮以第一周为对照，验证企业版支持不再拆分已经按课整理好的短课纲，并将同一加工方式应用到四周。业务维护原稿不变，旧入库批次保留；新批次供后续知识接入联调。

执行日期：2026年10月2日。服务器 CLI 版本为0.4.22。

## 本轮调整

原来的28个来源单元生成45个正文片段。本轮使用 `--args parse_mode:no_split`，由服务端 MarkdownParser 处理，保持28个单元对应28个正文文件。逐单元 `ov read` 验证返回文本与本地生成文本完全一致，课程段落、表格及 Markdown 结构均保留。

每份资料前只增加简短的级别、书目、原文课次和来源声明。原文正文没有改写；完整哈希、来源行号、未知版本等字段仍由清单维护。原稿来源快照仍为 `source-c764d8b325cc`，加工修订号为 `source-c764d8b325cc-nosplit-v2`，两者不能当成已确认的正式课程版本。

自动生成摘要未稳定遵守导入指令，仍有缺少级别、未审核声明等情况。已改为从原文章节标题和来源字段生成28份简短导航，通过CLI更新对应目录已经存在的 L0 摘要与 L1 概览，保留更新前回执，写后逐份读回。读取接口会去掉末尾换行，因此摘要比对只忽略末尾换行，不放宽文字检查。

自动摘要初检不通过的记录仍保留在 `coverage-*.json`；修正后的结果单独记录在 `navigation-check-*.json`，不会把初次失败改写为成功。

## 本次整理核对

2026年10月3日只读核对发现：远端保留新批次的加工文件及回执，但 `access-map.json` 与最终 `validation.json` 目前在本地，不能假定远端相同路径已存在。接入请使用下方本地已生成映射；部署该映射是独立操作。本次未重跑课程入库、检索或正文验收。见[环境核对记录](../deployments/docs-cleanup-20261003T021247Z.json)。

## 实际地址和读取要求

新批次根地址：

```text
viking://resources/digitaltutor/courses/L2/fantastic-mr-fox/source-c764d8b325cc-nosplit-v2
```

```text
week-01/
  day-01/content.md
  day-02/content.md
  day-03/content.md
  day-04/content.md
  day-05/content.md
  week-overview/content.md
  ability-alignment/content.md
week-02/  第6至10天及对应总览、说明
week-03/  第11至15天及对应总览、说明
week-04/  第16至20天及对应总览、说明
```

接入使用本批 [access-map.json](../build/openviking/courses/source-c764d8b325cc-nosplit-v2/access-map.json)。每个单元包含唯一正文地址 `actual_read_uris`、检索范围 `canonical_scope_uri`、来源信息，以及固定的 `navigation_summary`。

已知目标课节时，先由真实业务课节映射到原稿课次，直接读对应单元。只有问题主题时，在本批指定范围内检索多个候选，再核对导航、来源与正文。不要只取第一条搜索摘要作为最终依据；补课日期也不能代替原课程课次。

`find` 返回的文件级摘要仍由服务端生成，书目根目录和周目录的上层概览也仍为自动生成。本次固定的是28个来源单元目录的摘要和概览。因此提供给智能体的导航应采用映射中的 `navigation_summary`，同时携带业务审核与版本字段。向量命中不代表内容适用于当前学生。

服务器企业版配置和原有默认服务连接均沿用前次交接。运行时要显式指定企业版配置，并限定本批根目录，避免同时召回新旧加工副本。示例：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/root/autodl-tmp/digitalTutor/runtime/openviking/enterprise-ovcli.conf
OV=/root/autodl-tmp/digitalTutor/runtime/openviking/bin/ov
"$OV" read 'viking://resources/digitaltutor/courses/L2/fantastic-mr-fox/source-c764d8b325cc-nosplit-v2/week-01/day-02/content.md' -o json
```

## 检索测试方法

问题及预期课节在查询前固定于 `ops/course_retrieval_cases.json`，共15个资料可回答的问题和5个资料边界问题。自然问法不提供明确课次提示，涉及人物特点、逆风情节、动作顺序、颜色描述、动物介绍、概括、推测和朗读等。

两版都用相同问题、相同目录范围和 `find --limit 5 -L 2`。第一周先测试8个问题；四周导入后，再在各自整书范围测试全部20个问题。报告同时给出首位命中、前三个不同来源单元命中、前五个不同来源单元命中。旧版多个片段可能来自同一课，报告去重后的课次，同时保留原始命中地址；该比较反映当前调用参数下的使用效果，不是对切分策略的纯单变量实验。

第一周8题：旧版首位命中3题，新版5题；前三名均命中7题，前五名均命中8题。“孩子说狐狸很聪明，但是找不出书里的理由，我怎么引导？”是保留的难例：旧版目标课在第4个不同来源单元，新版在第5个。没有因它失败而修改预期课节。

整书测试结果及各题明细见 [retrieval-book.json](../build/openviking/courses/source-c764d8b325cc-nosplit-v2/retrieval-book.json)，汇总见 [validation.json](../build/openviking/courses/source-c764d8b325cc-nosplit-v2/validation.json)。最终汇总文件在本地根据原稿、已保存的检索结果和服务器上观察到的任务状态生成；最初整理时未完整下载的回执，已在2026年10月3日恢复并重算核验，见下文后续记录。

15个有对应资料的问题，在整书范围的结果如下。以下均为同一组开发回归题，过滤策略是在观察难例后调整的，不是独立测试集准确率。

| 方式 | 第一项命中 | 前三个来源单元命中 | 前五个来源单元命中 |
| --- | --- | --- | --- |
| 旧版默认切分及原始摘要，find | 7/15 | 9/15 | 9/15 |
| 新版不拆分，find | 10/15 | 10/15 | 13/15 |
| 新版加每日课程类型过滤，find | 10/15 | 12/15 | 13/15 |
| 新版加每日课程类型过滤，search，无会话上下文 | 10/15 | 12/15 | 13/15 |

已通过CLI为28个来源单元递归设置明确的 `record_type=lesson`、`record_type=week_overview` 或 `record_type=ability_alignment` 检索标签。过滤测试中返回结果均属于每日课程，说明该过滤实际生效，不是仅把字段写在Markdown里。

当前推荐：具体教学指导采用带 `record_type=lesson` 的 `find`，已知课次时直接读取；周安排或能力对标则选择相应类型。无会话上下文的 `search` 在这组题上没有增加命中，不默认替换 `find`。本轮未配置或验证新的重排模型，也未测试带真实会话上下文的检索。

保留三个难例：关于狐狸聪明的证据、第2天逆风情节、喜欢人物的理由。类型过滤后，它们均未进入前三名；其中前者及“喜欢人物的理由”未进入前五名。尤其后一个问题旧版能首位命中第19天，而新版未命中前五名，是明确的单题回退。因此目前只推荐知识层联调，不能把第一条命中直接视为答案。后续应结合已知课程进度缩小范围，或在证据不足时读取课程导航、扩大候选并核对正文；这部分选择与回答行为仍待老师智能体验证。

5个边界问题分别涉及未知出版页码、不存在的第21天、仅凭打卡认定能力、原始课件链接、正式版本与班期。检索返回相似课程时，也不能据此认定问题可回答。本轮仅记录返回结果和清单中的资料缺口，没有调用完整老师智能体，不声称已经验证正确拒答、追问或人工衔接。

## 维护和再次加工

本批目录下保存 `manifest.json`、`navigation.json`、`access-map.json`、`validation.json`、全文校验报告、检索对照报告和汇总回执。详细脱敏CLI回执已在后续恢复到本地；旧验证文件保留当时的证据可用性说明。原文拆分、导航和验证分别由以下脚本完成：

- `ops/prepare_course_nosplit.py`：生成新加工修订的资料，检查原稿哈希与正文。
- `ops/evaluate_course_nosplit.py`：确认单文件结构、读取全文并记录初始自动摘要。
- `ops/build_course_navigation.py`：从原文章节标题生成导航，不补写教学内容。
- `ops/publish_course_navigation.py`：更新已有目录摘要和概览，验证读回结果。
- `ops/test_course_retrieval.py`：使用固定问题对比两版结果。
- `ops/tag_course_units.py` 和 `ops/test_course_typed_retrieval.py`：设置资料类型标签，并对照过滤后的find和search。
- `ops/finalize_course_nosplit.py`：生成地址映射及分项验证结论。

上述脚本针对本次课纲和加工修订，尚不是任意书目的通用导入流水线。CLI回执标签不可覆盖；遇到超时或不确定结果，应先核对任务和实际内容。

重新导入或执行语义重建，可能重新生成摘要。维护流程应在导入完成后重新生成、发布并检查导航，再生成接入映射；不能假定人工校正后的摘要永久不变。原稿、导航生成逻辑或加工选项改变时应采用新的加工修订，并保留旧回执。

原稿仍标注AI生成，业务审核、正式课程版本、书籍版本及真实班期课节映射仍待确认。知识层测试通过不代表这些业务项已经验收，也不代表已完成Hermes或服务Skill的运行绑定。

操作依据：[官方资源导入指南](https://docs.openviking.ai/en/api/02-resources)、[摘要正文更新规则](https://docs.openviking.ai/en/api/12-content)，并已通过本次企业版CLI回执核实实际支持情况。


## 初批导入与版本演进

2026年10月2日首批默认解析使用 `source-c764d8b325cc`，28个来源单元为20课、4份周总览及4份能力对标，生成45个正文片段。逐单元读取覆盖检查通过，6个限定范围的检索冒烟样例通过；该检查忽略空白和Markdown展示符号，保留文字、标点及表格行列关联，不等于云端正文逐字节相同。

初批已由本文的一课一份正文加工方式替代作为推荐接入入口，旧数据保留作对照。[初批地址映射](../build/openviking/courses/source-c764d8b325cc/access-map.json)、[初批验收](../build/openviking/courses/source-c764d8b325cc/validation.json)。初批脚本为 `ops/prepare_course_resources.py`、`ops/verify_course_resources.py`、`ops/finalize_course_resources.py`；两批共享 `ops/course_cli.py`。

来源快照来自原稿哈希，不能替代正式课程版本或真实班期课节映射。默认CLI包装器连接本地服务，企业版课程读取须显式指定本文配置；不能混用新旧快照或猜测正文地址。更新资料时创建新加工修订，任务结果未知先查原任务，不覆盖旧批次或重复提交。

## 2026年10月3日后续核验与回答实验

已恢复398份JSON文件，其中385条原始CLI回执；重新核验28份正文与原稿、生成文件一致，56份目录摘要与概览读回一致，四个导入任务完成。见[恢复核验](../build/openviking/experiments/20261003-catalog/recovered-verification.json)。这属于历史证据重算，不是本次清理重新调用云端验证。

[课程目录与回答对照实验](OpenViking课程目录与回答对照实验.md)补充比较语义检索与目录选课：保留语义检索，目录用作导航和补救；已知课次直接读取。原15题分数与后续实验评分标准分别保留。CLI解析已修复为读取输出末尾完整JSON，纠正记录不覆盖原回执。

来源资料的事实审核仍未被技术验收替代，具体问题见[来源质量记录](../build/openviking/experiments/20261003-catalog/来源质量待审核.md)。
