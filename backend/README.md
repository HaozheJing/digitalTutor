# 教辅后端 0.3.0

> 当前执行状态（2026-10-04）：隔离K实现及冻结验证结束，361项服务器程序测试通过，32个真实Flash案例完成独立评审；4B/4C关口未通过。反复出现未来通知承诺和教学方法来源错误，另有恢复计划缺下一步动作，按主方案第10条第4项暂停受影响分支并提交取舍。主目录H及亲测数据未替换，不进入4D。详见《第四阶段4C隔离K交付与待决记录.md》。


开发和验收在服务器 `/root/autodl-tmp/digitalTutor`，本地 `backend/` 是同步源码。业务环境位于 `runtime/backend/`，各次测试状态写入独立的 `state/backend-stage*/<run>/`，证据写入 `evaluations/backend-stage*/<run>/`。

现有能力：SQLite业务核心、8表学情SQL、模拟微信工具、家庭串行调度和租约、发送恢复、三种角色上下文、家庭记忆、固定范围知识读取、Hermes原生工具/Skill/会话/取消/cron适配。**生产微信尚未接入**；sent/delivered仅为模拟渠道回执。真实课程、覆盖时间和正式触达频控未确认的部分保留unknown或simulation。

阶段二服务器59项和两日模拟已通过。阶段三冻结复验93项程序测试、纯虚构输入的真实API六场景及原生材料读取全部通过，见 [验收汇总](../evaluations/backend-stage3/stage3-final-20261003/acceptance.json) 和同目录源码指纹。旧API证据保存在 `evaluations/backend-stage3/synthetic-api-20261003/`。

## 服务器运行与验证

```bash
cd /root/autodl-tmp/digitalTutor
bash backend/scripts/prepare.sh
export PYTHONPATH="$PWD/backend/src"
PY=runtime/backend/bin/python

# 所有程序测试；显式提供样本路径以执行只读工作簿集成检查。
DIGITALTUTOR_TEST_WORKBOOK="$PWD/data/表格数据.xlsx" "$PY" -m unittest discover -s backend/tests -v

# 第一阶段基线：创建新演示目录、业务示例和独立学情导入。
bash backend/scripts/run-stage1.sh
```

`prepare.sh` 使用独立依赖环境。测试使用虚构家庭和临时数据库；原表按用户授权存于 `data/表格数据.xlsx`，只读且不写入Git。验收清单记录服务器、源码SHA256和状态目录，不打印真实uid或作答原文。不要把某次测试通过挪用给不同源码修订。

直接创建演示库时使用新的路径，已有库不会被演示清空：

```bash
"$PY" -m digital_tutor --db state/backend-stage1/manual-new.sqlite demo
```

配置支持 `--config backend/config.example.json`；默认Asia/Shanghai、08:00≤常规外发时间<21:00。`--now` 只用于宿主模拟时钟。

## 微信全部、最新和增量读取

先从阶段一验收取得虚构演示库，再调用工具：

```bash
DB="$("$PY" -c 'import json; print(json.load(open("evaluations/backend-stage1/latest.json"))["state_root"] + "/demo.sqlite")"
"$PY" -m digital_tutor --db "$DB" call --family family-demo wechat.read_messages --arguments '{"mode":"all"}'
"$PY" -m digital_tutor --db "$DB" call --family family-demo wechat.read_messages --arguments '{"mode":"latest"}'
"$PY" -m digital_tutor --db "$DB" call --family family-demo wechat.read_messages --arguments '{"mode":"latest","limit":10}'
"$PY" -m digital_tutor --db "$DB" call --family family-demo wechat.read_messages --arguments '{"mode":"since","after_seq":0,"limit":100}'
```

- all按入库序号升序返回当前家庭全部消息，超过10000条明确报错，使用增量分页，不静默截断。
- latest默认最新1条，可设limit；“最新”以入库序号判断，同时保留发生时间和接收时间，has_older表示还有更早记录。
- since用after_seq开始，使用返回next_cursor继续；has_more表示尚有下一页。
- direction可为all/inbound/outbound，过滤方向后保持相同方向继续分页。
- 读取不标记已答；消息意图与实际回复操作关联，未知发送不登记已回答。

## 学情SQL与8表来源

```bash
"$PY" -m digital_tutor --db "$DB" call --family family-demo learning.schema
"$PY" -m digital_tutor --db "$DB" call --family family-demo learning.query_sql --arguments '{"sql":"SELECT day,completion_status FROM learning_days WHERE day>=:start ORDER BY day","parameters":{"start":2}}'
"$PY" -m digital_tutor --db state/backend-stage3/learning-manual.sqlite import-workbook data/表格数据.xlsx
```

| SQL投影表 | 用途 |
| --- | --- |
| learning_days | uid/批次/教学日的到课、完课、作业、伴读、打卡及来源 |
| assignments | 作业提交与次数、作答、AI引导、轮数/语音数、质量标记 |
| learning_batches | 原快照元数据；导入时间不等于覆盖时间 |
| learning_source_rows | 8个sheet的本uid原始数据行、表头、日期/题目元数据、分组和坐标 |
| learning_summaries | 5类汇总原值、分子分母、分组及冲突标记 |
| assignment_details | AI表达题类型/名称、分组及明细来源 |
| homework_days | 作业-按天的配置、月日/W标签、题目、状态和单元格 |
| course_bindings | 可信宿主确认的课程/版本/课节映射、覆盖和时区 |

这是实际执行的参数化SQLite SQL。宿主先物理投影当前家庭uid，省略WHERE或使用UNION也读不到其他家庭。禁止写入、ATTACH、PRAGMA、数据库元数据、扩展加载、多语句和非白名单函数；限制执行预算、行数和大小，truncated明确标记截断。

作业扩展可按batch_id、uid、source_row关联：

```sql
SELECT a.day, a.status, d.ai_expression_type, d.ai_expression_name,
       d.source_group, d.evidence_locator
FROM assignments AS a
JOIN assignment_details AS d
  ON a.batch_id=d.batch_id AND a.uid=d.uid AND a.source_row=d.source_row
WHERE a.batch_id=:batch_id AND a.day=:day
```

同文件哈希即同batch_id；重导返回duplicate，旧数据库缺扩展来源时返回upgraded，仅补来源并保留原记录与导入时间。不自动创建家庭或拼接模拟聊天。不同批次不能直接累加为独立学习成果；unknown、not_configured、零和null分别保存，3条原提交次数矛盾不自动改值。

真实uid与家庭由可信业务系统/管理员绑定。CLI bind-family是宿主管理命令，不是模型工具，当前仅支持mock渠道。`confirm_course_binding(app, admin_token, args)` 是Python宿主入口：要求当前家庭/批次关联、最新版本和来源说明；字段省略保留原确认，显式null清空，历史可追溯。课程身份或版本变化且未给新映射时旧lesson_map清空。源时间标签、分组和文件修改时间不能推断真实课程或覆盖范围。

## 22工具和两种调用协议

完整用途、角色、模型和返回值见 [Tool元数据目录](../docs/Tool元数据目录.md)。规范工具名包括原14个业务工具，以及memory.read/save/delete、context.read、knowledge.list/read、skills.list/read。

CLI tools返回当前角色Schema，serve提供持久JSON Lines：

```bash
"$PY" -m digital_tutor --db "$DB" tools --family family-demo --role service
"$PY" -m digital_tutor --db "$DB" serve --family family-demo --role service
```

逐行输入：

```json
{"method":"tools/list"}
{"method":"tools/call","name":"wechat.read_messages","arguments":{"mode":"all"}}
{"method":"tools/call","name":"context.read","arguments":{}}
```

宿主启动时绑定家庭和角色，请求不能自行更换。普通来信走service；daily是唯一正式新事项入口；progress固定绑定report_id/revision，精确消费关联原事项，不扩目标、不改完成条件或计划排序。admin并非所有业务写入的通行权限。

Hermes原生注册采用 `dt_` 别名，例如 `dt_learning_query_sql`、`dt_wechat_send_message`，内部仍调用/审计点号规范id。`hermes_tools.py` 仅注册当前会话工具；`hermes_host.py` 每运行创建独立profile/工作目录/原生会话，复用未修改的AIAgent循环。取消使用原生interrupt及能力撤销，必要时只清理本次进程组，不干预共享服务。

knowledge仅读宿主批准的manifest版本和资料编号，原文指纹必须相符，缺课不读相邻课替代。skills通过原生skill_view(preprocess=False)读批准Skill，核对来源路径和正文，并完整加载必读规范。默认context明确标识未加载材料，不能把目录摘要当正文证据。记忆保留来源、范围、发生时间和有效期；过期不读、迟到旧消息不覆盖新偏好，暂停始终查Control。

## 调度、发送和未知恢复

`Scheduler.tick()`只创建当前业务日的daily与有依据的step/review任务；普通来信优先。`JobQueue`用短事务领取，家庭串行；Worker宿主心跳续租，generation及token隔离过期/停用前旧运行。空结果不等于报告消费、问题答复或事项完成。

Hermes原生cron可作为唤醒入口，业务队列仍负责家庭身份、事务和恢复，不替代Hermes模型循环。当前没有默认启动生产常驻调度或开放HTTP服务。以下探针只在新的隔离profile验证原生唤醒：

```bash
PYTHONPATH="$PWD/backend/src" vendor/hermes/venv/bin/python backend/scripts/probe_native_cron.py
```

模拟发送需最新expected_family_version、request_id、正文、动作及reply_to或event_id；交付另带part_id，受控提醒可带step_id。准备事务预占额度，queued/unknown也计入。**频控为simulation配置，尚不是业务确认的正式触达政策。**

发送发起前重新检查窗口、停用、暂停、租约、家庭输入、事项与今日计划。准备后变化会取消未发起操作；只有cancelled且dispatch_count=0可新请求重准备，旧记录保留。相同义务换请求号/正文不能绕过去重；unknown/dispatching/accepted查询原operation和原渠道，不盲目重发。

业务库与模拟渠道账本分离，覆盖“渠道已执行但本地未落回执”的故障。回执与同步返回使用单调合并，delivered不被迟到sent/unknown降级。停用或21:00后仍允许查询已发起操作；内部review会话不取得外发权限。

暂停催课不阻断家长新问题。目标型事项需要真实课节证据；交付型需全部部分满足原sent/delivered要求。提醒不等于补课完成，模型最终文本默认只走内部通道，必须调用发送工具才能形成模拟外发。

## 验收边界和真实API

[第二阶段59项及两日证据](../evaluations/backend-stage2/20261003T141601255327Z/manifest.json)对应已记录修订。第三阶段冻结版已完成纯虚构API的a1→b→a2家庭隔离、daily、progress、cancel六场景：实际SQL、模拟发送回执、原目标保留与报告消费、interrupt及RUN_REVOKED。最终[验收汇总](../evaluations/backend-stage3/stage3-final-20261003/acceptance.json)已保存93项回归、逐场景结果和对应源码指纹。

服务器本地3个原生Skill、2份OpenViking课/产品资料读取探针已通过。2026年10月4日用户已明确授权指定第1课、explain-lesson / plan-proactive-service及必读规范送现有maas.qianwenaiapi.com的qwen3.8-flash。以`--materials`运行service、daily、progress三条链路均通过，见[材料补充验收](../evaluations/backend-stage3/stage3-materials-20261004/acceptance.json)。家庭、消息与学情仍全部虚构。默认exclude_materials保持开启；本次授权测试显式启用指定材料，未启用生产触达。

以下为纯虚构探针示例，选未使用的run名称，按顺序运行；默认不加载内部材料，不添加--materials：

```bash
"$PY" backend/scripts/probe_native_model.py init --run my-synthetic-check
"$PY" backend/scripts/probe_native_model.py a1 --run my-synthetic-check
"$PY" backend/scripts/probe_native_model.py b --run my-synthetic-check
"$PY" backend/scripts/probe_native_model.py a2 --run my-synthetic-check
"$PY" backend/scripts/probe_native_model.py daily --run my-synthetic-check
"$PY" backend/scripts/probe_native_model.py progress --run my-synthetic-check
"$PY" backend/scripts/probe_native_model.py cancel --run my-synthetic-check
```

API凭据由服务器配置读取，不写入命令、普通日志或文档。模型额度开放不改变资料外发范围。程序测试、原生本地探针、纯虚构真实API、内部材料真实API和真实微信分别验收；指定已批准材料链的技术验证见第四阶段交付记录；真实微信仍未开放。业务语义质量、真实课程适用性及正式灰度上线仍需后续阶段确认。


## 第四阶段被动运行与人工亲测入口（2026-10-04）

历史max E版本4A/4B1/4B2有独立验收记录；当前Flash G为240回归和4B1工程通过，回答内容待用户共同评审，Flash 4B2尚未复验，当前4B未签署通过。用户最新停止点为4B1、4B2均通过后暂停亲测，不进入4C。实际冻结版本、检查数、未覆盖及独立审查见docs/教辅后端第四阶段交付记录.md。

严格Worker默认passive，普通来信直接service；角色仍为service/planner，配方为service/planner_daily/planner_progress。strict与legacy仅允许qwen3.8-flash，错配拒绝启动且不回退max；目的地仍限maas.qianwenaiapi.com。材料pin只允许原获批Day1、explain-lesson、plan-proactive-service及原必读规范，未扩大正式Skill或真实家庭数据范围。vendor未改。

人工CLI使用专属state/backend-manual-demo/<run>和evaluations/backend-manual-demo/<run>；不能续跑验收库。首次指定新run，已有人工实例必须--resume，源码或批准材料变化后换新run，不覆盖旧证据。只输入虚构消息，通过同一带租约Worker/HermesHost与模拟渠道；未启用HTTP、常驻服务或真实微信。

```sh
cd /root/autodl-tmp/digitalTutor
PYTHONPATH=backend/src runtime/backend/bin/python backend/scripts/demo_passive.py --run my-passive-test --clock '2026-10-04T10:00:00+08:00'
PYTHONPATH=backend/src runtime/backend/bin/python backend/scripts/demo_passive.py --run my-passive-test --resume
```

交互终端逐轮输入虚构家长消息，/quit退出；也可多次--message传入各轮文本。模拟业务时钟通过共享文件传给实际发送进程；不传--clock的新实例使用北京时间实时时钟，常规外发窗口08:00至21:00（不含21:00）。晚间咨询保留未答，推进新日窗口需显式--clock；不修改服务器系统时间。退出码0为空闲或实际回复且责任完整，1为执行失败，2为等待/未答。脚本结束、草稿或工具成功不等于业务交付。

当前合成学情为a家庭第1天completed/submitted、第2天incomplete/not_submitted，绑定与覆盖未知时不能推断真实履约或能力；课程正文只可问批准Day1，其余缺资料如实保留责任。缺正文后的纯知识源恢复自动唤醒尚未验收。主动事项规划与完整progress不属于本次用户亲测开放范围。


最终4B冻结版本与独立双审见[交付记录](../docs/教辅后端第四阶段交付记录.md)，人工命令、隔离状态、可测范围及退出码见[亲测说明](../docs/第四阶段4B亲测说明.md)。严格Worker显式temperature=0.0；当前Flash 4B1回答内容待用户共同评审，未进入下一关口。


4B亲测入口编码补充：2026-10-04修复直接终端中文输入；UTF-8默认、GBK终端使用--input-encoding gb18030，坏字节不入业务。209回归及两种真实PTY问答通过，详见交付记录与亲测说明；原E证据保留，用户my-test-01不动，新脚本请用新run，继续暂停在4B。


## 4C独立开发与4B遗留联合计划（2026-10-04）

当前4C联合计划已保存；主干候选链尚未集成。worker.py现有daily选项不能单独当旧queued主动发送已被禁止的证据，先完成三层门禁验证。现有被动亲测入口和运行配置保持。

详见[联合实施计划](../docs/第四阶段4C独立开发与4B遗留修复计划.md)。
