# M3 验收

环境：Python 3.13.11，pytest 9.2.0。命令在 `muzi-llm-server` 下执行。

```text
C:\Users\rkwork\miniconda3\python.exe -m pytest tests/skills/test_discovery.py tests/skills/test_contracts.py tests/skills/test_registry.py -q --tb=line
```

`tests/skills` 全套结果见 `M3_M4_TEST_REPORT.md`。发现专项 `tests/skills/test_discovery.py` 4 项通过，包含在 25 项通过之内。

| ID | 结果 | 证据 |
|---|---|---|
| D-01 | PASS | `test_discovery_does_not_read_skill_bodies` 在发现期间 `SKILL.md` 读取次数为 0 |
| D-02 | PASS | 同文件的双用户显式指定返回 `SKILL_NOT_FOUND` |
| D-03 | PASS | `test_disabled_skill_is_not_selected_and_load_rechecks` |
| D-04 | PASS | 无关问题返回空候选 |
| D-05 | PASS | 两次发现的排序一致 |
| D-06 | PASS | `reason_codes` 含 `name_exact` 或 `description` |
| D-07 | PASS | disabled、recommended、explicit、auto 分支都有断言 |
| D-08 | PASS | 推荐模式 `selected_ids` 为空，发现阶段读取次数为 0 |
| D-09 | PASS | 禁用后加载返回 `SKILL_NOT_AVAILABLE` |
| D-10 | PASS | `test_legacy_prompts_stay_in_place`，`app/prompts/skills.py` 未改 |

结论：M3 验收 PASS。这不是生产就绪。
