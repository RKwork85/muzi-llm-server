# A05–A07 功能审计

客户端能力声明是空对象，并且会拒绝 resources、prompts、sampling。Resources 和 Prompts 按“未声明且未实现”记录，不单独构成协议缺陷。Tools 是项目实际交付的能力。

## A05 Tools

| ID | 项 | 等级 | 结果 | 证据 |
|---|---|---|---|---|
| A05-01 | `tools/list` | MUST | PASS | stdio `McpSession.start`；远程 `McpService._list`。两个测试文件都断言工具名 |
| A05-02 | `tools/call` | MUST | PASS | 两边都发送 `name` 与 `arguments`。stdio、Streamable HTTP、Legacy SSE 均有成功调用测试 |
| A05-03 | 名称与描述 | SHOULD | PARTIAL | 名称有字符白名单。描述去掉不可打印字符并截到 240。换行被压成空格，远端 schema 里的 description 被丢弃 |
| A05-04 | Input Schema | SHOULD | PARTIAL | `_schema` 只保留最多 32 个简单 type，强制 `additionalProperties: false`。enum、format、数值范围、嵌套、`$ref`、描述都丢失。`test_registration_keeps_only_safe_allowlisted_tools` 证明这种裁剪是故意行为 |
| A05-05 | JSON Schema 2020-12 | SHOULD | NOT_IMPLEMENTED | 2026-07-28 讨论的 2020-12 行为没有实现，也没有校验方言 |
| A05-06 | Output Schema / annotations | SHOULD | NOT_IMPLEMENTED | 不读取 `outputSchema`、`annotations`、`readOnlyHint`、`destructiveHint` |
| A05-07 | 分页与列表变更通知 | MAY | NOT_IMPLEMENTED | 不发送 cursor，不处理 `notifications/tools/list_changed` |
| A05-08 | 结果 content | MUST | PARTIAL | 只拼接 `type=text`。image、audio、resource、`structuredContent` 被丢弃。`isError: true` 变成执行失败，不会当成正常文本。测试覆盖了 text 和 `untrusted` |
| A05-09 | 参数校验 | MUST | PARTIAL | 缺必填字段时 stdio 测试得到 `InvalidToolArgumentsError`。类型、枚举和长度不按远端 schema 检查。`McpArgs` 是 `extra=allow` |
| A05-10 | 执行前白名单 | PROJECT | PASS | stdio 还要过 `mcp_tool_allowlist`。远程还要过用户 `selected_tools`，且 `delete_page` 在 `allow_write=false` 时不会被选中。测试断言 secret 工具不能执行 |
| A05-11 | 写入识别 | PROJECT | FAIL | 只靠工具名正则 `(create|update|delete|insert|remove|write|append|patch|move|trash|destroy)`。`drop_database`、`send_email`、`execute` 不会被当成写入。stdio 工具一律按配置标成 `read_local` 或 `read_network`，并强制 `idempotent=True` |
| A05-12 | 结果不可信 | PROJECT | PASS | 返回 JSON 含 `untrusted: true`。agent 模式附加 `UNTRUSTED_PROMPT`（`app/tools/web_tools.py`）。远程文本没有再走 `_plain`，控制字符仍可进入模型上下文 |

## A06 Resources

| ID | 项 | 结果 | 说明 |
|---|---|---|---|
| A06-01 | `resources/list`、`resources/read`、订阅、模板 | NOT_IMPLEMENTED | 客户端拒绝这些方法。项目没有声明该能力。与当前 tools-only 定位一致 |
| A06-02 | 若将来打开 Resources | PROJECT | 目前没有 URI 校验、路径限制或跨用户资源测试。不能把“未实现”写成“读取是安全的” |

## A07 Prompts

| ID | 项 | 结果 | 说明 |
|---|---|---|---|
| A07-01 | `prompts/list`、`prompts/get` | NOT_IMPLEMENTED | 与 Resources 相同，被显式拒绝 |
| A07-02 | MCP Prompt 不得升格为系统指令 | N/A | 没有把 MCP Prompt 注入系统提示。现有系统提示来自 `PromptService` 和模式提示，不来自 MCP prompts/get |

## 结论

Tools 的发现和调用是可用子集，schema 和安全语义被大幅削减。Resources、Prompts 未实现，且没有被错误声明。功能维度是 PARTIAL，不是完整 MCP 功能面。
