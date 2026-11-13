# M4 验收

环境：Python 3.13.11，pytest 9.2.0。

```text
C:\Users\rkwork\miniconda3\python.exe -m pytest tests/skills/test_runtime_skills.py tests/skills/test_skill_integration.py -q --tb=line
```

这两份测试都包含在 `tests/skills` 的 25 项通过里。

| ID | 结果 | 证据 |
|---|---|---|
| R-01 | PASS | 快照保存 `principal_id` 和 `task_id`，其他用户读取返回 `SKILL_NOT_FOUND` |
| R-02 | PASS | 更新并禁用后，快照仍是 `1.0.0` 和旧正文 |
| R-03 | PASS | 一条任务同时激活 weekly-report 和 meeting-notes，上限 3 |
| R-04 | PASS | 取消后的 `move(completed)` 被拒绝 |
| R-05 | PASS | 两个线程得到同一个 `activation_id`，激活计数为 1 |
| R-06 | PASS | `BuiltContext.sections` 可按类型断言 |
| R-07 | PASS | 系统段落保持原文，技能段落 `untrusted=True` |
| R-08 | PASS | 未传入资源路径时不读引用；测试单独读取一个 `references` 文件 |
| R-09 | PASS，有限制 | `..`、绝对路径、盘符、UNC 和混合分隔符被拒绝。当前进程没有创建符号链接的权限（WinError 1314），逃逸检查用解析到包外的路径覆盖 |
| R-10 | PASS | `ChatService.skill_runtime` 和 `AgentRuntime.skill_runtime` 是同一个适配器 |
| R-11 | PASS | 取消后状态为 `cancelled`，重复取消不再释放 |
| R-12 | PASS | 见 R-02 |
| R-13 | PASS | 包内 `scripts/run.py` 没有被执行；`app/skills` 源码没有 `subprocess`、`eval(`、`exec(`、`os.system` |
| R-14 | PASS | 损坏的 `SKILL.md` 返回 `SKILL_PACKAGE_INVALID`，`prompt_block` 为空 |

Agent 集成测试替换了 `_execute`，用来确认交给循环的提示词。它没有调用模型。

结论：M4 验收 PASS。符号链接的真实创建受 Windows 权限限制。这不是生产就绪。
