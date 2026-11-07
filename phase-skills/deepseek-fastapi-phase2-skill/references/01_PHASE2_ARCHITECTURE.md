# Phase 2 Architecture

## 1. Purpose

Phase 1 is a model access service. Phase 2 adds a controlled execution runtime above the existing model provider. The runtime owns the agent lifecycle; the model proposes responses/tool calls but does not execute tools and cannot grant itself permissions.

## 2. Recommended module layout

Adapt names to the repository's current conventions after inspection. Suggested additive structure:

```text
app/
  agents/
    __init__.py
    runtime.py              # AgentRuntime orchestration
    loop.py                 # bounded model/tool loop
    policy.py               # ExecutionPolicy
    context.py              # ContextManager
    task_manager.py         # in-process task state
    events.py               # typed event envelope/publisher
    registry.py             # ToolRegistry
    executor.py             # ToolExecutor
    protocols.py            # provider/tool protocols if needed
  api/v1/endpoints/
    agents.py               # thin HTTP endpoints
  schemas/
    agents.py               # external and internal Pydantic schemas
  services/
    ...                     # reuse Phase 1 service patterns
tests/
  agents/
    test_policy.py
    test_registry.py
    test_executor.py
    test_context.py
    test_loop.py
    test_runtime.py
    test_agent_api.py
    test_agent_stream.py
docs/
  phase2/
    BASELINE.md
    IMPLEMENTATION_PLAN.md
    ARCHITECTURE.md
    API_CONTRACT.md
    SECURITY_REVIEW.md
```

Do not blindly create this tree if the existing repository already has a better matching structure. Avoid duplicate `ToolRegistry`, `ToolExecutor`, or provider abstractions if equivalent reusable code exists.

## 3. Dependency direction

```text
HTTP Endpoint
    |
    v
AgentRuntime -----> TaskManager
    |                    |
    v                    v
AgentLoop ----------> EventPublisher
    |
    +----> ContextManager
    |
    +----> ExecutionPolicy
    |
    +----> ToolExecutor ----> ToolRegistry ----> existing tool handlers
    |
    +----> existing DeepSeekProvider
```

Domain/runtime code must not import FastAPI request/response objects. API layer may depend on runtime; runtime must not depend on API layer.

## 4. Responsibilities

### AgentRuntime
- Accept a validated run request and a generated task ID.
- Create a task record and establish a deadline.
- Start the loop, publish lifecycle events, and collect the final answer.
- Convert controlled failures into typed terminal outcomes.
- Ensure one terminal state and cleanup.
- Does not know HTTP status codes.

### AgentLoop
- Calls the existing provider with the current bounded message list and allowed tool schemas.
- Interprets assistant response structure.
- Routes proposed tool calls to ToolExecutor.
- Appends assistant tool-call and tool-result messages in provider-required format.
- Stops on final assistant answer or policy limit.
- Does not own HTTP/SSE formatting.

### ToolRegistry
Each tool entry contains:
- stable unique name;
- human-readable description;
- JSON Schema exposed to the model;
- Pydantic argument model;
- handler callable;
- execution metadata (timeout, max output, risk tier, enabled state).

The registry is the source of authority. Never dispatch with `globals()`, `getattr()` on arbitrary names, import paths from model output, or dynamic code execution.

### ToolExecutor
- Look up tool by exact registered name.
- Check run-level allowed tool names and tool enabled state.
- Parse JSON strictly; require an object; reject unknown keys.
- Validate with the registered Pydantic model.
- Apply per-tool timeout and run deadline.
- Invoke async handler directly; run synchronous handler in a bounded thread pool.
- Normalize output to JSON-safe data and enforce character/byte limit.
- Return a typed result with success/failure metadata.
- Do not retry.

### ContextManager
- Maintain the conversation state for one run.
- Preserve protocol correctness: assistant tool-call message and every corresponding tool result must remain associated.
- Enforce max message count and estimated serialized character/token budget.
- Never trim system/developer instructions or the latest user message.
- Never leave a tool result without its assistant tool-call message.
- Never remove only one member of a tool-call/result group.
- If the context cannot be made safe within limits, stop with a controlled `CONTEXT_LIMIT_REACHED` outcome. No model-based summarization in Phase 2.

### ExecutionPolicy
Minimum fields:
- `max_steps`
- `max_tool_calls`
- `max_tool_calls_per_step`
- `max_wall_time_seconds`
- `model_timeout_seconds`
- `tool_timeout_seconds`
- `max_context_messages`
- `max_context_chars`
- `max_tool_result_chars`
- `max_event_queue_size`
- `allowed_tools`
- optional `max_model_tokens` only if supported by the current provider API.

Validate all positive limits and hard upper bounds. Server configuration defines ceilings; request-level policy may only tighten limits, never loosen server ceilings.

### TaskManager
In-process only for Phase 2:
- task_id, request_id, status, created_at, started_at, completed_at;
- current step, tool call count, safe result summary/error code;
- bounded event queue or event subscription.
- No durable persistence or cross-process guarantees.
- Clearly document that tasks disappear on process restart and are not shared across multiple workers.
- Do not expose an endpoint that implies durable task retrieval unless its semantics are explicitly documented.

### EventPublisher
- Typed events and monotonically increasing sequence per task.
- UTC timestamps.
- Bounded queue and deterministic overflow handling.
- Public event data is allowlisted and sanitized.
- Never publish reasoning content, API secrets, raw stack traces, or unrestricted tool payloads.

## 5. API design (additive proposal)

### POST /api/v1/agents/run

Request (illustrative; final schema must be aligned to existing Pydantic conventions):

```json
{
  "message": "Calculate 23 * 19 and explain the result.",
  "prompt_name": "calculator",
  "allowed_tools": ["calculator"],
  "policy": {
    "max_steps": 5,
    "max_tool_calls": 3
  }
}
```

Response (illustrative):

```json
{
  "task_id": "opaque-id",
  "request_id": "request-id",
  "status": "completed",
  "answer": "23 * 19 = 437.",
  "steps": 2,
  "tool_calls": 1,
  "usage": null
}
```

The request must not accept arbitrary tool definitions or handler code. `allowed_tools` is an intersection with the server registry, never a way to add permissions. Prefer server-side named agent profiles in a later phase; for Phase 2, keep one default profile.

### POST /api/v1/agents/run/stream

Use POST with a request body. Do not assume browser `EventSource` supports arbitrary POST bodies; clients may use `fetch()` streaming. Use `text/event-stream` and the project's established framing helper.

Event envelope:

```json
{
  "request_id": "request-id",
  "task_id": "opaque-id",
  "sequence": 1,
  "timestamp": "2026-11-06T00:00:00Z",
  "type": "agent.started",
  "data": {}
}
```

Event order:
1. `agent.started`
2. zero or more `agent.step.started`
3. zero or more tool lifecycle events
4. exactly one terminal event: `agent.completed`, `agent.failed`, `agent.cancelled`, or `agent.limit_reached`

`agent.model.delta` is optional and may be emitted only if the actual provider streaming interface can safely support it. Do not simulate token streaming by sending a completed response as deltas.

No new task retrieval/cancel endpoint is required in this phase unless a real use case and in-process semantics are documented. Client disconnect must cancel the run unless the implementation explicitly documents a different policy. Prefer cancel-on-disconnect for this phase.

## 6. State machine

```text
created -> running
running -> completed
running -> failed
running -> cancelled
running -> limit_reached
```

Terminal states are immutable. Reject illegal transitions. Exactly one terminal event must be attempted. If the client disconnects, internal cancellation cleanup still runs; do not attempt to send an event to a dead connection.

## 7. Error model

Reuse the Phase 1 error envelope and existing error translation patterns where possible. Add Agent-specific error codes without altering existing meanings:
- `AGENT_STEP_LIMIT_REACHED`
- `AGENT_TOOL_CALL_LIMIT_REACHED`
- `AGENT_DEADLINE_EXCEEDED`
- `AGENT_CONTEXT_LIMIT_REACHED`
- `AGENT_CANCELLED`
- `AGENT_EVENT_OVERFLOW`
- `AGENT_INVALID_STATE`
- `AGENT_TOOL_NOT_ALLOWED`

Map request validation errors consistently with existing API behavior. Do not leak provider exception text or stack traces.
