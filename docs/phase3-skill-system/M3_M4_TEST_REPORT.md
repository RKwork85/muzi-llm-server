# M3 / M4 测试记录

环境：Python 3.13.11，pytest 9.2.0。工作目录 `muzi-llm-server`。解释器 `C:\Users\rkwork\miniconda3\python.exe`。

## 专项

```text
python -m pytest tests/skills -q --tb=line
25 passed in 1.01s
```

其中 M0–M2 原有契约和注册表测试仍在这个目录里，一起通过。

## 全量

```text
python -m pytest -q --tb=line
2 failed, 323 passed, 10 skipped in 11.41s
```

失败只有：

- `tests/database/test_metadata.py::test_nine_business_tables_exist`
- `tests/database/test_metadata.py::test_foreign_keys_use_explicit_delete_actions`

原因仍是断言只列了九张旧业务表，模型里还有 MCP 表。本次没有改这个测试，也没有改 MCP 表。没有新增失败。

先前记录的基线是 307 passed、10 skipped、2 failed。这次通过数更高，是因为其间增加了 Skill 测试。失败名称没有变化。

## 没有执行的内容

没有调用真实 DeepSeek。Agent 集成测试在进入模型循环前替换了 `_execute`。符号链接文件没有在这台 Windows 会话里创建成功。
