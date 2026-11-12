# Linux 项目目录

本页数量采用 2026-11-12 工作区整理快照。单独整理提交与尚未提交业务变化的关系见 [技术说明](DEVELOPER-GUIDE.md)。

| 目录 | 内容 |
| --- | --- |
| app/ | DSH 插件、上下文与记忆、工具面、定时唤醒、启动和 Node 回归测试 |
| adapters/ | Python 业务、MCP 工具、状态与回执、知识与媒体处理、人工交接，以及业务回归测试 |
| skills/ | 49 个服务方法目录及附件、能力声明 |
| ops/ | 四个课程知识读取、查询工具、发布和隔离评估脚本 |
| assets/ | reading-guides 的 20 份导读素材，weekly-review 的 4 份周总结材料 |
| knowledge/ | curriculum/L2/catalog.json 公共课程坐标、书目和材料索引 |
| docs/ | 文档入口、目录和开发指引；design 为业务设计，reference 为接口与动作维护规范，figures 为配图 |
| config/ | 当前环境配置说明，实际密钥和工作空间配置按环境提供 |
| runtime/ | 现有数据库、夹具和本机运行输出。历史实验及其回放指针已整体归档 |
| legacy/ | 原有历史版本，以及 cleanup-20261112 下本次归档的演示测试、旧 vendor、历史报告、旧文档和运行实验 |

有效测试继续与 app、adapters 内实现相邻。四个演示专项测试移入 legacy/cleanup-20261112/demo-tests。vendor 仅保留当前媒体调用使用的 digital_tutor/ark_media.py 及包初始化文件，其余旧后端与回调实现归入 unused-vendor。

家庭隔离与人工交接的适配仍由当前实现调用，相关代码和回归测试继续维护。此前的五家庭演示目录位于本项目之外。

归档记录见 [本次整理记录](../legacy/cleanup-20261112/README.md)，完整回归入口见 [开发指引](development.md)。
