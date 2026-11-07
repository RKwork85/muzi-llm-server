# Runtime Contracts and Detailed Behavior

## 1. Tool specification

A tool registration should be explicit and immutable after application startup unless the existing architecture already supports safe lifecycle updates.

Conceptual type:

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    parameters_schema: dict[str, object]
    args_model: type[BaseModel]
    timeout_seconds: float
    max_result_chars: int
    enabled: bool = True
    risk_tier: Literal["read", "write", "external"]
```

The real implementation may use dataclasses/protocols for callables, but external/untrusted arguments must pass Pydantic validation. Do not expose Python class names or import paths to the model.

Tool schema requirements:
- unique names;
- stable JSON Schema;
- argument root must be an object;
- reject additional properties;
- no arbitrary code or expression fields;
- validate schema generation in tests;
- use only JSON-serializable output.

## 2. Tool execution contract

Input:
- tool name from model;
- raw JSON argument string;
- run-level allowlist;
- execution deadline.

Output:
- `tool_call_id`;
- `tool_name`;
- `status`: succeeded/failed/timed_out/rejected;
- normalized JSON result or safe error object;
- duration_ms.

Rules:
1. Exact registry lookup.
2. Check request allowlist and server-side enabled flag.
3. Strict JSON parse; reject NaN/Infinity and non-object payloads.
4. Pydantic validation; reject extra fields.
5. Run under min(tool timeout, remaining run deadline).
6. Sync handler must not block the event loop; use bounded offload.
7. Async handler awaited directly.
8. Bound serialized output; truncation must preserve valid JSON or return a structured truncation envelope.
9. Never echo untrusted raw arguments into logs.
10. Never retry a tool automatically.

## 3. Agent loop algorithm

```text
initialize messages from trusted server prompt + user message
for step in 1..policy.max_steps:
    ensure deadline and context budget
    ask provider for assistant response with only allowed tool schemas
    if provider returned final assistant content and no tool calls:
        return completed
    if provider returned tool calls:
        validate count against per-step and total limits
        append assistant tool-call message in provider-compatible format
        for each tool call:
            execute through ToolExecutor
            append matching role=tool message with original tool_call_id
            publish safe tool event
        continue
    otherwise:
        fail with INVALID_MODEL_RESPONSE
return limit_reached
```

Implementation must use the actual SDK/provider response shape, not assume the illustrative pseudocode matches it. Confirm DeepSeek-specific message fields and serialization behavior from official docs and current tests.

### Multiple tool calls
If the model returns several calls in one assistant message:
- enforce `max_tool_calls_per_step` and remaining total budget before executing any call;
- validate each independently;
- preserve original call order when appending results;
- do not execute calls in parallel unless tool metadata explicitly marks safe parallelism and a later reviewed requirement approves it. Default sequential execution is safer and easier to reason about.

### Thinking mode
The first phase documents that DeepSeek thinking-mode tool calls require `reasoning_content` to be carried into subsequent requests. Preserve it only as opaque provider protocol data when thinking is enabled. It must not be surfaced in response schemas, SSE events, logs, traces, or error messages. Do not claim that prompt instructions can remove or secure model reasoning.

## 4. Context management

Phase 2 context handling is deterministic:
- trusted system prompt first;
- current conversation state;
- newest user message retained;
- tool-call/result groups retained atomically.
- apply max message and serialized character budgets.
- do not cut JSON tool arguments or tool results mid-string.
- do not use an LLM to summarize or rewrite history in this phase.
- if limits cannot be met without breaking protocol or dropping mandatory content, return a controlled context-limit outcome.

Token estimation is provider/model-dependent. If no tokenizer is already present, use a documented conservative character budget rather than adding a heavyweight tokenizer dependency.

## 5. Policy precedence

Effective limits:

`effective_limit = min(server_hard_limit, configured_server_limit, request_requested_limit_if_present)`

A request may reduce but never increase limits or add tools outside the server allowlist. The server decides the default permitted tool set. If the request omits `allowed_tools`, use a conservative server-owned default profile.

## 6. Task lifecycle and concurrency

- Every run gets a cryptographically random opaque ID.
- A task record is created before execution.
- Status transitions are atomic within the process/event loop.
- Terminal status cannot be overwritten.
- In-memory task storage has a maximum record count and TTL cleanup, or another bounded strategy.
- Event queue capacity is bounded.
- Do not claim cross-worker visibility or persistence.
- Do not create background tasks that outlive request ownership unless lifecycle and shutdown cancellation are explicitly implemented.
- If using FastAPI streaming response, ensure the producer is cancelled and awaited on disconnect.
- Use `try/finally` cleanup.
- Propagate `asyncio.CancelledError`.

## 7. API validation

Request validation must enforce:
- message non-empty and bounded;
- prompt_name references an existing server-side prompt;
- allowed_tools contains unique known names only (or return controlled not-allowed error);
- policy values are positive and no larger than server ceilings;
- clients cannot submit `system`, `developer`, or `tool` messages as trusted roles;
- clients cannot supply raw function schemas or handler definitions.

## 8. Event payload privacy

Allowed event data examples:
- step number;
- tool name;
- duration;
- safe status;
- public answer on completion;
- normalized error code/message.

Disallowed:
- reasoning_content;
- API key or Authorization;
- raw stack trace;
- full unredacted user prompt in logs;
- raw tool arguments;
- unrestricted tool output;
- internal environment variables.
