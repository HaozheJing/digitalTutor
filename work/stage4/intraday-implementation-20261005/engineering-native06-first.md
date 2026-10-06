# native06 首闭合包工程复核

本次 independent_need、missing_guide 均为 test15 新实跑，各1 native/8 API/1实际 sent。每案83个运行源码/提示词 SHA 与 test15冻结189清单一致。4个闭合SQLite使用immutable只读，integrity_check均ok，读取前后SHA不变。详细逐项字段在 engineering-native06-first.json。

independent_need：实际mode=passive。msg-2fed9dccaa424736bff3297cae241907 / intent-independent-need-1 保留requires_reply与空finalrefs；partial operation-01faf26f86654308af3a55a3bbbaab1a 是业务/独立渠道一致的单次sent。pending:151a009cf79d4675cb141bf68e2445bbad0c30b653beec3647afbf67dd38580c 为原pair、v1/decision_revision1/await_decision=true，实际候选源与当前原文摘要、授权片段一致。原service waiting attempt1，当前outcome精确观察该pending；admission ready attempt0，没有执行Planner、没有Event/Plan/Report。直接调用本次候选helper重审真实闭合双库，全部未答pair准确覆盖，无豁免普通欠项；新postcheck不是靠allow_unanswered放宽而变绿。

missing_guide：实际mode=full。真实 skills.read 返回 SKILL_MISSING；msg-80b9c9b7a8904e8f8e1f3adab53ab399 / intent-l2-day1-peidu 仍未答，partial operation-7c054ecc9772497e8206b5d27657852d 单次sent与渠道一致。仅原service waiting，无daily/progress job，无planning_requested、Event/Plan/Report。此普通缺资料没有被新待决定规则错误算作coverage，而是按原缺资料合同保留责任。

两案本次限定工程核对通过；内容真实性由语义另审，源恢复后补答、接纳后正式确认及最终履行不属于首包覆盖。整体4C门槛不预签。
