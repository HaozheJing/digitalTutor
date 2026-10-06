# digitalTutor 远程服务器与 Git 交接

核对日期：2026年10月3日（Asia/Shanghai）。本页维护已部署形态、操作入口与历史验收定位；业务方案见原样保留的[服务状态与规划设计交接](教辅数字员工服务状态与规划设计交接.md)，阶段成果从[README](README.md)进入。

## 当前部署形态

远端根目录为 `/root/autodl-tmp/digitalTutor`。本次只读核对Hermes与OpenViking进程均为RUNNING，OpenViking健康与就绪通过；Hermes的dashboard和storage正常，消息网关未接渠道、为stopped，整体状态degraded。该结果是核对时快照，后续用 `scripts/status.sh` 复查。

本地 `agent/` 与远端48个文件哈希一致，自动触发关闭；在现有Hermes配置、环境、包装器和本地Skill目录检查中未见这批业务Skill的加载绑定，`agent/system.md` 尚不存在。课程和产品知识已有独立入库成果。见[只读核对记录](../deployments/docs-cleanup-20261003T021247Z.json)、[Skills交接](服务Skills开发总结与后续会话交接.md)及[知识入口](README.md)。

本地项目位于 `E:\Agent Project\digitalTutor`，分支为 `main`，当前有大量尚未纳入Git的开发产物；不能沿用旧文档“工作区干净”的描述。远端项目根尚无 `.git`，`vendor/hermes/.git` 属于第三方源码，Git推送不自动部署。

后续源码、依赖、配置、数据、日志与缓存继续放在既定远端根目录；保留现有配置与状态，不以新checkout覆盖运行目录。

## SSH连接

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

## 固定软件基线与连接关系

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

## 服务与模型配置

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

## 远端目录与配置位置

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

课程与产品知识连接企业版时，显式使用 `runtime/openviking/enterprise-ovcli.conf`；`bin/ov` 默认包装器加载本地服务配置，两者不能混淆。远端 `agent/` 为项目教辅能力文件，`build/openviking/` 为知识加工与验证产物。远端 `docs/` 还保留依赖锁与原始验收文件，和本地交接文档目录内容不同，不能按本次本地清单清空远端目录。

## 服务身份

本地OpenViking使用api_key鉴权。开发账号为 `digitaltutor`，ADMIN为 `deployment-admin`，Hermes使用USER `teacher`；密钥分别在 `config/openviking-admin-key`、`config/openviking-user-key`，ROOT管理密钥在 `config/openviking-api-key`。凭据不得写入文档或Git。

`digitaltutor/teacher` 是开发服务身份，不代表真实学生或家庭隔离。Hermes使用内置OpenViking provider，历史验收调用过 `viking_search`。Codex是否具有OpenViking MCP工具以当次连接为准，安装Skill不等于连接服务。

## 常用操作

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

## Git与发布

项目仓库为 <https://github.com/HaozheJing/digitalTutor>，origin指向该项目，main跟踪origin/main；私有属性及推送可用性为2026年10月2日的历史核验。本次文档整理未提交、推送或部署。

接手先运行 `git status --short --branch`、`git log -3 --oneline`，逐项确认需提交文件。`.gitignore` 排除凭据、运行状态、依赖、缓存和真实学生导出；未跟踪文件不能依赖Git恢复。保留其他会话成果，不使用广泛清理或硬重置恢复环境。

本地 `ops/` 保存部署与维护脚本，远端操作入口在 `scripts/`。文件同步、服务配置、重载、验证与Git版本记录分别处理。

## 历史验收与后续边界

2026年10月2日环境验收包括：模型接口和1024维向量、依赖兼容、OpenViking入库和检索、Hermes调用viking_search、WebSocket、停止后恢复。远端证据位于 `docs/provider-probe.json`、`docs/openviking-smoke.json`、`docs/hermes-smoke.jsonl`、`docs/hermes-api-smoke.json`，依赖锁与清单也在远端docs。部署测试知识 `viking://resources/deployment-smoke` 不是业务学情。

原“服务Skill和企业知识尚未建设”的待办已经被后续成果取代，详见[当前阶段入口](README.md)。业务加载、真实消息工具和执行接续仍需要对应验收，不能用历史环境冒烟代替。Docker化与开机自启动仍按原部署记录作为后续事项，本次未执行安装、重启或升级。

固定版本参考：[Hermes release](https://github.com/NousResearch/hermes-agent/tree/v2026.9.24)、[OpenViking 0.4.22鉴权](https://github.com/volcengine/OpenViking/blob/v0.4.22/docs/en/guides/04-authentication.md)。
