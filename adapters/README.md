# 业务适配层

当前目录为 fastloop 的 Python 业务适配与 MCP 服务实现。

- mcp_server.py 注册工具，service_workflow.py 协调业务工作流，business.py 处理状态与交付约束。
- knowledge_search.py、course_navigation.py 处理知识查询，s01_delivery.py、weekly_materials.py 处理导读和周总结交付。
- preference_memory.py、memory_session.py 处理记忆，message_hooks.py 接入事件，family_lab_bridge.py 等模块提供当前仍被调用的家庭与人工交接适配。
- test_*.py 和 test_*.mjs 保留业务回归用例，完整发现入口在 ../app/run-tests.py。
- legacy/ 与 vendor/ 包含仍被当前实现导入的适配代码。

完整目录说明见 ../docs/DIRECTORIES.md。旧版实验说明保存在 ../legacy/cleanup-20261112/before/original/adapters/README.md。
