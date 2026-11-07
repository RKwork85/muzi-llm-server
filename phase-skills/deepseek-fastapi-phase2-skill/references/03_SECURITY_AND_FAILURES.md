# Security, Threat Model, and Failure Handling

## 1. Trust boundaries

Treat all of the following as untrusted:
- user messages;
- model-generated tool names and arguments;
- tool outputs;
- external data returned by future integrations;
- prompt content supplied through any client-controlled field.

Only server code may define tool permissions, schemas, and handlers.

## 2. Required controls

### Least privilege
- Only explicitly registered tools are callable.
- Per-run allowlist is an intersection with server policy.
- No shell, Python execution, arbitrary file operations, unrestricted URL fetch, or arbitrary SQL.
- High-impact tools are out of scope. Future write-capable tools require explicit user confirmation and a separate authorization design.

### Input/output validation
- Parse tool arguments as strict JSON.
- Validate with Pydantic.
- Reject unexpected keys and wrong types.
- Normalize tool output to JSON-safe values.
- Bound output size.
- Treat tool output as data, not instructions.

### Resource limits
- max agent steps;
- max total tool calls;
- max tool calls per model response;
- max wall-clock runtime;
- provider timeout;
- per-tool timeout;
- max context messages/chars;
- max tool result chars;
- bounded event queue;
- bounded in-memory task records;
- optional request rate limiting only if existing infrastructure supports it without adding a new external service.

### Cancellation
- Client disconnect cancels the run.
- Cancellation propagates to provider and active tool where possible.
- `CancelledError` is not swallowed.
- Cleanup runs in `finally`.
- No terminal success event after cancellation.

### Logging
Log only metadata:
- request_id, task_id;
- step number;
- tool name;
- elapsed time;
- result status/error code;
- model and token usage if available.

Never log:
- API keys;
- Authorization headers;
- complete prompts/messages;
- reasoning content;
- raw tool arguments;
- raw tool outputs if they may contain sensitive data.

## 3. Failure matrix

| Failure | Expected behavior |
|---|---|
| Provider auth error | controlled failed result / error event |
| Provider rate limit | controlled failure; no uncontrolled retry |
| Provider timeout | deadline/timeout error |
| Provider 5xx | controlled upstream failure |
| Malformed model response | `INVALID_MODEL_RESPONSE` |
| Unknown tool | reject, record safe tool failure, do not dispatch |
| Tool arguments invalid | reject before handler invocation |
| Tool timeout | cancel/timeout handler, return safe failure |
| Tool exception | sanitized error, no stack trace to client |
| Tool output too large | valid bounded JSON envelope |
| Step/tool limit | terminal `limit_reached` |
| Context budget exceeded | terminal controlled limit |
| Event queue full | explicit controlled overflow/backpressure |
| Client disconnect | cancel and clean up |
| App shutdown | cancel owned active tasks and close resources |

## 4. Prompt injection expectations

Do not promise to “solve” prompt injection. The runtime's security must not depend on model obedience or system prompt wording. Use code-enforced allowlists, strict argument validation, least privilege, bounded outputs, and human approval for future high-impact actions.

The tool output boundary must be explicit: tool results are untrusted observations. A malicious result that says “ignore previous instructions” must not change the server-side tool allowlist or execution policy.

## 5. No unsafe shortcuts

Forbidden:
- `eval()` / `exec()`;
- shell command tools;
- dynamic imports from model output;
- arbitrary `getattr()` dispatch;
- automatic retry of side-effecting tools;
- trusting model-generated tool names without registry lookup;
- exposing chain-of-thought;
- unbounded asyncio queues;
- unbounded task dictionaries;
- catching `BaseException` and continuing;
- swallowing `asyncio.CancelledError`.
