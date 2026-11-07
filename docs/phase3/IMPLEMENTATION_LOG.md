# Phase 3 实施记录

分支 `main`。开始时 HEAD 为 `2c5d97f`，已跟踪文件没有未提交 diff。`phase-skills.zip` 和 Skill 目录原本就是未跟踪文件，仍未提交。

改代码前的全量测试：

```text
.\.venv\Scripts\python.exe -m pytest -q
152 passed, 6 skipped in 5.65s
```

## T0 工具框架

没有新增业务工具。`ToolSpec` 增加可选元数据，默认策略只含 `basic`，空 `artifacts` 不出现在响应里。

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=line
163 passed, 6 skipped in 4.30s
```

失败数为 0。6 个跳过是 `DEEPSEEK_LIVE != 1`。

## T1 本地工具与 Mermaid

新增 `utility` 与 `diagram` 工具，以及 artifact 提取。该阶段结束时的全量结果是 `171 passed, 6 skipped in 4.28s`。后续全量测试仍包含这些用例。

## T2 联网工具

`DeepSeekWebClient` 使用 MockTransport。默认不重试，来源策略默认 `provenance`。

离线专项最后一次：`tests/agents/test_web_tools.py` 为 `19 passed in 0.33s`。

真实 Search 与 Fetch 已执行，各一次：

```text
tests/test_live_web.py
1 passed in 11.28s
```

输出没有打印密钥。

## T3 文件工具

工作区目录是 `agent_workspace\.gitkeep`。根目录不能作为工作区。文件工具走现有同步线程池，执行器提交前复制上下文。

专项：`35 passed, 1 skipped in 2.62s`（含原有执行器测试）。跳过的是符号链接：本机无法创建链接，已用 `root` / `root_evil` 解析单测替代。

包含文件工具后的全量结果：

```text
192 passed, 8 skipped in 46.49s
```

## T4 / T5

只写了 `docs/phase3/T4_T5_DESIGN.md`。没有安装 Node，也没有启动 MCP。本机原本已有 Node `v24.11.1`。

## T6

补了空结果、非 http(s) 结果、坏响应、预算耗尽不重试、取消传播，以及能力归属测试。更新了 README、`.env.example`、安全复核和验收记录。

最终离线全量：

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=line
195 passed, 9 skipped in 46.70s
```

失败数为 0。9 个跳过分别是 6 个对话 live、1 个网页 live、1 个能力归属 live，以及 1 个符号链接测试。

能力归属已执行：`tests/test_live_capability.py` 为 `1 passed in 28.54s`。对照和实验各 3 次，记录在 `CAPABILITY_ATTRIBUTION.md`。第一次轮询没有等待，0.66 秒即中断，没有终态，不计入这 6 次。

```text
git diff --check
```

退出码 0。

风险：同步线程仍不能强杀；任务仍只在本进程；DeepSeek 侧 SSRF 不能由本地测试证明。
