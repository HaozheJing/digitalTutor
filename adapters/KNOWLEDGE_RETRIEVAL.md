# FAQ 知识检索链路

2026-10-20：将误放在答疑 Skill references 中的 FAQ 全文与索引移除。原始稿、309条问答的8份归组稿仍由项目 knowledge/faq/reading-camp 管理；Git 历史保留上一版副本。Skill 只保留答疑流程、取证和表达方法。

链路：用户问题 → answer-family-question → knowledge_search → OpenViking find → 按返回URI读取并核验正文SHA256 → 智能体核对语义、适用范围、审核状态与实时家庭事实 → 回复。工具调用及返回正文沿用现有工作台审计，不会因检索直接发送微信。

- `knowledge_search(query, scope="faq", limit=3)`：实际云端问题检索，读取匹配正文并返回文档ID、版本、URI、分数及业务状态。
- `scope="product"`：检索产品core范围；历史套餐reference不进入默认搜索。
- `knowledge_read(document_id)`：支持已登记的8份FAQ主题与5份产品资料，并保留原课程正文入口。
- `knowledge_catalog.json`：只有文档地址、哈希、版本与状态，无FAQ答案。固定选定版本，拒绝检索结果越界、旁路摘要、正文漂移。
- OpenViking异常、无匹配或版本变化明确返回状态，不回退到Skill中的旧FAQ全文。

现有FAQ修订 `source-1fb0918df52f-faq-8a790143` 在云端仍可实时检索和读取；309条来源归为93个服务问题组及8个产品引用。现阶段云端仅有内部问题导航，`approved_business_answers=0`，不是已审核标准答案库。工具原样保留此边界，`can_answer_directly=false`。产品资料同样标记未核实，政策、权益、时限与实际操作结果必须另取适用证据。

该次调整没有向云端发布新的标准答案，没有把全部原始话术改成已批准内容，也没有触发新的微信发送。
