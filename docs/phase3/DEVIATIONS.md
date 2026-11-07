# Phase 3 与 Skill 的差异

以真实代码为准。无新依赖。

## 已对照的官方文档

2026-11-06 重新读取了 Search 指南、Search API、Fetch API。与 `references/04_DEEPSEEK_SEARCH_FETCH_CONTRACT.md` 没有字段级差异。

## 工具元数据

Skill 想把 `risk_level` 改成 `read_local` / `read_network` / `read_files` / `write_local` / `write_external`。

真实代码和测试把 `risk_level="low"` 当作“允许注册和执行”，`high` 会在注册时被拒绝。替换这个字段会破坏 Phase 1 / M3 测试。

决定：保留 `risk_level="low"`。新增 `access`（`read_local`、`read_network`、`read_files`）。`write_local` 和 `write_external` 在注册时拒绝。其余 Skill 字段（`requires_network`、`timeout_s`、`max_result_chars`、`max_calls_per_run`、`idempotent`、`toolset`）加在 `ToolSpec` 上，并带默认值，现有工具构造不用改。

## 请求里的工具选择

Skill 写“请求不能指定单个工具”。M3 已经有可选的 `allowed_tools`，并且只能收紧。该字段保留。新增可选 `toolsets`。有效工具集是所选 toolset 的并集，再用 `allowed_tools` 收紧。

## toolset 划分

`01` 把 T1a 的本地工具放进 `diagram`。T0 改成独立的 `utility` toolset，始终可用、无网络、无文件。`diagram` 只放三个 Mermaid 工具。这样画图和文本工具可以分开选择。默认请求仍然只有 `basic`。

## 注册表实例

`build_default_registry()` 仍只注册 `calculator` 和 `get_current_time`。Phase 1 的 `ToolService` 在未指定工具名时会把注册表里的全部工具交给模型，现有测试也锁定了这两个名字。

Agent 使用同一个 `ToolRegistry` 类的另一个实例 `build_agent_registry()`。这样 Phase 1 的默认工具列表不变，Agent 才能按 toolset 暴露新工具。

## 代码位置

不新建第二套注册表。元数据留在 `app/tools/registry.py`。toolset 解析放在 `app/agents/toolsets.py`。业务工具仍按阶段放在 `app/tools/` 下，不搬进 Skill 建议的 `app/agents/tools/`。

## 工具结果信封

执行器已经把成功结果包成 `source/name/result` 字符串信封。artifact 放在 `result` 里的 JSON 中，由循环提取。发给模型的 tool message 只保留 artifact 的 id、type、size。事件白名单只能收字符串和数字，因此事件带 `artifact_id`、`artifact_type`、`artifact_size`、`artifact_count`，不带正文，也不带列表。

## 响应兼容

`artifacts` 为空时不出现在 JSON 里，避免默认响应比 M3 多一个 `null` 字段。有产物时才出现。

## 依赖

`httpx` 已在 `requirements.txt` 中声明，T2 不再新增。

## Mermaid

节点 `key` 不使用 Skill 里的 ASCII 正则。保留字、中文和 emoji 都要能作为边的引用，输出仍然只用 `n1`、`p1` 这类内部 ID。key 限制为 1 到 32 个字符，并排除空白、引号、反引号、竖线和尖括号。

转义对照 Mermaid 官方 flowchart 与 sequenceDiagram 页面：双引号用示例中的 `#quot;`，`#` 用页面写明的十进制 `#35;`，序列图分号用页面写明的 `#59;`。`<`、`>`、`&` 使用同一页允许的 HTML 实体名。`|` 和反引号会破坏边标签或 Markdown 文本，分别用十进制 `#124;`、`#96;`。

发给模型的 tool message 去掉完整 `mermaid` 字段，只留摘要和 artifact id。完整源码在响应的 `artifacts` 里。这样上下文不会再放一份最长 20000 字符的图。

## 服务端渲染

不实现 Mermaid SVG 渲染，也不引入 Node。客户端自行渲染，并应使用 strict 安全级别。

## 文件工具与同步上下文

文件工具放在 `app/tools/file_sandbox.py` 和 `app/tools/file_tools.py`，不搬到 Skill 建议的 `app/agents/tools/files/`。

同步工具走现有 `max_workers=2` 的线程池。线程池不会自动看到当前任务的 `ContextVar`，因此执行器在提交前复制上下文。文件工具由此读到本次运行的工作区配置和剩余时间，不另建线程池。

非 80/443 端口在 URL 预校验时拒绝。这比“可配置额外端口”更紧，当前没有端口白名单配置项。

本机无法创建符号链接。逃逸测试被跳过，并由 `confined()` 的 `root` / `root_evil` 路径解析单测替代。记录见实施日志。
