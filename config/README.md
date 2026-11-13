# 环境配置

公开仓库只保存配置说明和占位示例。账号密码、API key、授权 token、真实服务地址、联系人路由和带密钥的回调 URL 通过公司私有文档或凭据管理系统交接，不能写入公开配置、文档、截图或测试日志。示例与读取约定见 [部署配置与凭据边界](../docs/reference/deployment-configuration.md)。

Linux 入口 app/start.py 从 config/provider.json 读取 api_key，并使用项目 runtime/node-v22.21.1-linux-x64/bin/node、app/node_modules 中的 DSH 和独立 DSH_HOME。当前仓库未提供完整部署环境，需按实际环境提供依赖与配置。

业务工作空间还按功能提供 service-workflow.json、message-hooks.json、tool-surface.json 等配置，读取条件见 adapters/native_experiments.py 和 adapters/tool_surface.py。

离线回归入口 app/run-tests.py 校验 runtime/fixtures/mock_uid_00001_first_month.json 的 SHA-256，期望值来自 app/test-fixture-sha256.json。整理前现有夹具与期望值不同，该原文件继续保留。本次回归在隔离副本中使用经过校验的合成夹具和既有 DSH 依赖，日志与条件记录在 legacy/cleanup-20261112/verification/。
