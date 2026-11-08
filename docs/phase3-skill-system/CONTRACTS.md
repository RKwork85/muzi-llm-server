# 接口契约

协议在 `app/skills/protocols.py`。M1 不提供注册表或加载器实现。

| 协议 | 方法 | 输入 | 输出 |
|---|---|---|---|
| SkillRegistry | register / get / query / update / uninstall | 清单、正文、调用方、来源、作用域 | Skill、SkillMetadata 列表或 SkillVersion |
| SkillDiscovery | discover | DiscoveryRequest | SkillMetadata 列表 |
| SkillLoader | load_instructions / load_resource | 技能、固定版本、调用方、相对路径 | LoadedInstructions 或 SkillResource |
| SkillRuntime | activate / deactivate | 技能、模式、任务、激活标识 | SkillContext 或 SkillActivation |
| SkillValidator | validate | SkillManifest | ValidationReport |
| SkillSecurity | visible / declared_tools | Skill、调用方、SkillPermission | 是否可见，以及声明的工具名 |
| SkillResourceManager | list_resources | 技能、版本、调用方 | 资源索引 |
| SkillLifecycleManager | apply / record | 安装记录、动作、激活数、执行记录 | 新安装状态或执行记录 |

## 错误

| 类型 | 场景 |
|---|---|
| SkillValidationError | 名称、摘要或资源路径不合法 |
| SkillStateError | 状态不允许该动作，或来源未启用 |
| SkillScopeError | 组织或项目作用域尚未可写 |

错误信息只含安全说明，不回显技能正文。

## 依赖

协议只引用 `app/skills/models.py`。不引用 Provider、ToolExecutor 或 SQLAlchemy。
