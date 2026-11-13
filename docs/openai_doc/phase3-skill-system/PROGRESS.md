# 进度

当前里程碑：M3 和 M4 已验收。M5 未开始。

## 已完成

- M0 基线、M1 契约、M2 文件注册表。
- M3 元数据发现、四种模式、懒加载和旧提示兼容。
- M4 任务快照、上下文、资源路径和 Chat/Agent 共用运行时。

## 正在执行

无。

## 未完成

- M5 到 M8。
- 数据库中的技能表。
- 真实模型联调。

## 测试

```text
python -m pytest tests/skills -q --tb=line
25 passed in 1.01s

python -m pytest -q --tb=line
2 failed, 323 passed, 10 skipped in 11.41s
```

两项失败仍是 `tests/database/test_metadata.py` 的旧表数量断言。

## 下一步

M5：第三方脚本继续默认不执行，补安全边界测试。不把 `run_python` 当作隔离沙箱。
