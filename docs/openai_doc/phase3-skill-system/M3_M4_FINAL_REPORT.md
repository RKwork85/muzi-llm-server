# M3 / M4 最终报告

## 完成情况

M3 发现和 M4 运行时都已接上现有注册表。Chat 和 Agent 共用 `SkillOrchestrator`。没有匹配或未开启 `skill_mode` 时，原对话路径不变。

M3 验收：PASS。M4 验收：PASS。

## 集成

- Chat：`prepare_stream()` 在原系统提示和旧技能提示之后追加不可信技能段落。
- Agent：`start()` 使用现有任务号，在进入 `AgentLoop` 之前追加同一段落。循环、工具执行和 MCP 装配没有重写。

## 数据库

没有迁移，没有改表，没有动真实库。激活快照写在 `skills_root/runtime/`。`skills_root` 默认是空字符串。

## 测试

```text
python -m pytest tests/skills -q --tb=line
25 passed in 1.01s

python -m pytest -q --tb=line
2 failed, 323 passed, 10 skipped in 11.41s
```

历史失败仍是 `tests/database/test_metadata.py` 的两项。本次没有新增失败。

## 保护范围

MCP 实现、代码执行工具、前端插件、旧四条提示和 `docs/phase3/` 都没有修改。`app/skills` 不执行脚本。没有 Git 提交。

## 未解决

- 组织和项目作用域仍然不能写入。
- 激活记录还不是数据库表。
- 当前 Windows 进程不能创建符号链接，逃逸测试使用解析后落在包外的路径。
- 没有真实模型联调。
- M5 安全治理、M6 持久化、M7 接口和 M8 发布都还没做。

## 结论

- CODE PASS：M3、M4 专项和现有回归里，除两项历史元数据失败外均通过。
- ACCEPTED：M3 和 M4 的验收项通过，符号链接权限限制已写明。
- PRODUCTION READY：否。

可以进入 M5。M5 仍要单独做脚本隔离、权限和安全测试，不能把这次结果当成整个 Skill System 已经可以上线。
