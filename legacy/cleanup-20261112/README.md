# 2026-11-12 Linux fastloop 整理

## 归档内容

| 位置 | 内容 |
| --- | --- |
| demo-tests/ | 四个演示专项测试，保留 app、adapters 相对路径 |
| unused-vendor/ | 未被当前 DSH 调用的旧 Hermes、回调实现及测试；媒体适配器保留在主目录 |
| reports/ | 原根目录的阶段、集成、测试及运行基线报告 |
| before/ | 调整前的文档、Git 状态、文件指纹和工作区补丁 |
| runtime/ | 历史实验、一次性 runner、日志、模型测试输出及其原回放指针和配置 |
| verification/ | 移动映射、文件保全核验、隔离回归结果及日志 |

历史 runtime 内的绝对路径保留原记录。恢复实验应先按移动映射在隔离环境适配路径。归档目录中可能包含私有实验配置，沿用受限权限，runtime 归档由 Git 忽略。

## 主代码与已有修改

整理前 HEAD 为 253503e124126e9a3f69318109ad34cc9e739a7b。保留已有工作区修改，Git 暂存区与提交历史由前后指纹核对。每个原源码文件按旧路径、归档路径或文档原文备份核验 SHA-256。历史 runtime 文件使用移动前后相同的设备号、inode、大小、时间和权限核验。

现有 runtime/live.sqlite 及原夹具继续保留，测试使用另建的隔离源码副本及匹配合成夹具。完整结果以 verification 下的文件为准。

## 本次验证

原有 819 个源码与材料文件完成内容保全核验，179,988 个运行文件或链接完成移动元数据核验。Git HEAD 和索引保持一致。保留 122 个回归测试文件、49 个 Skill。

249 个 Python 文件和 29 个 JavaScript 文件通过语法检查，媒体适配器可导入，48 个文档链接有效。完整隔离回归退出码为 1，详情见 verification/tests.json、python-full.log、node-full.log 和 baseline-comparison.json。隔离测试副本归入 verification/workspaces。

完整隔离回归执行 Python 1108 项、Node 71 项。Python 原始报告包含 10 个失败及 12 个错误，其中两项由隔离副本缺少两份归档规则文档造成，补齐后重跑通过。其余 20 项及 Node 的 5 项失败均在整理前源码副本复现。原始日志和对照结果完整保留。

生成的 Python 编译缓存另外移入 generated-cache，映射见 verification/cache-moves.json。
