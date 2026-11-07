# Phase 3 验收记录

日期：2026-11-06。Skill 清单文件没有预先勾选。下面是本次真实结果。

## 开始前

- 项目根是 `C:\Users\rkwork\Desktop\muzihhh\work\muzi_project\qs_llm_dp`。
- 开始时分支 `main`，HEAD `2c5d97f`，工作区没有已跟踪文件的 diff。未跟踪的 `phase-skills.zip` 和 Skill 目录保持原样，没有提交。
- 改代码前的全量测试：`152 passed, 6 skipped in 5.65s`。
- 2026-11-06 重新读取了 DeepSeek Search 指南、Search API、Fetch API，与 `references/04` 没有字段级差异。

## T0 到 T3

- 元数据有默认值。`calculator` 与 `get_current_time` 仍可按原构造注册。`risk_level` 保持 `low`，访问级别放在新增的 `access`。差异见 `DEVIATIONS.md`。
- `write_local` / `write_external` 注册失败。
- 未知 toolset 为 `TOOLSET_UNKNOWN`，不可用为 `TOOLSET_UNAVAILABLE`，没有静默降到 `basic`。
- 默认响应的字段集与 M3 相同；空 `artifacts` 不出现。
- 每工具调用上限、网络闸门、排队计入预算、异步联网工具不进同步线程池，均有测试。
- Mermaid 同输入逐字节稳定；保留字、中文和 emoji 使用内部 ID；转义由测试锁定；`guarantees_render` 为 false。
- Search 请求体包含 `need_content=false` 和 `count`，不含 `localization`。Fetch 请求体只有 `url`。
- 状态码 400/401/402/403/404/408/413/429/500/504 已映射。超大响应、空结果、非 http(s) 结果、缺字段和非 JSON 会安全失败。
- 默认不重试。预算为 0 时即使允许 1 次重试也不再发出第二次请求。取消会继续抛出 `CancelledError`。
- 文件路径穿越、盘符、UNC、冒号、设备名、超长路径、敏感文件和 `root_evil` 已测试。
- 符号链接：本机无法创建，测试跳过，并由路径解析单测替代。
- `search_text` 把 `a.b` 当字面量。没有写入接口。

## T4 / T5

- `docs/phase3/T4_T5_DESIGN.md` 已写。
- 没有业务 API、数据库、MCP 服务端或 MCP SDK。
- 没有安装 Node.js。本机原本已有 Node `v24.11.1`，本次没有启动 Everything。
- 设计里包含补充阶段测试方案，并列出需要用户决定的问题。

## T6

- `SECURITY_REVIEW.md`、README、`.env.example` 已更新。示例没有密钥。
- 能力归属已执行，见 `CAPABILITY_ATTRIBUTION.md`。对照和实验各 3 次。
- 全量离线测试：

```text
.\.venv\Scripts\python.exe -m pytest -q --tb=line
195 passed, 9 skipped in 46.70s
```

9 个跳过是：`DEEPSEEK_LIVE!=1` 的 6 个对话联调、1 个能力归属；`WEB_LIVE!=1` 的 1 个 Search/Fetch；Windows 无法创建符号链接的 1 个文件测试。

- Live：`tests/test_live_web.py` 为 `1 passed in 11.28s`，一次搜索和一次抓取。能力归属为 `1 passed in 28.54s`。
- `git diff --check` 退出码 0，没有空白错误。

## 已知限制

- 同步线程超时后仍占线程池名额，不能强杀。
- 任务只存在于当前进程。
- 本地不能证明 DeepSeek 侧的 SSRF 防护。
- `WEB_FETCH_URL_POLICY=any` 会取消来源限制。
- 本机没有做成真实符号链接逃逸实验。
- 能力归属只覆盖这 6 次请求，模型输出不稳定。实验组有两次同时调用了 `validate_mermaid`，因此出现两份 artifact。
