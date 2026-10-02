# digitalTutor 远程服务器与 Git 交接

更新日期：2026-10-02（Asia/Shanghai）

用途：供后续开发、配置与排查会话复用。本文记录已核实的环境、操作入口和项目约定；具体工作范围以当次用户要求为准。

## 1. 新会话先读这里

项目是少年得到阅读营第二阶段教辅数字员工，当前先建设后端。已完成 Hermes 与 OpenViking 的环境部署及基本连接验收，教辅业务流程仍需后续开发。

| 项目 | 当前事实 |
| --- | --- |
| 本地项目目录 | `E:\Agent Project\digitalTutor` |
| 远程部署目录 | `/root/autodl-tmp/digitalTutor` |
| 私有项目仓库 | <https://github.com/HaozheJing/digitalTutor> |
| Git 远程 | `origin` → `https://github.com/HaozheJing/digitalTutor.git` |
| 默认分支 | `main`，已跟踪 `origin/main` |
| 本地 Git | 已初始化、提交并推送；编写本文前工作区干净 |
| 远程项目 Git | 部署目录本身尚未初始化为本项目 Git 仓库，也未配置本项目的自动拉取或部署 |
| Hermes | 0.21.5，固定 tag `v2026.9.24`，已运行 headless 后端 |
| OpenViking | 0.4.22，已运行，健康与就绪检查通过 |
| 模型 | 对话和理解先统一使用 `qwen3.8-flash` |
| Docker | 服务器未安装 Docker，尚未制作镜像 |

接手时先核对本地 `git status` 和远程 `scripts/status.sh`。不要将本文的历史验收当作任何未来时刻的实时健康证明。

**目录约定：后续远程源码、依赖、配置、数据、日志与缓存继续集中在 `/root/autodl-tmp/digitalTutor` 下，便于后续 Docker 化。** 不要重新使用上游默认安装路径将项目安装到其他目录。

## 2. SSH 连接

| 参数 | 值 |
| --- | --- |
| 主机 | `connect.westc.seetacloud.com` |
| SSH 端口 | `11385` |
| SSH 用户 | `root` |
| 操作系统 | Ubuntu 22.04.1 LTS，Linux x86_64 |
| 登录方式 | 已验证密码登录；密码由项目负责人独立提供 |
| 已核对的主机密钥指纹 | `SHA256:YFFffpHZJeEWfmNbSh7+lFkFouKticMqV3p6Rr278oM` |

```bash
ssh -p 11385 root@connect.westc.seetacloud.com
```

本文不包含 SSH 密码、模型 API Key 或 OpenViking 密钥。新会话如无可用 SSH 登录凭据，需由项目负责人提供；模型凭据已经保存在服务器，可在服务器上复用，不必重新写入聊天或代码。

本地有 `ops/remote_admin.py`，使用 Paramiko 执行 SSH 脚本和上传文件。登录密码从进程环境变量 `DIGITALTUTOR_SSH_PASSWORD` 读取；不要把它硬编码进脚本、命令文件或 Git。该工具依赖本地 Python、`paramiko`；仓库创建工具还依赖 `requests`，目前未制作统一的本地开发依赖锁。

## 3. 软件版本与连接关系

| 组件 | 已安装版本或配置 | 位置 |
| --- | --- | --- |
| Hermes Agent | 0.21.5 / `v2026.9.24` | `vendor/hermes/` |
| Hermes 源码提交 | `f97608f178d1ffeca59860195ab7da295f7c8e5f` | 第三方源码基线 |
| OpenViking | 0.4.22 | `runtime/openviking/` |
| Python | CPython 3.11.16 | `runtime/python/` |
| uv | 0.12.21 | `bin/uv` |
| Supervisor | 4.3.0 | `runtime/supervisor/` |
| Hermes 配置版本 | 46 | `state/hermes/config.yaml` |

Hermes 和 OpenViking 使用**独立 Python 环境**，通过 HTTP 连接。不要把 OpenViking 安装到 Hermes 的虚拟环境中；两者依赖版本不同。

```text
Hermes headless serve :9119
  ├─ 对话模型：qwen3.8-flash
  └─ 内置 OpenViking memory provider
       └─ HTTP + USER API Key → OpenViking :1933
            ├─ 内容理解：qwen3.8-flash
            ├─ 向量模型：qwen3.7-text-embedding-flash
            └─ 持久数据：state/openviking/
```

`vendor/hermes/.git` 是 **NousResearch 的 Hermes 第三方源码仓库**，不是 `HaozheJing/digitalTutor` 项目仓库。不要在该目录向项目的 `origin/main` 推送代码。

## 4. 服务与模型配置

| 服务 | 监听地址 | 检查入口 |
| --- | --- | --- |
| Hermes headless 后端 | `127.0.0.1:9119` | `/api/status`；原生 WebSocket `/api/ws` |
| OpenViking | `127.0.0.1:1933` | `/health`、`/ready` |

项目服务绑定服务器回环地址，通过 SSH 隧道访问；没有配置公网反向代理。Hermes 当前没有构建前端，访问 `9119` 根页面不能视为已安装聊天网页。

```bash
ssh -p 11385 \
  -L 9119:127.0.0.1:9119 \
  -L 1933:127.0.0.1:1933 \
  root@connect.westc.seetacloud.com
```

| 配置项 | 值 |
| --- | --- |
| 接口类型 | OpenAI 兼容接口 |
| base URL | `https://maas.qianwenaiapi.com/compatible-mode/v1` |
| Hermes 对话模型 | `qwen3.8-flash` |
| Hermes 压缩模型 | `qwen3.8-flash` |
| OpenViking 理解模型 | `qwen3.8-flash` |
| Embedding 模型 | `qwen3.7-text-embedding-flash` |
| 向量维度 | 实测 1024 维，已写入配置 |
| 备用大模型 | `qwen3.8-max`，已记录，当前未启用 |

用户已确定：凡涉及对话理解，当前先用 small 模型。更换向量模型或维度时应同时安排索引迁移或重建，不能直接复用旧索引。

## 5. 远程目录与配置位置

以下路径均相对于 `/root/autodl-tmp/digitalTutor`。

| 路径 | 用途 |
| --- | --- |
| `vendor/hermes/` | 固定版本 Hermes 源码及官方 `uv.lock` |
| `vendor/hermes/venv/` | Hermes 虚拟环境，实际目录名是 `venv` |
| `vendor/skills/openviking-memory/` | 官方 OpenViking Skill 原文及参考文件 |
| `runtime/python/` | 项目内 CPython |
| `runtime/openviking/` | OpenViking 独立虚拟环境 |
| `runtime/supervisor/` | 进程管理器独立虚拟环境 |
| `bin/hermes` | 使用正确环境和路径的 Hermes 启动包装器 |
| `bin/ov`、`bin/openviking-server` | OpenViking CLI 与服务启动包装器 |
| `config/provider.json` | 模型参数与凭据的配置源文件 |
| `config/ov.conf` | OpenViking 服务配置，包含模型、存储与服务鉴权 |
| `config/ovcli.conf` | OpenViking CLI 配置，包含用户及管理访问密钥 |
| `config/runtime.env` | 集中路径变量、缓存路径与运行环境 |
| `config/supervisord.conf` | 两个后端的进程管理配置 |
| `state/hermes/` | Hermes 配置、会话、记忆和运行状态，亦为 `HERMES_HOME` |
| `state/hermes/config.yaml` | Hermes 模型、记忆 provider 与其他设置 |
| `state/hermes/.env` | Hermes 使用的模型与 OpenViking 用户凭据 |
| `state/openviking/` | OpenViking 持久数据 |
| `state/supervisor.sock`、`state/supervisord.pid` | 进程管理通信与状态文件 |
| `scripts/` | 服务器上的配置、启停、状态与验收入口 |
| `logs/` | 服务日志，已配置轮转 |
| `docs/` | 部署说明、依赖版本与验收记录 |
| `cache/`、`tmp/` | 项目内缓存与临时文件 |

SSH 非交互环境的默认 `PATH` 不一定有 Python。优先使用包装器，或显式使用 `runtime/openviking/bin/python`、`vendor/hermes/venv/bin/python`。需要运行环境变量时：

```bash
cd /root/autodl-tmp/digitalTutor
source config/runtime.env
```

不要将 shell 的 `HOME` 或本地 `CODEX_HOME` 改成项目目录来迁移文件；已有包装器使用 `HERMES_HOME` 和各组件的专用配置变量。

## 6. OpenViking 身份与 Skill

OpenViking 采用 `api_key` 模式，已创建开发用账号 `digitaltutor`。

| 身份 | 密钥文件 | 用途 |
| --- | --- | --- |
| ROOT | `config/openviking-api-key` | 账号与用户管理；不能用于普通租户数据 API |
| ADMIN：`digitaltutor/deployment-admin` | `config/openviking-admin-key` | 项目开发账号管理 |
| USER：`digitaltutor/teacher` | `config/openviking-user-key` | Hermes 实际读写知识、会话与记忆 |

凭据文件已核对为 `0600`。Hermes 使用 USER 密钥；不能为了让检索通过而切回 ROOT 密钥或关闭鉴权。

`digitaltutor/teacher` 是当前开发用服务身份，尚未实现学生、家庭之间的权限与记忆隔离，也不能把该身份当作某个真实学员 ID。

官方 `openviking-memory` Skill 已安装到本地 Codex：

```text
C:\Users\jinghaozhe\.codex\skills\openviking-memory\SKILL.md
```

服务器的 `vendor/skills/openviking-memory/` 保留原文。Hermes 实际使用内置 OpenViking provider，已验证工具 `viking_search`。该 Skill 所述 MCP 工具与 Hermes 原生工具名称不同，未将该 Skill 重复启用为 Hermes 的运行 Skill。

后续 Codex 会话是否能使用 OpenViking MCP，应检查当次已注册工具。**Skill 安装成功不代表 MCP 已连接。** Hermes 的原生 `MEMORY.md`、`USER.md` 能力仍存在；业务层记忆职责由后续设计落实。

## 7. 常用操作

以下命令在服务器执行。

### 状态、启动与停止

```bash
cd /root/autodl-tmp/digitalTutor
./scripts/status.sh
./scripts/start.sh
./scripts/stop.sh
```

Supervisor 会在进程意外退出后重启服务；SSH 断开不会停止已运行的服务。尚未设置服务器或 AutoDL 容器开机自动启动，服务器重启后执行 `scripts/start.sh`。

启动脚本先等待 OpenViking 就绪，再准备用户身份、刷新配置并启动 Hermes。刷新配置会依据 `config/provider.json` 更新本次接入涉及的模型、provider 等字段，保留其他额外配置。直接修改生成文件中的这些字段可能在下一次启动时被配置源覆盖。

### 试用与健康检查

```bash
./bin/hermes chat -q '你好' --oneshot
./bin/hermes memory status
./bin/ov --help
curl -fsS http://127.0.0.1:1933/health
curl -fsS http://127.0.0.1:1933/ready
curl -fsS http://127.0.0.1:9119/api/status
```

`hermes memory status` 中的 `available` 表示配置已具备，不能单独证明服务连接、检索或记忆提取成功。

当前未接入家长消息渠道，Hermes 状态中的 messaging gateway 为 `stopped`，`overall` 为 `degraded`；本次核对时后端、存储和后端自检正常。后续遇到 `degraded` 应读取具体组件原因，不能直接认定是上述原因。

### 修改配置与重启

修改服务器上的 `config/provider.json` 后：

```bash
cd /root/autodl-tmp/digitalTutor
runtime/openviking/bin/python scripts/configure.py
runtime/supervisor/bin/supervisorctl -c config/supervisord.conf restart openviking hermes
./scripts/status.sh
```

更换接口或模型需重新实测兼容性；生成器读取 `docs/provider-probe.json` 中的探测结果。更换 embedding 模型或维度，还要安排索引重建。重启前检查是否有其他会话正在使用服务。

日志位于 `logs/hermes.log`、`logs/openviking.log`、`logs/supervisord.log`。诊断输出需隐藏凭据，不要直接将 `.env`、`ov.conf`、`provider.json` 的完整内容贴进聊天。

## 8. Git 情况与后续提交

本地仓库位于 `E:\Agent Project\digitalTutor`，GitHub 仓库属于 `HaozheJing`，可见性已通过 API 核对为 **Private**。

| 内容 | 状态 |
| --- | --- |
| `main` | 已推送，并跟踪 `origin/main` |
| 初始提交 | `ff9fcb0`：知识库及后端部署文件 |
| 本文编写前的基线提交 | `763282b`：Linux 脚本执行权限和私有仓库验证 |
| 本文及后续变更 | 以实际 `git log` 和 GitHub `main` 为准，不将上述基线当作永久最新提交 |
| GitHub CLI | 本次未安装 `gh`；通过 Windows Git Credential Manager 的已有登录完成创建和推送 |

日常操作（本地 PowerShell）：

```powershell
Set-Location 'E:\Agent Project\digitalTutor'
git status --short --branch
git remote -v
git log -3 --oneline
git fetch origin
```

修改后检查待提交文件，再提交并推送：

```powershell
git add <本次需要提交的文件>
git diff --cached --stat
git commit -m '说明本次修改'
git push
```

`git push` 已验证可用。新的机器或账号仍需自行配置 GitHub 身份，不要把访问令牌写进 remote URL。

`.gitignore` 已排除密钥、环境配置、运行数据、缓存、下载的第三方源码与依赖环境，以及真实学员 Excel 导出。当前保留了空白整理模板 `knowledge/数字教辅知识库整理模板.xlsx`。被忽略的真实学员数据仍在本地文件系统中。

`.gitattributes` 为 shell 脚本等设置 LF，`ops/*.sh` 已纳入 Git 可执行权限。Windows 中可能因环境用户不同而出现 `dubious ownership`；已为本地项目的精确路径添加 Git `safe.directory`，不要扩大到 `*`。

**Git 推送不会自动更新远程运行环境。** 服务器部署目录尚未关联本项目 Git，只有 `vendor/hermes` 是已克隆的第三方源码仓库。后续若建设远程项目 checkout 或自动部署，应放在既定部署根目录内，并保留 `config/`、`state/` 和运行环境；不要直接用项目 checkout 覆盖整个部署目录。

多个会话协作时先查看已有修改和分支，避免覆盖其他会话成果。不要使用 `git reset --hard`、`git clean -fd` 或广泛进程清理来恢复环境；如需隔离测试，使用独立分支和独立配置、状态、端口。

## 9. 已验收范围与证据

以下验收发生于 2026-10-02。编写本文时再次只读核对了服务状态、模型配置及 GitHub 私有属性。

| 验收项 | 结果 | 远程证据 |
| --- | --- | --- |
| 对话接口与 embedding 接口 | 成功；向量为 1024 维 | `docs/provider-probe.json` |
| 两个 Python 环境依赖兼容性 | 均通过 `uv pip check` | 安装验收输出、依赖清单 |
| OpenViking 存活与就绪 | `/health`、`/ready` 通过 | `scripts/status.sh` 可复核 |
| 测试资料理解与入库 | Semantic 和 Embedding 队列无错误 | `docs/openviking-smoke.json` |
| 语义检索 | 检索出验收代号“海豚星” | 同上 |
| Hermes 实际工具调用 | small 模型调用 `viking_search`，正确回答“海豚星” | `docs/hermes-smoke.jsonl` |
| 后端 WebSocket | 鉴权后收到 `event/gateway.ready` | `docs/hermes-api-smoke.json` |
| 停止后重新启动 | 两个服务恢复，持久知识仍可检索 | 启停验收；`scripts/final-checks.sh` |
| Git | 私有属性确认，基线本地与远程提交一致 | `ops/github_repository.py --verify` 可复核 |

测试资料位于 OpenViking 的 `viking://resources/deployment-smoke`，明确标记为部署测试，不是真实学生资料。后续业务资料处理不要把这份测试资料当成学情依据。

远程 `docs/` 还保存了 `hermes-locked-requirements.txt`、`hermes-locked-dependencies.txt`、`hermes-requirements.txt`、`openviking-requirements.txt`。Hermes 使用固定 release 的官方锁定依赖；OpenViking 固定主包版本并保存已安装依赖清单。

## 10. 尚未完成的工作

- 教辅 System Prompt、服务动作 Skill 和企业知识库正式导入。
- 学员及家庭档案、业务事项状态、个性化记忆职责与权限设计。
- 触发事件、服务任务、消息排队、发送回执与跨日跟进。
- 真实业务接口和家长/孩子消息渠道。
- 独立的跨会话记忆提取、回忆闭环验收。
- 图片、音频、完整课程资料及真实业务场景验收。
- Docker 镜像、容器编排和自动部署。
- 服务器开机自启动。

当前交付是可运行的框架与知识服务环境，不代表教辅业务已实现或通过业务验收。

后续 Docker 化应从源码与固定依赖重新构建，不直接复制主机 venv 作为可移植环境；凭据使用运行时配置或 secret 注入；`state/` 保留为持久卷，`cache/` 和 `tmp/` 不作为业务数据备份。

## 11. 给下一会话的引用方式

可以直接发送以下说明，并附上本文：

> 请先阅读 `docs/远程服务器与Git交接.md`，核对本地 Git 状态及与你任务相关的远程服务状态。远程文件继续集中在 `/root/autodl-tmp/digitalTutor`；在现有环境基础上完成我本次指定的任务，保留已安装环境、凭据和持久数据。本文记录的基线与验收不代替当前检查。本次任务是：〔填写具体任务〕。

本文更新后应同步项目 Git 和远程 `docs/远程服务器与Git交接.md`，避免两处交接记录发生漂移。

## 12. 官方资料

- [火山引擎 OpenViking Context 介绍](https://docs.volcengine.com/docs/84313/2374478?lang=zh)
- [OpenViking 个人版接入记忆](https://docs.volcengine.com/docs/84313/2693723?lang=zh)
- [OpenViking 官方 Skill](https://github.com/volcengine/OpenViking/tree/main/agent-plugins/skills/openviking-memory)
- [Hermes 与 OpenViking 集成说明](https://docs.openviking.ai/en/agent-integrations/05-hermes)
- [OpenViking 0.4.22 鉴权说明](https://github.com/volcengine/OpenViking/blob/v0.4.22/docs/en/guides/04-authentication.md)
- [本次安装的 Hermes release 源码](https://github.com/NousResearch/hermes-agent/tree/v2026.9.24)

查阅主分支或最新文档时，需要核对其是否适用于本文的固定版本；不要仅因上游出现新版就自动升级现有服务。
