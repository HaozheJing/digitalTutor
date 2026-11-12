# 开发阅读指引

当前主代码为基于 DeepSeek Harness 的 fastloop Linux 实现。仓库层级见 [目录说明](DIRECTORIES.md)，环境要求见 [配置说明](../config/README.md)。

## 按维护任务查代码

| 维护任务 | 主要入口 |
| --- | --- |
| 宿主插件、模型事件和工具面 | [tutor-plugin.mjs](../app/tutor-plugin.mjs)、[tutor-tool-surface.mjs](../app/tutor-tool-surface.mjs) |
| 业务工具注册 | [mcp_server.py](../adapters/mcp_server.py)、[tool_surface.py](../adapters/tool_surface.py) |
| 服务工作流和业务状态 | [service_workflow.py](../adapters/service_workflow.py)、[business.py](../adapters/business.py) |
| 唤醒、排期和服务轮结束 | [workflow-schedules.mjs](../app/workflow-schedules.mjs)、[service-turn-completion.mjs](../app/service-turn-completion.mjs) |
| 业务方法、路由和表达规范 | [skills](../skills/)、[service_catalog.py](../adapters/service_catalog.py)，业务设计见 [服务方法与人格](design/service-method.md) |
| 课程检索、课程坐标和公共知识 | [knowledge_search.py](../adapters/knowledge_search.py)、[course_navigation.py](../adapters/course_navigation.py)、[课程知识入口](../knowledge/README.md) |
| 课程知识读取与发布 | [curriculum_knowledge.py](../ops/curriculum_knowledge.py)、[publish_curriculum_knowledge.py](../ops/publish_curriculum_knowledge.py) |
| 导读和周总结素材 | [素材说明](../assets/README.md)、[s01_reading_guides.py](../adapters/s01_reading_guides.py)、[weekly_materials.py](../adapters/weekly_materials.py) |
| 家庭记忆 | [memory-agent.mjs](../app/memory-agent.mjs)、[preference_memory.py](../adapters/preference_memory.py) |

理解一次业务处理时，可先读 [服务流程](design/service-flow.md)，再沿对应宿主入口、工具、业务状态和交付回执查代码。处理资料引用问题时，结合 [知识库设计](design/knowledge.md) 核对课程坐标、来源版本和正文覆盖范围。

## 启动与回归

Linux 启动入口为 [app/start.py](../app/start.py)。该入口从项目目录读取 provider 配置，并使用环境中提供的 Node 和 DSH。具体路径与配置要求见 [config/README.md](../config/README.md)。

完整离线回归入口为 [app/run-tests.py](../app/run-tests.py)，在仓库根执行。

```bash
python -B app/run-tests.py
```

入口先核对合成夹具 SHA-256，再执行 adapters 下的 Python unittest 和 app、adapters 下的 Node 测试。结果写入 runtime/test-results/python.log 与 node.log。测试包含 Linux 文件锁、权限和符号链接行为，完整回归应在配置好依赖的 Linux 隔离环境运行。

测试文件与实现相邻。修改宿主行为时查看相应 app/test_*.mjs，修改业务工具时查看相应 adapters/test_*.py。历史演示测试位于 [演示测试归档](../legacy/cleanup-20261112/demo-tests/)，整理时的验证结果见 [2026-11-12 整理记录](../legacy/cleanup-20261112/README.md)。

工具与配置约定见 [工具与 Skill 契约](reference/tool-skill-contracts.md)，编写业务动作前阅读 [动作编写规范](reference/action-authoring.md)。

原有夹具与回归清单不同。本次提供的匹配合成夹具另存于 runtime/fixtures/regression-approved/，在隔离副本中放到测试入口要求的位置。已有数据库和原夹具继续保留。
