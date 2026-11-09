# Interactive Planning implementation status

Updated 2026-11-09. This report tracks the authorized M1-M8 sequence. It does
not claim deployment or production readiness.

## M1-M3: contracts, planning runtime, and UI

- Versioned strict contracts and JSON Schemas are in `app/planning/contracts.py`
  and `schemas/`.
- The planning decision engine asks the configured provider for typed JSON. It
  cannot execute model-authored tool calls. Plan tools are validated against
  the server allowlist.
- Chat renders the selection, text, confirmation, and plan review components.
  Its Plan mode submits through the planning API. Local chat records preserve
  the server conversation and plan identifiers.
- Plan approval, switching to Agent, and execution authorization are separate
  API requests and separate UI actions.

## M4: persistence

**Pass.** The additive `f38a1d2e9c70` migration creates plan revisions,
decisions, interactions, idempotent submissions, ordered events, and an
outbox. Planning APIs verify bearer-token ownership. Plan and conversation
events are stored in PostgreSQL; ordinary Chat messages use the existing
`sessions` and `messages` tables. Handoff Agent tasks use the existing
`agent_tasks`, `executions`, and `events` tables.

The migration was applied to the local development database and
`alembic check` reports no metadata drift. The reverse migration was rendered
as offline SQL; it was not applied because it drops the new planning tables.
The database integration suite covers restart reads, idempotent replay,
concurrent revision conflicts, and Agent event/result persistence: **3 passed**.

## M5: streaming and cancellation

**Implemented; end-to-end gate partial.** Chat retains its existing POST SSE
event names and chunk behavior. Authenticated requests can persist their
conversation messages and batched `chat.*` events; event APIs replay by
conversation sequence. Chat has an owner-scoped cancellation API and UI stop
button. Plain and tool-loop provider streams enforce configured timeouts.
Agent run events can be replayed from database records after the runtime no
longer has an in-process publisher.

The frontend production build and lint pass. Targeted SSE and cancellation
checks pass. The phase checklist's full disconnect/reconnect, slow-client, and
out-of-order browser scenarios have not all been exercised.

## M6: handoff

**Implemented; security gate partial.** The review UI asks for plan approval,
then explicit Agent mode switch, then explicit execution authorization. The
server validates the approved plan revision and current Plan state before
starting AgentRuntime. Handoff stores a plan snapshot in the task input and
persists task, execution, event, result, and terminal Plan state. Handoff runs
start with an empty explicit grants list and still pass through AgentRuntime's
server policy.

The PostgreSQL integration test verifies `executing -> completed` from a
persisted Agent terminal event. Revoked-grant revalidation, interrupted-run
resume, and safe retry need broader adversarial coverage in M7.

## Verification snapshot

- Full backend suite (`pytest -q --import-mode=importlib`): **366 passed,
  13 skipped, 1 failed**. The remaining failure is the pre-existing
  `tests/test_remote_mcp.py::test_sse_transport_calls_the_selected_tool`;
  its fake OAuth response lacks the `Location` header expected by the test.
- Planning/Agent/SSE focused suite: **189 passed, 4 skipped**.
- PostgreSQL planning integration suite: **3 passed**.
- Frontend `npm run build` and `npm run lint`: **passed**. Vite reports existing
  large chunks over 500 kB.
- `alembic check`: **passed**. Offline downgrade SQL generation: **passed**.
- Browser visual QA could not run: the computer-use browser host was
  unavailable in this environment.

## Remaining gates

M7 security review and M8 end-to-end acceptance remain open. The regular
non-persisted `/api/v1/agents/runs` path remains process-local unless its caller
supplies an owned `conversation_id`; the planning handoff path is persisted.
Persisted Agent run reads, cancellation, and event replay verify the owning
bearer principal. The repository has no general API rate limiter, and the full
security review must assess legacy non-persisted Agent routes and MCP grant
revalidation.
No repository commit, push, deployment, or production-readiness claim was made.
