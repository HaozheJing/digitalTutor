# digitalTutor

基于 DeepSeek Harness（DSH）的 Linux 阅读辅导智能体。当前项目位于 /home/ubuntu/digitalTutor-fastloop-dev-20261107，依赖 @deepseek-ai/dsh 0.2.1-rc.2。

从 [文档入口](docs/README.md) 开始阅读，[目录说明](docs/DIRECTORIES.md) 列出各目录职责，[开发指引](docs/development.md) 按维护任务定位代码。

| 内容 | 位置 |
| --- | --- |
| DSH 宿主与模型交互 | app/ |
| 业务逻辑、工具和回执 | adapters/ |
| 服务方法及参考材料 | skills/ |
| 公共课程索引与维护 | knowledge/、ops/ |
| 导读和周总结材料 | assets/ |
| 本机环境与状态 | config/、runtime/ |
| 历史实现、演示测试和实验记录 | legacy/ |

启动入口为 app/start.py，完整离线回归入口为 app/run-tests.py。环境要求见 [配置说明](config/README.md)。回归测试与实现相邻，保留 app 和 adapters 的原有发现方式。

2026-11-12 整理前的 HEAD 为 253503e124126e9a3f69318109ad34cc9e739a7b，工作树包含后续开发修改。归档映射、文件保全结果和当次验证记录见 [Linux 整理记录](legacy/cleanup-20261112/README.md)。
