# Linux 演示代码与测试整理

本轮归档目录为 legacy/demo-code-cleanup-20261112。

## 已归档

| 文件 | 原用途 |
| --- | --- |
| family_lab_start.py | 五家庭演示进程启动器 |
| manual_workbench_start.py | 手动微信测试工作台启动器 |
| reply_lab_server.py | 独立回复实验台启动器 |
| reply_lab_seed.py | 回复实验候选和场景种子脚本 |
| render_report.py | 固定读取 2026-10-20 实验输出的旧 HTML 报告脚本 |
| p0_acceptance.py、p0_actions.py | 仅指定集成工作区可启用的 P0 演示验收入口及动作 |
| app/test_reply_voice_ui.mjs | 针对已不存在的 reply-lab.html/js 的旧界面测试 |

同时移除 teacher_workbench 中 P0 专用分发和页面映射。主业务、回复实验 API、发布权限和人工交接处理继续沿用现有实现。

两个混合测试文件分别处理。test_skill_studio 中已不存在页面的 DOM 控件测试归档；test_reply_lab 中移除旧页面存在性断言，保留并单独执行 HTTP API 未认证请求被拒绝的断言。其余测试逻辑保持原样。原测试文件与抽取方法分别保存在归档 before/ 和 tests/retired-ui-cases.json。

## 继续保留

- family_lab、family_lab_bridge、family_lab_contacts、family_lab_cost、family_lab_server 及相关模块承担家庭隔离、发送回执、人工操作、费用或共享控制。family_lab_round/setup 仍由有效的联系人迁移测试依赖。
- manual_workbench、manual_media 用于真实渠道权限、时钟授权和固定素材发布。
- experiment_runtime、experiment_dialogue_context、experiment_result_mcp、experiment_phase_mcp、experiment_media、experiment_parameter_proxy、native_experiments、app/experiment-route.mjs 仍被宿主、媒体、语音、知识评估或原生回归使用。
- reply_lab、teacher_workbench、skill_studio 与相关子模块承担现有权限、版本、场景、媒体和模型测试接口。reply_lab_presets 为 S07/S08 业务回归生成合成场景。
- test_mode.py、test_scheduler.py、wechat_test.py 是宿主调用的时钟、调度和交付实现，继续维护。

具体调用位置、已有文件指纹、移动映射及验证结果保存于归档 verification/。归档脚本保留原字节和原始相对路径假设，恢复演示时应按对应版本在隔离环境恢复依赖。


## 本轮验证

原有 416 个受检文件的内容已核对到主目录、原文件备份或归档位置。Git 提交与暂存条目保留，现有数据库和夹具在对照回归前后保持一致。

对照回归覆盖教师工作台、Skill 工作台、HTTP 认证、内部草稿、缺失资料转人工、家庭隔离、联系人绑定、手动渠道、工具约定、服务时段及实验运行控制。

Python 整理前 183 项、3 个错误；整理后 182 项、1 个错误。剩余错误为原有 Pillow 缺失，未新增失败。旧页面断言归档后，原有 HTTP API 未认证拒绝检查独立通过。

Node 整理前 71 项、66 项通过、5 项失败；整理后 70 项、66 项通过、4 项失败。减少的一项是已归档的旧回复界面加载失败，其余失败在前后两版一致。

保留 121 个测试文件和 49 个 Skill，242 个 Python 文件及 28 个 JavaScript 文件通过语法检查。具体结果和日志见 verification/final.json、regression.json 以及 before/after 测试日志。
