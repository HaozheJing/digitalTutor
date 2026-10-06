# 第三阶段限定知识与 Skill 读取交接

日期：2026-10-03。新增模块 `knowledge.py`、`skill_loader.py`，新增独立测试 `test_stage3_knowledge.py`。未修改 Application、ToolRegistry、domain 或第三方源码，服务器统一测试由主控执行。

## 工具接线

两个模块均导出 `SPECS`，格式与现有工具目录一致：`name: (roles, PydanticModelOrNone, description)`。

- `knowledge.list` 无参数，`knowledge.read` 仅接受 `document_id`。
- `skills.list` 无参数，`skills.read` 仅接受 `skill_id`。
- 对应类方法：`KnowledgeCatalog.dispatch(name,args,role,binding)`、`SkillCatalog.dispatch(name,args,role)`。
- 宿主负责从授权会话获取 role，从家庭或运行配置获取 KnowledgeBinding；模型不能提交 role、family、revision、URI 或任意路径。

初始化示意：

```python
from digital_tutor.knowledge import KnowledgeCatalog, KnowledgeBinding, OpenVikingCLI
from digital_tutor.skill_loader import SkillCatalog

catalog = KnowledgeCatalog([course_map, product_map], OpenVikingCLI(ov_executable, enterprise_config))
binding = KnowledgeBinding(revision='source-c764d8b325cc-nosplit-v2',
    allowed_unit_ids=('LESSON-L2-FOX-D01',), simulation=True)
skills = SkillCatalog(project_root / 'agent')
```

现有课程清单缺正式课程版本、业务审核、真实课节对应，因此真实绑定默认拒绝。`simulation=True` 仅由宿主用于虚构家庭验证，结果携带该标识和原始未知版本，不能升级为真实家庭课程适用结论。产品正文保留未核实来源状态；reference 默认为不可见，需宿主显式允许。

课程新版 nosplit `access-map.json` 在本地存在，远端尚未部署；原生读取探针证明云端新版正文存在。主控同步时应部署该固定清单，不能误用旧版清单。

## 原生 Skill 配置

每个独立 Hermes 进程须在 import 前设置独立 HERMES_HOME，config.yaml 的 `skills.external_dirs` 指向批准的项目 `agent/skills`。cwd 也使用运行隔离目录。模块的默认 adapter 调用 `tools.skills_tool.skill_view(name,preprocess=False)`，核对 `_source_path` 与全文，防止同名遮蔽和模板/inline shell 执行。

34 个家庭入口、2 个内部入口、1 个扩展入口、4 个别名均按批准清单解析。服务角色发现36个规范入口，planner角色只发现 `plan-proactive-service`，admin发现37个规范入口；别名重定向不增加能力。Skill正文、全部 `tutor-required-guides` 规范与V1运行约束一起返回。正文和规范在catalog实例内固定指纹，变化须重载审核目录。

## 服务器实际原生读取探针

使用既有 Hermes 解释器与企业版CLI配置，无模型调用，无服务重启，无vendor修改。临时 profile 只用于读取，探针结束清理。

| 检查 | 实际结果 |
| --- | --- |
| 原生 `skill_view('explain-lesson',preprocess=False)` | success=true |
| `_source_path` 命中项目批准文件 | true |
| 原生返回全文与批准文件一致 | true，1608字符 |
| Skill SHA256 | `570277f0e056fe8561d8e8263b8a9fe3a869457b62ae7c5722f8d54c3f21664c` |
| 企业版CLI读取nosplit新版D01正文 | exit=0，JSON形状为 `{ok,result}` |
| 课程正文 | 1742字符 |
| 课程正文SHA256 | `5bae2236aa83e14bb33c7046dd85708a9e01149bbb09cfef2c72d73876716518`，与本地清单一致 |

CLI decoder 按 `ops/course_cli.py` 的末尾完整JSON原则解析，不把命令回显中的JSON当结果。不读取或打印配置中的密钥，错误不把stdout/stderr或服务器路径回传给模型。

此探针验证原生依赖和真实知识读取可用；新增12项单元/接口测试需由主控统一上传并在服务器执行后才算模块验收完成。未执行真实模型语义测试。
