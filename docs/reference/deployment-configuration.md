# 部署配置与凭据边界

本文面向需要配置 digitalTutor 的开发和运维人员。公开文档说明配置结构与读取方式；实际连接信息由项目负责人通过公司私有文档或凭据管理系统提供。文中的占位符没有访问能力。

## 公开仓库与私有交接分别包含什么

| 内容 | 公开仓库 | 公司私有交接 |
| --- | --- | --- |
| 架构、源码入口、字段名称、环境变量名称 | 保留 | 可按部署补充 |
| 配置示例 | 仅使用占位值 | 提供环境对应的实际配置 |
| OpenViking | CLI 调用、配置格式、知识查询流程 | 服务地址、业务 API key；管理密钥单独授权 |
| 模型服务 | provider 配置结构、模型选择方式 | 实际端点、API key、额度和权限负责人 |
| Linux 主机 | 运行依赖、启动入口和路径约定 | 主机地址、登录账号、密码或私钥、主机指纹 |
| 工作台与机器事件接入 | HTTP 路由、认证字段及作用域 | 实际授权 token、来源身份和访问入口 |
| 企业微信及其他渠道 | 渠道适配流程与回执含义 | 组织与联系人路由、渠道鉴权、完整回调地址 |

私有交接应标明配置来源、所属环境、用途、权限和核对日期。不存在或未确认的控制台账号、密码应明确写为待确认，不能用 API key 代替网页登录密码。

## OpenViking 业务配置

知识查询通过 OpenViking CLI 执行。业务读取入口使用 `TUTOR_KNOWLEDGE_CONFIG_FILE` 指定配置文件，并将该路径传给 CLI 的 `OPENVIKING_CLI_CONFIG_FILE`。直接运行 CLI 时，可设置后者。课程维护脚本的配置参数以对应脚本的命令行为准。

下面是 JSON 格式示意，`url` 与 `api_key` 必须从私有交接获取后替换：

```json
{
  "url": "https://openviking.example.invalid",
  "api_key": "<OPENVIKING_BUSINESS_API_KEY>"
}
```

建议将实际文件保存在代码仓库外，例如部署账户的私有配置目录：

```bash
export TUTOR_KNOWLEDGE_CONFIG_FILE="$HOME/.config/digitalTutor/knowledge-ovcli.conf"
export OPENVIKING_CLI_CONFIG_FILE="$TUTOR_KNOWLEDGE_CONFIG_FILE"
chmod 600 "$TUTOR_KNOWLEDGE_CONFIG_FILE"
```

以上命令只指定路径和限制文件权限，不会创建配置、安装 CLI 或验证服务。当前 CLI 可执行路径仍由源码中的 `CLI` 常量指定，移植环境时需要核对。业务读取通常不需要服务端 `root_api_key`，管理凭据与业务凭据分别维护。

读取逻辑见 [knowledge_search.py](../../adapters/knowledge_search.py)、[curriculum_knowledge.py](../../ops/curriculum_knowledge.py)，工作空间配置装配见 [native_experiments.py](../../adapters/native_experiments.py)。

## 模型提供器

当前 [启动入口](../../app/start.py) 从工作空间的 `config/provider.json` 读取 `api_key`，再传入运行进程的 `TUTOR_API_KEY`。以下仅展示该入口所需字段，不是完整 DSH 模型配置：

```json
{
  "api_key": "<MODEL_PROVIDER_API_KEY>"
}
```

实际 provider 文件由部署系统或项目负责人私下提供。完整模型端点、模型 ID、兼容参数、输出预算及独立记忆模型配置还需与 DSH profile 保持一致；单独设置环境变量或复制这个最小示例不能替代完整装配。

启动入口还依赖约定位置的 Node、DSH 依赖和独立运行目录。环境未配齐时，先按 [开发者指南](../DEVELOPER-GUIDE.md#启动与工作空间装配) 核对，不使用现有业务数据库执行试启动。

## 工作台、事件来源与渠道

公开文档保留认证方式和字段名，部署时通过私有配置注入授权值。工作台 token、完课事件来源 token、渠道身份和回调路径密钥分别对应不同调用边界，不互相复用。

回调地址若含路径密钥，应将整条地址作为敏感信息维护。故障记录和截图使用脱敏表示；需要核对企业微信侧配置时，由有权限的人员在私有环境操作。

`context_token`、`generation_token`、`execution_token` 等业务字段用于版本和授权校验，源码中的字段定义可以公开；实际运行值、家庭数据、数据库和会话记录不进入公开示例。

## 配置管理与验证

- 在独立工作空间装配实际配置和运行依赖，确认目标数据库、端口及渠道允许名单。
- 在私有渠道核对每项配置的用途和权限；配置文件存在并不能证明密钥有效或服务已完成接入。
- 启动后按对应业务流程检查健康状态、知识读取与受控交付，不把生成内容视为发送成功。
- 本仓库忽略常见私有配置文件，但 `.gitignore` 不会移除已经提交的文件或历史内容。提交前仍需检查暂存差异，尤其是日志、截图、归档材料和配置样例。

运行与回归的边界见 [开发指引](../development.md)；本文不包含任何现有服务的可用账号或凭据。
