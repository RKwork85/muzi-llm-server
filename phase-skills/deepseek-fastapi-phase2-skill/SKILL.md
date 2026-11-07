---
name: deepseek-fastapi-phase2-agent-runtime
description: Build Phase 2 Harness / Agent Runtime on top of the frozen DeepSeek FastAPI Phase 1 v0.1.0 service. Use when implementing, reviewing, testing, or extending the Agent Runtime from the current project root.
---

# DeepSeek FastAPI Phase 2 — Cursor Skill

## Mission

Implement Phase 2, **Harness / Agent Runtime**, inside the current project root.

The project root is the directory that contains `app/`, `tests/`, `docs/phase2/`, and `phase-skills/deepseek-fastapi-phase2-skill/`. Resolve every relative path and command from that directory. Do not use the retired nested path `qs-test/qs_llm_dp`, and do not switch to any older checkout. The test command stays `.\.venv\Scripts\python.exe -m pytest -q`.

Phase 1 is a frozen baseline at `v0.1.0` (2026-11-06). It already provides:
- DeepSeek Chat Completions through an AsyncOpenAI-based provider.
- Non-streaming and SSE chat endpoints.
- Server-owned prompt templates.
- Controlled Function Calling with `calculator` and `get_current_time`.
- Configuration, request IDs, structured errors, logging, and tests.

Phase 2 adds a reusable execution engine that can run bounded, observable, policy-controlled agent tasks. It must reuse the Phase 1 provider and shared infrastructure. Do not rebuild model connectivity.

## Mandatory first actions

1. Inspect the repository tree, `README.md`, `requirements.txt`, `.env.example`, all existing provider/service/schema/config/error modules, and tests.
2. Run the existing test suite before editing:
   `.\.venv\Scripts\python.exe -m pytest -q`
3. Record the exact baseline result and current Git status.
4. Read `references/01_PHASE2_ARCHITECTURE.md`, `references/02_RUNTIME_CONTRACTS.md`, `references/03_SECURITY_AND_FAILURES.md`, `references/04_TEST_AND_ACCEPTANCE.md`, and `references/05_OFFICIAL_REFERENCES.md`.
5. Produce a short implementation plan that maps each proposed new file to the existing repository conventions.
6. If a required existing abstraction differs from this specification, adapt to the real code and document the difference. Never invent an existing class or method.
7. Do not start implementation until the baseline and plan are recorded.

## Non-negotiable compatibility rules

- Do not change, remove, rename, or reinterpret Phase 1 routes:
  - `POST /api/v1/chat/completions`
  - `POST /api/v1/chat/completions/stream`
  - `POST /api/v1/chat/tool-calls`
  - `GET /api/v1/models`
  - `GET /health`
  - `GET /ready`
- Do not change Phase 1 request/response schemas, error codes, SSE event names/order, built-in tool names, or prompt behavior.
- New functionality must use additive modules and additive routes. Proposed new routes:
  - `POST /api/v1/agents/run`
  - `POST /api/v1/agents/run/stream`
- If the existing project already uses a different versioning convention, preserve its conventions while keeping all Phase 1 paths untouched.
- Do not silently modify the Phase 1 provider contract. If an adapter method is missing, add a backward-compatible method or a dedicated adapter in Phase 2.
- Keep Phase 1 tests passing after every implementation milestone.

## Phase 2 scope

Implement these components:

1. **AgentRuntime** — orchestrates one agent run, applies policy, manages lifecycle and final result.
2. **AgentLoop** — repeatedly requests the next model response, validates tool calls, executes authorized tools, appends tool results, and stops on a terminal condition.
3. **ToolRegistry** — owns tool definitions, schemas, metadata, and handler lookup. Model output never grants permission.
4. **ToolExecutor** — validates arguments, applies per-tool timeout and result limits, invokes async/sync handlers safely, normalizes failures.
5. **ContextManager** — builds and bounds model context, preserves tool-call protocol pairs, and prevents unbounded message growth.
6. **ExecutionPolicy** — centralizes max steps, max tool calls, wall-clock deadline, per-tool timeout, allowed tools, and context/result budgets.
7. **TaskManager** — tracks in-process task lifecycle and status for this phase only; no database, Redis, RQ, Celery, or durable recovery.
8. **EventPublisher** — emits typed lifecycle events to the stream route without leaking private reasoning or secrets.
9. **Agent API** — additive run and stream endpoints, validated request/response schemas, unified errors and request IDs.

## Explicitly out of scope

Do not implement:
- multi-agent orchestration;
- autonomous task planning trees / DAG planners;
- MCP client/server;
- RAG, embeddings, vector databases, or long-term memory;
- database persistence, Redis, RQ, Celery, distributed workers;
- login, billing, tenant permissions, frontend UI;
- shell execution, arbitrary Python execution, `eval`, `exec`;
- arbitrary filesystem access or unrestricted HTTP/network tools;
- browser automation;
- DeepSeek Responses API or Beta strict tool calling;
- automatic retries of tool side effects;
- hidden chain-of-thought exposure.

Do not add a framework such as LangChain or LangGraph in this phase. Implement the small, explicit runtime directly on the existing FastAPI/Pydantic/provider stack. This keeps behavior inspectable and avoids an unnecessary framework migration.

## Required implementation sequence

Implement one milestone at a time. After each milestone:
- run focused tests;
- run the full existing test suite;
- report changed files and test output;
- do not proceed with failing tests.

### M0 — Baseline and design
Deliver `docs/phase2/BASELINE.md` and `docs/phase2/IMPLEMENTATION_PLAN.md`.
No production code changes.

### M1 — Domain contracts and policy
Create typed models/enums for agent request, result, task status, tool specification, tool execution result, and event envelope. Implement `ExecutionPolicy` validation and defaults. Add unit tests.

### M2 — Tool registry and executor
Register the existing `calculator` and `get_current_time` tools through adapters if their current contracts permit it. Do not duplicate implementations. Validate JSON arguments with Pydantic, reject unknown fields, enforce allowlist, timeout, output limits, and structured errors. Add tests for unknown tool, invalid arguments, timeout, exception, oversized output, sync/async handler behavior.

### M3 — Context manager
Implement safe message assembly and context budgeting. Preserve assistant tool-call messages and matching tool results as protocol pairs. Never truncate away a tool-call ID or leave orphan tool messages. Do not summarize context using another model in this phase. Add tests for message limits and tool-pair integrity.

### M4 — Agent loop
Implement bounded model/tool iteration using the actual Phase 1 provider interface. Handle:
- assistant final answer;
- one or more tool calls;
- malformed tool arguments;
- unknown/disallowed tool;
- model response with neither content nor tool calls;
- provider timeout/auth/rate-limit/upstream errors;
- maximum step/tool-call/deadline reached;
- cancellation.

Preserve any provider-specific fields required by DeepSeek (including `reasoning_content` only as opaque internal protocol data when the configured thinking mode requires it). Never return reasoning content to API clients or logs. Ensure tool-call IDs and tool result roles are paired correctly.

### M5 — Runtime, task lifecycle, events
Add AgentRuntime and in-process TaskManager. Define legal state transitions. A task must reach exactly one terminal state: `completed`, `failed`, `cancelled`, or `limit_reached`. Ensure cleanup on cancellation and exceptions. Publish events in order. Event queues must be bounded; define explicit overflow behavior (prefer backpressure or fail the run with a controlled internal error; never silently grow memory without bound).

### M6 — API and SSE
Add the two additive routes:
- `POST /api/v1/agents/run`
- `POST /api/v1/agents/run/stream`

Use new Agent-specific schemas and event names. Do not reuse/alter Phase 1 event names. Stream event types:
- `agent.started`
- `agent.step.started`
- `agent.model.delta` (only if the provider supports safe token streaming for this runtime path; otherwise omit rather than fabricate)
- `agent.tool.started`
- `agent.tool.completed`
- `agent.tool.failed`
- `agent.completed`
- `agent.failed`
- `agent.cancelled`
- `agent.limit_reached`

Each event payload includes `request_id`, `task_id`, `sequence`, `timestamp`, and event-specific safe data. No API key, authorization header, raw stack trace, private reasoning, or unrestricted tool arguments/results. Apply the repository's established SSE framing and disconnect cleanup patterns.

### M7 — Hardening and docs
Complete tests, update `.env.example` only for genuinely configurable bounded policy defaults, update README, add architecture/API examples, and write a Phase 2 security review. Do not add secrets or machine-specific paths.

## Engineering requirements

- Python 3.11+; preserve compatibility with the project's actual pinned dependencies and Python 3.13.11 environment.
- Async-first. Never block the event loop with synchronous handlers; use a bounded thread offload for sync tools.
- Use FastAPI lifespan for shared client/resource lifecycle, following the existing Phase 1 pattern. Do not create a new model client per request.
- Use Pydantic models for all external request data and tool arguments. Reject unexpected fields where security-sensitive.
- Use explicit enums and typed return values; avoid untyped dictionaries except at SDK boundaries.
- Keep endpoint functions thin. They validate, call application services, and translate results; they do not implement the agent loop.
- Keep provider-specific SDK types inside provider adapters where practical.
- Apply wall-clock deadline and cancellation propagation across model calls and tool calls.
- Do not swallow `asyncio.CancelledError`. Cleanup in `finally`, then re-raise.
- Do not automatically retry tools. A tool may have side effects; retries require an explicit idempotency contract in a future phase.
- Never expose chain-of-thought. User-facing answer, public lifecycle events, logs, and API schemas must exclude `reasoning_content`.
- Log metadata only: request_id, task_id, step, tool name, duration, outcome, model, usage if available. Never log message bodies, secrets, raw tool arguments, or private reasoning.
- Tool output is untrusted data. It must not be interpreted as system/developer instructions.
- Keep dependencies minimal. Any new dependency requires a written reason and user approval before installation.
- Use UTC timestamps in serialized events.
- All limits must be explicit, validated, configurable, and have safe defaults.

## Definition of done

Phase 2 is complete only when all conditions in `references/04_TEST_AND_ACCEPTANCE.md` pass. A passing happy-path demo is insufficient.

## Cursor reporting format

At the end of each milestone report:
1. What was inspected/implemented.
2. Files created/changed.
3. Public contract changes (must be additive only).
4. Focused test command and result.
5. Full test command and result.
6. Known limitations and unresolved risks.
7. Exact next milestone.

Do not claim completion if any acceptance item is unverified.
