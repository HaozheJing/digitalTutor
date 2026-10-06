# OpenViking产品知识接入与维护

2026年10月3日。本批按照确认的“4份常用正文＋1份规划与历史参考”整理阅读营产品知识。业务原稿继续由`knowledge/product/阅读营产品知识库.md`维护，生成资料按完整章节提取，不改写产品事实，不补写未确认的价格、权益、功能状态或服务规则。

## 五份正文与配套索引

| 正文 | 来源章节 | 字符数 含Markdown | 用途 |
| --- | --- | --- | --- |
| 产品介绍与学习安排 | 1、2、3 | 2,289 | 产品定位、课程形态、课上课后方式、年课与月课边界 |
| 分级体系与选书介绍 | 4、5 | 4,812 | L0—L6整体对比、选书原则、代表书目及选篇范围 |
| 阅读能力与教学方法 | 6 | 1,232 | 五个能力维度、方法名称、策略发展方向；具体练法关联课程资料 |
| 产品功能与伴学服务 | 7、8、9 | 2,409 | 学习功能、报告、测评、伴学服务及内部沟通安排 |
| 规划与历史参考 | 10、13 | 1,449 | 规划功能和历史背景，按需检索 |

第11章的8个FAQ保留在`faq-intents.json`，包含原始问题、原文、来源行号和目标正文地址，作为本地问题入口，不额外形成云端答疑副本。第12章的10项待补配置保留在`issues.json`与可读维护清单中，另列6项使用时必须核实的条件。

所有来源内容分别在五份正文、FAQ索引或维护清单中保留。原稿350个非空正文行全部覆盖；总标题不计入该数量。云端只导入`content/`内的五份正文。

## 来源状态和内容边界

本产品文件没有提供业务确认人、正式产品版本、生效日期或当前功能核验日期，本批均保留为未知或待核实。没有套用课程原稿的“AI生成”标签；产品来源的生成方式未确认。

- “已上线”只记录为来源声称，不等于本次核实了客户端版本或所有账号权限。
- 年课240节、回放和伴学等配置不可直接套用月课或某个家庭的订单。
- 单节课时长存在多种描述，不能自动选一个作为统一口径。
- 代表书目不等于完整交付书单，也不证明随订单寄送实物。
- 四轮学情沟通是内部窗口安排，不是每个家庭都获得四次沟通的承诺；本批没有创建任何触达任务。
- 培养目标和学习参与记录不能代替个体能力证据。
- 方法名称索引不足以支持具体教学步骤，步骤应继续读取适用的课程知识。

这些条件随对应正文一起保留，不能只根据搜索摘要中的一句话作答。

## 目录和读取约定

加工修订`source-52e8a8289d17-merged-v1`来自源文件哈希与加工方式，不是正式产品版本。

接入使用[access-map.json](../build/openviking/products/reading-camp/source-52e8a8289d17-merged-v1/access-map.json)，业务查看可从[五份正文导航](../build/openviking/products/reading-camp/source-52e8a8289d17-merged-v1/导航.md)进入。待补内容见[维护清单](../build/openviking/products/reading-camp/source-52e8a8289d17-merged-v1/待补充与待核实.md)。

```text
build/openviking/products/reading-camp/source-52e8a8289d17-merged-v1/
  source/阅读营产品知识库.md
  content/
    core/
      overview/content.md
      levels-and-books/content.md
      reading-methods/content.md
      features-and-service/content.md
    reference/
      plans-and-history/content.md
  manifest.json
  navigation.json
  faq-intents.json
  issues.json
  待补充与待核实.md
  导航.md
  change-log.json
  retrieval-cases.json
  cli-records/
```

云端根地址：

```text
viking://resources/digitaltutor/products/reading-camp/source-52e8a8289d17-merged-v1
```

普通产品答疑限定根地址下的`core`，同时使用`knowledge_type=product,retrieval_scope=core`标签。规划或历史问题明确进入`reference`并切换对应标签。检索范围约定不是访问权限控制；如果调用方搜索整个上层目录，仍可能召回参考资料。

已知问题属于哪个主题时，通过稳定资料编号直接读取。未知主题先检索，随后读取正文与适用条件。FAQ和缺口索引供后端本地读取，不应假设它们已经成为独立云端资源。

每份单元目录的摘要和概览由原始章节导航生成并写回；文件级搜索摘要仍由服务端生成，不保证包含所有例外。接入应使用`access-map.json`中的导航、来源状态和实际正文地址。

## 本次实际验证结果

使用服务器OpenViking CLI 0.4.22连接既有企业版配置。导入任务`b65b7656-85fe-44dd-8518-5a39bdbc6b1c`已完成，五份文件均由MarkdownParser处理，没有失败、跳过或不支持的文件。云端正文数为5，每份全文与本地生成文件完全一致；10份目录摘要和概览写后读回一致，逐份标签设置成功，检索返回也携带所要求的标签。

| 检查 | 结果 |
| --- | --- |
| 常用主题检索 | 10题中9题首位命中，全部前两位命中 |
| 指定参考范围检索 | 规划、历史各1题，均首位命中 |
| 边界探查 | 4题，均未在默认范围中返回规划历史文件 |
| 来源完整性 | 五份正文、8个FAQ、10项待补配置均保留可追溯来源 |
| 本地回执复核 | 66份回传产物哈希通过，其中61条原始CLI回执 |

唯一首位未命中的问题是“西游记是不是会把整部原著全部讲完”：首位为产品介绍，第二位才是包含选篇精读条件的分级与选书资料。因此不能把首条命中直接视为答案。涉及整本与选篇交付范围时，应读取`PRODUCT-READING-CAMP-LEVELS`的第4.4节及对应级别书目；现有FAQ 11.6也指向这份资料。

价格与退款问题在本次默认范围未返回命中。豆包免费次数和入口问题会返回相似的常用功能资料，但没有可据此确认的次数和入口。这些结果只验证了检索行为，不能说明智能体已经学会正确拒答或追问。本次未向额外的答题模型发送产品材料，也没有执行自动答疑评分。

12个有目标主题的问题在查询前固定，属于来源知情的小样本检索探查，不是独立业务准确率，更不是与20—30份拆分方案的对照实验。规划历史的两题已预先指定参考范围，其首位命中不表示已验证模型能自动正确分流。

过程中先后修正了预检查错误JSON位于stderr的解析处理和任务查询命令，未重复导入；失败命令回执与恢复日志保留。所有完成结论均从成功任务和实际读回记录重新计算，不把失败记录改写为成功。

原始验证见[validation.json](../build/openviking/products/reading-camp/source-52e8a8289d17-merged-v1/validation.json)，本地重新计算见[local-verification.json](../build/openviking/products/reading-camp/source-52e8a8289d17-merged-v1/local-verification.json)。原稿未改动，业务审核、实时功能权限与订单权益仍待业务来源确认。

## 后续补充与再加工

业务人员可以先把新增材料放在产品目录的补充文件中，并尽量注明来源、确认人、适用产品、版本和生效时间。先判断它是补充原主题、替代旧口径，还是存在未解决的冲突；新文件时间不能自动决定覆盖关系。

第一版脚本仅处理当前这份原稿及其既定13章结构。它不会自动发现、合并其他补充文件；出现新章节也会要求重新核对映射，避免静默丢失内容。确认新口径后更新业务原稿，再运行加工脚本；原稿字节哈希变化会形成新的修订目录。加工逻辑改变但原稿不变时，需要提升`merged-v1`的加工修订号。

发布新修订前，应比较`source_sections`、FAQ关联和待核实清单，查看哪些稳定资料编号受到影响，再重跑相关检索问题。新旧云端修订并存时，调用方只选择一个生效修订，避免同时召回重复或过期资料。更换接入地址属于后续接入操作，本次不修改老师智能体配置。

## 脚本

```powershell
python -X utf8 ops/prepare_product_resources.py
python -X utf8 ops/verify_product_resources.py source-52e8a8289d17-merged-v1
```

服务器使用已配置的企业版CLI执行导入与验证：

```bash
runtime/openviking/bin/python ops/publish_product_resources.py source-52e8a8289d17-merged-v1
```

脚本先检查目标地址，导入使用`parse_mode:no_split`，保存导入任务、逐份读取、标签和摘要写回的原始回执。已有同标签回执会复用并核对参数；遇到超时或状态不明应先查询任务和实际内容，不能直接重新导入。

下载回执后，本地执行：

```powershell
python -X utf8 ops/verify_product_resources.py source-52e8a8289d17-merged-v1 --require-remote
```

操作参考：[OpenViking资源导入指南](https://docs.openviking.ai/en/api/02-resources)、[摘要与正文更新指南](https://docs.openviking.ai/en/api/12-content)。实际企业版支持情况以本批CLI回执为准。
