# 第三阶段学情输入交付说明

日期：2026-10-03。此说明记录学情输入子模块，服务器统一集成验收由主控负责。

## 实现与兼容

- `learning.py`：仅负责家庭范围内的 SQLite 只读投影；`from digital_tutor.learning import import_workbook` 保持兼容。
- `learning_import.py`：原工作簿只读导入、字段验证、原表与扩展来源的原子写入。
- `source_rows.py`：独立、可重复执行的附加表初始化，不修改既有 learning_days/assignments 表结构，不改变业务库 user_version。
- `course_binding.py`：宿主确认课程、课节、覆盖时间和源时区；未注册为模型工具。
- `tests/test_stage3_learning.py`：10 项虚构八表数据回归。

导入哈希仍为 batch_id。新导入同时写入全部来源；相同批次再次导入返回 duplicate。旧数据库在没有源文件时仍能查询第一阶段记录；重新提供同一源文件时返回 upgraded，只补扩展来源并保留原始导入时间、原业务记录与旧学情字段。所有扩展写入处于同一短事务，失败不留下半个批次。不自动创建家庭或模拟聊天历史。

## SQL 工具新增可读表

`PUBLIC_SCHEMA` 已扩展，既有 learning.schema / learning.query_sql 自动取得这些表，无须新增模型写工具。

| 表 | 范围及关键字段 |
|---|---|
| learning_source_rows | 8 个 sheet 的当前 uid 原始数据行；source_group、headers_json、values_json、metadata_json、evidence_locator。保留宽表尾部统计、月日、W编号和题目元数据。 |
| learning_summaries | 汇总中的 5 类指标；保留 raw_value、source_group，拆分 numerator/denominator；解析失败保留 null 和质量标记。 |
| assignment_details | 与 assignments 按 batch_id/source_row/uid 关联；补齐分组、AI表达题类型和名称、明细来源坐标。 |
| homework_days | 作业-按天独立来源；教学日、原分组、原状态、标准化状态、月日/W标签、题目、冲突标记及单元格。 |
| course_bindings | 可信宿主对当前家庭/导入批次确认的课程、版本、班期、级别、lesson_map_json、覆盖时间、源时区、依据、确认时间及修订版本。 |

原有 3 张表、列名和只读查询合同保持不变。新增表同样先物理投影当前 uid，省略 WHERE、UNION 或显式查询其他 uid 都不能读取其他家庭。源表与课程确认分开：learning_batches 中未知的源元数据保持 null，不能把后来宿主确认伪装成原文件字段。

建议按需读取：

```sql
SELECT a.day, a.status, a.answer_text, d.ai_expression_type,
       d.ai_expression_name, d.source_group, d.evidence_locator
FROM assignments AS a
JOIN assignment_details AS d
  ON a.batch_id=d.batch_id AND a.uid=d.uid AND a.source_row=d.source_row
WHERE a.batch_id=:batch_id AND a.day=:day
```

## 宿主课程绑定接口

Python 入口：`confirm_course_binding(app, admin_token, args)`。参数包含 request_id、batch_id、expected_family_version、source_note；可选择确认 course_id、course_version、cohort_id、level、lesson_map、coverage_through、source_timezone。

lesson_map 为 `[{"day":106,"lesson_ref":"已确认的课节标识"}]`。教学日不能重复或超出当前家庭在该批次的记录；有映射必须同时有课程和内容版本。coverage_through 必须包含时区，source_timezone 必须为可识别 IANA 时区。未确认的信息保持 null，不能由学习日期范围、文件修改时间或分组标签推断。

这是可信宿主/admin 写接口，service/planner 调用被拒绝，模型 ToolRegistry 不注册它。模型通过只读 SQL 获取确认结果。字段省略保留旧值，显式 null 清空，两者具有不同幂等请求摘要；原记录修订进入 history。课程身份或版本改变且没有提供新映射时，旧映射自动清空并记录 mapping_reset_reason，防止旧课映射被套到新课程。

## 已完成验证

- 本地第三阶段学情回归：10 项全通过，包括八表、字段来源、家庭隔离、旧DB补齐、失败回滚、unknown、分组冲突、host权限、幂等、CAS、课程版本更正及历史。
- 本地第一阶段回归：33 项全通过，包括用户表格只读导入。
- 真实表格聚合：15 uid、3600 教学日、405 作业明细；8 sheet、510 原始学员行、75 汇总指标、405 AI题型/名称记录、405 按天作业记录。
- 质量标记仅为已知的 405 条题干缺失和 3 条提交次数矛盾；原始断言未自动修正。
- 源 SHA-256 保持 `c03747180148e8d418a55eb4b9cd3a1bb1b50ca5db883e50f4d3008fb145327d`。

上述输出未打印真实 uid 或用户回答。真实课程绑定仍待提供；当前所有绑定回归均使用虚构值。服务器集成、真实模型读取新表及文档/工具元数据更新需要由主控在最终阶段三验收记录中一并登记。
