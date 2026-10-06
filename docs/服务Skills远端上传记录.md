# 服务 Skills 远端文件交接

日期：2026年10月3日。目标项目目录：`/root/autodl-tmp/digitalTutor`。

本批按本地项目布局上传服务能力与直接依赖。最终上传数量、逐文件SHA256及远端读回结果保存在同目录的 `service-skills-<UTC时间>.json` 回执中；本地对应回执位于 `deployments/`。

| 远端位置（相对项目根目录） | 内容 |
| --- | --- |
| `agent/skills/` | 41个Skill目录：34个家庭服务入口、3个编排/公共处理/扩展入口、4个名称导航别名。 |
| `agent/guides/` | 6份共用服务规范，由Skill显式关联。 |
| `agent/config/service.yaml` | 服务规则小样，自动触发保持关闭，待确认项保持待确认。 |
| `knowledge/service/【数字员工】服务动作打样.md` | 动作、FAQ与相关规范原稿，供现有知识引用使用。 |
| `knowledge/product/阅读营产品知识库.md` | Skill引用的产品资料原稿。 |
| `knowledge/lesson/【数字员工】L2-课程大纲.md` | 远端原有课程文件；上传前核对与本地相同，直接复用。 |

上传使用SSH已登记主机密钥；登录密码仅通过进程环境传入，不保存到脚本或回执。存在内容不同的远端同名文件时，上传脚本会停止，不直接覆盖。

本批是项目文件交付。没有修改 `state/hermes/config.yaml`、官方内置Skills、模型或OpenViking配置，没有重启服务，也没有将这些能力注册为已可调用的Hermes运行Skills。

后续加载适配应指向 `agent/skills/`，保留 `guides/`、`config/`、`knowledge/` 的相对关系，并实际实现 `metadata.tutor-required-guides` 所声明的依赖加载。已安装Hermes源码包含外部Skill目录支持，但实际配置、版本适配与业务工具接入仍须专门验证；单独复制到全局Skills目录不能证明这些依赖会被加载。

本地详细开发总结位于 `docs/服务Skills开发总结与后续会话交接.md`，测试报告位于 `docs/服务Skills核对与模拟测试报告.md`。它们及大批冻结测试输入不属于本次运行文件上传范围。
