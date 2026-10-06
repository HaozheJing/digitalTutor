# 第三阶段授权内部材料联测独立验收

日期：2026-10-04。用户已明确授权第1课正文、`explain-lesson`、`plan-proactive-service`及必读规范用于既有模型服务测试。仅使用虚构家庭与学情。本验收者未修改后端源码或原规范，未重新调用模型/API。

## 结论

指定内部材料联测通过。独立验收实际执行31项只读断言，全部通过，结果保存为：

`evaluations/backend-stage3/stage3-materials-20261004/independent-review.json`

该文件已从服务器下载本地。验证脚本为`work/design-inputs/verify_stage3_materials_readonly.py`，不在backend目录，不改变49份冻结源码清单；脚本只读原运行产物、项目材料与SQLite，只写新的独立验收结果。

## 已实际核对

| 项目 | 证据与结果 |
| --- | --- |
| 3次真实模型运行 | a1/daily/progress均完成并passed，模型qwen3.8-flash、材料工具开启、输出路由internal |
| 源码版本 | 3case各自49份源码SHA256清单完全一致，逐文件重算与当前服务器一致 |
| trace真实性 | 每case内嵌tool_trace与其独立运行目录tools.jsonl逐项完全一致；3个profile/cwd独立且都在本次state根目录 |
| a1准确Skill | 成功调用参数digest精确对应`explain-lesson`；完整返回digest与本地原文、全部必读guides、内容指纹及V1约束重算完全一致 |
| daily/progress准确Skill | 两者均实际成功读取`plan-proactive-service`，并在各自commit之前；完整返回digest重算匹配，补足原probe缺少的显式Skill断言 |
| a1准确课节 | 成功参数digest精确对应`LESSON-L2-FOX-D01`；用固定manifest和本地生成正文离线重算完整知识返回digest，与原运行完全一致；读取早于模拟发送 |
| 材料范围 | 所有成功Skill/知识读取均为本次批准ID，无相邻课或额外Skill正文读取；课程simulation绑定和未核实来源属性保持原样 |
| 真实业务记录 | 只读SQLite验证1条synthetic-A模拟sent操作、1份synthetic-P计划及1个原目标；目标状态in_progress、原课节/完成标准/剩余工作保持，报告applied |
| 提交确实落库 | send、daily commit、progress commit各自trace中的完整结果digest匹配持久化事务receipt，未用模型最终文字替代成功结果 |
| 家庭和渠道 | 业务库仅3个已知synthetic家庭、mock uid及mock收件人；学情仅mock-uid-A/B；操作channel=mock_wechat且simulated=true，无真实学员或真实微信 |
| Hermes | 提交仍为f97608f178d1ffeca59860195ab7da295f7c8e5f；现场git status为空 |

离线重算调用SkillCatalog的本地reader依赖注入和KnowledgeCatalog的本地正文client，没有启动Hermes循环、OpenViking CLI或任何网络请求；用于比较原调用的确定性结果摘要。原运行调用事实来自各run实际trace与冻结桥接源码。

## 实际模拟答复语义核对

SQLite中唯一模拟发送正文为：“已为您查询：学习记录共有 3 天。”

该答复仅陈述SQL记录行数，与已验证的3条本家庭学习日记录一致；没有把读取的第1课资料描述为该家庭正式课程绑定，也没有把学习参与推断为理解或能力提升。没有额外营销、催课或跨家庭信息。

## 通过范围

本轮补齐此前待授权的指定材料进入真实模型与业务工具联测分支。通过不代表41个Skill全部完成业务语义验收，不代表未确认来源已获正式业务审核，也不代表第四阶段完整两日真实模型服务或真实微信已完成。没有扩大到其他材料或新API调用。
