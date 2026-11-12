# 环境配置

Linux 入口 app/start.py 从 config/provider.json 读取 api_key，并使用项目 runtime/node-v22.21.1-linux-x64/bin/node、app/node_modules 中的 DSH 和独立 DSH_HOME。当前仓库未提供完整部署环境，需按实际环境提供依赖与配置。

业务工作空间还按功能提供 service-workflow.json、message-hooks.json、tool-surface.json 等配置，读取条件见 adapters/native_experiments.py 和 adapters/tool_surface.py。

离线回归入口 app/run-tests.py 校验 runtime/fixtures/mock_uid_00001_first_month.json 的 SHA-256，期望值来自 app/test-fixture-sha256.json。整理前现有夹具与期望值不同，该原文件继续保留。本次回归在隔离副本中使用经过校验的合成夹具和既有 DSH 依赖，日志与条件记录在 legacy/cleanup-20261112/verification/。
