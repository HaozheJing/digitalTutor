# 历史兼容代码

- delivery_limits.py：旧Skill文字长度约定的迁移转换，仍供新工作区复制旧稿时使用。
- skill_aliases.py：旧Skill名称及旧知识路由提示，保持历史调用可解析，不新增动作。
- retired_materials.py：已停用附件清单，阻止旧上下文重新加载归档规则。

legacy表示兼容代码的维护位置，不表示可以删除。业务入口保留必要导入；旧legacy_limits模块仅作为兼容转发。
