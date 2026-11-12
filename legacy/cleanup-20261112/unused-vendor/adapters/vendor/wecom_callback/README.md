# 企业微信真实对话接入

## 当前测试模式：Replyer-only（2026-10-19）

当前工作台已切换为独立文字 Replyer。下面原 Service/媒体说明保留作回退参考，不代表当前仍执行 Service。

- 工作台只编辑 `replyer-common.md` 与 `replyer-role.md` 对应的两段提示词；装配时复制到不可变版本的 `active.parts`，实际模型 system 消息只由这两段组成。不会追加 Service 执行提示词或启动 Hermes 工具循环。
- `replyer-fixtures.json` 是人为准备的五组模拟 Service 输出（不是实际查询，不是真实家长案例），可在右侧选场景并展开只读材料。场景选择与提示词一同装配生效，默认自由聊天。
- Replyer 使用原 Doubao 模型，一次文字生成后由宿主调用受控发送账本；不提供模型业务工具。仍限定原有私聊白名单，回调鉴权、自发消息过滤和发送回执规则保留。
- 每轮输入为：两段系统提示词、标注 synthetic 的模拟 Service 材料、当前测试上下文中同一接收对象最近最多 20 条入站及其已确认发送回复、当前用户原话。不会读取旧 Service 的业务历史、记忆或运行指令。
- 数据在当前 context 目录的 `replyer.sqlite` 与 `replyer.channel.sqlite`；每轮保存实际模型 request、提示词版本、fixture ID、回复与发送状态。模型失败显示 failed，不伪造回复。发送超时保持 unknown，不自动重发。
- 此模式仅处理文字；图片/语音记录为 replyer_text_only，不触发媒体识别或模型回复。用户后续需要时再扩展。
- 切换时已创建空白测试上下文，旧记录保留。原代码、页面、active 和 draft 位于服务器 `var/wecom-autoreply/backups/before-replyer-v1/`。回退需同步恢复页面、运行模式及所需上下文，不能只替换提示词。
- 验证：23 项隔离回归测试通过；4 个真实模型调用使用合成输入且不外发。它们验证可运行性，不表示语气质量已经验收；真实微信 Replyer 首轮仍需用户来信验证。

工作台编辑不会自动同步回本地初始 Markdown 文件。新装配版本从下一轮生效；清空上下文保留当前提示词和选定场景。

2026-10-19 已完成一次真实文本消息闭环：平台回调、token 校验、Service 原生运行、真实发送、平台成功回执。当前仅启用用户确认的“北斋先生 → 景浩哲”私聊，其他联系人不触发回复。

## 运行位置

- 独立候选：`/home/ubuntu/digitalTutor/candidates/service-wecom-v1`
- 配置：`/home/ubuntu/digitalTutor/config/wecom-callback.json`，权限 `0600`；包含凭证，不复制到仓库。
- 回调收件箱：`/home/ubuntu/digitalTutor/var/wecom-callback/inbox.sqlite`
- 实际对话、发送账本、原生运行记录：`/home/ubuntu/digitalTutor/var/wecom-autoreply/`
- 服务：`digitaltutor-wecom-callback.service`、`digitaltutor-wecom-autoreply.service`
- API Base URL：`https://kilo-bg.ddregion.com/hub-api`

原有 demo10 Service 提示词与原生工具循环沿用；原有工作台未替换。新候选使用独立业务数据库，没有导入历史模拟对话或学习数据。`smoke-20261019` 是独立合成测试目录，不是真实用户对话。

## 处理边界

1. 回调入口先鉴权并持久化，再快速确认；token 在持久化前脱敏。路由允许平台附加 query 和结尾斜杠，但 query 不用于绕过 JSON token 鉴权。
2. 桥接器只接收启用时间之后、已验证且匹配 `orgId/imBotId/chatId/imContactId` 白名单的个人文字、图片和语音来信。平台实际省略入站 `isSelf` 时，额外要求个人微信发送者类型、发送者不等于机器人，以及一致的两个外部联系人 ID。机器人自己发出的回调不触发回复。
3. Service 以 passive 模式执行，只支持文本 `reply` 和 `clarify_existing`。没有启动主动计划调度。群聊和语音回复尚未接入。
4. HTTP 调用之前持久记录操作身份；同一操作重复调用不会重发。HTTP 超时保持未知，不自动重试外发。
5. 发送接口成功只记 `accepted`；匹配企业、双方身份、请求 ID 的成功回执才记 `sent`。实际成功回执省略 `errorCode` 时，需要 `sendCode=0` 且有 `messageId`。`sent` 不代表用户已读。

## 运维

### 图片与语音输入

2026-10-19 接入媒体预处理，保留当前 Service 模型、系统提示词、原生工具及上下文：

- `messageType=6`：优先读取 `payload.artwork.url`，其次 `imageUrl`；没有地址时调用平台 `POST /api/v2/message/loadArtWorkImage`，传 `chatId + messageId`。Doubao Seed 2.1 Pro 产生结构化视觉观察、文字与不确定项，再交给 Service。当前不是把原图直接塞进 Service 原生会话；精细看图追问可能需要补充原图工具。
- `messageType=2`：使用 `payload.voiceUrl` 的 MP3 等标准音频，交给 Qwen3.8-Omni-Flash 识别是否有可辨认话语并转写。忽略平台附带 `payload.text`，不把 SILK 地址冒充 MP3。没有可辨认话语时转写为空，明确标记而不猜测内容。
- Omni 配置位于服务器 `config/wecom-media.json`，权限 `0600`；只含媒体模型连接，不改主模型配置。密钥不写入仓库、工作台状态或日志。
- `media.py` 的单独子进程处理网络请求，结果写入 `var/wecom-autoreply/media.sqlite`；每条媒体最多尝试两次，成功结果缓存。按消息顺序等待媒体处理，同时继续接收发送回执。失败会作为内容未知交给 Service，不使用平台文本编造兜底。
- 只接受已观察到的 HTTPS 媒体存储主机 `area-k.tos-cn-beijing.volces.com`；若平台更换主机需核验后调整白名单。限制图片声明大小 20 MiB、音频声明时长 180 秒；这不是对远端文件实际字节大小的下载核验。
- 视觉观察与语音转写在入站正文中明确标注为派生结果，来源保留真实回调 ID。原有 `media_status` 枚举无对应值，因此已识别内容暂记 `unknown`；失败记 `media_unavailable`。缓存保存识别模型与结构化结果，静音不会生成转写字段。
- 清空上下文会等待当前媒体进程结束；旧回调不会注入新会话。媒体缓存保留用于审计，但不作为新会话记忆加载。

### 提示词工作台

本机入口 `http://127.0.0.1:18997/`，经 SSH 转发到服务器 `127.0.0.1:9011`；服务名 `digitaltutor-wecom-workbench`。页面支持保存草稿、一键装配、查看生效版本和最近真实聊天。

装配产物保存在 `var/wecom-autoreply/workbench/versions/`，通过原子替换 `active.json` 发布。每个运行进程在开始时固定候选，下一轮读取新版本，正在运行的回复不受改写影响。提示词指纹变化会创建新的原生上下文并从原业务库重建，聊天记录保留。`last-prompt.json` 标明最近一轮选用版本。只保存草稿不会生效。

“清空测试上下文”请求在当前 worker 结束后切换到全新的 `contexts/<id>/business.sqlite` 和 `runs/`，因此旧聊天、记忆、事项及原生会话都不进入新测试。原数据保留，`context-archives/` 存放旧上下文目录索引。清空前已入库或发送时间早于清空请求的延迟消息不进入新会话。提示词、草稿、凭证、白名单不变。页面显示切换状态后再发下一条测试消息。

本机隧道关闭后可重新运行：`D:\conda\python.exe ops/wecom_callback/tunnel.py --access tmp/migration-execute-20261013/new-server-access.json --port 18997`。

查看 `var/wecom-autoreply/status.json` 和 systemd 状态。需要暂停自动回复时，将配置的 `auto_reply_enabled` 改为 `false`，再重启自动回复服务；回调收件箱可继续接收。更改接收 token 或企业 ID 后，必须重启回调服务。

不要直接放开所有联系人。新增测试会话应先用真实入站消息核对身份，再追加白名单，并明确启用时间。不要把企业级 token 或带秘密路径的回调 URL 写入普通日志。

## 验证记录

- 18 项回归测试通过，涵盖鉴权、私聊白名单、原生发送回执、上下文清空、静音不虚构、原图接口回退、媒体缓存/失败重试、先图后字的顺序以及识别时继续处理回执。这些测试在 HTTP 边界模拟，不对真实联系人发送。
- 原生模型合成测试通过，发送至模拟账本。
- 真实来信“回调测试103”收到，首次回复的实际 HTTP 调用数为 1，平台 `sendCode=0`，本地操作已确认为 `sent`。机器人自己的消息回调已被过滤。
- 真实媒体回调 17（图片）、18（静音语音）、19（课堂录音）已完成识别并进入当前 Service 测试上下文；均一次识别成功。18 的平台附带文本为“嗯。”，独立音频检测结果为 `has_speech=false`，该平台文本没有注入 Service。19 检测为有人声并产生课堂内容转写；准确率未由用户逐字验收。
- Service 对三条媒体形成一条澄清回复，操作回执为 `sent`。图片链路验证了视觉观察生成与上下文注入，未将一张照片测试解释为精细题目识别的质量验收。

官方接口文档入口：<https://s.apifox.cn/d292e311-af7c-4a68-bfe4-416fd1d657b6/7068846m0>

Omni 官方说明：<https://help.aliyun.com/zh/model-studio/qwen3-8-omni-flash>
