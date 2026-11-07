# Test Plan and Acceptance Criteria

## 1. Baseline preservation

Before changes, record Phase 1 test results. After every milestone, run:
```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
All Phase 1 tests must remain green. Do not change tests merely to hide regressions.

## 2. Unit tests

### Policy
- defaults are safe;
- zero/negative limits rejected;
- request cannot exceed server ceiling;
- unknown tools cannot be added through request;
- hard upper bounds enforced.

### Registry
- unique tool names;
- unknown lookup rejected;
- disabled tool rejected;
- generated schemas stable and valid;
- registry cannot be modified by model output.

### Executor
- valid arguments invoke handler once;
- invalid JSON rejected;
- non-object JSON rejected;
- unknown argument rejected;
- invalid Pydantic data rejected;
- unknown tool rejected;
- run allowlist enforced;
- tool timeout enforced;
- sync function does not block event loop;
- async handler awaited;
- handler exception sanitized;
- oversized result remains valid JSON;
- no automatic retries.

### Context
- system prompt remains first;
- newest user message remains;
- max message/character budget enforced;
- tool-call/result pairs remain intact;
- no orphan tool messages;
- oversized mandatory context fails safely;
- reasoning data never appears in public serialization.

### Agent loop
- final response completes without tools;
- one tool call then final answer;
- multiple tool calls preserve IDs and order;
- unknown tool rejected;
- invalid tool args rejected;
- malformed model response handled;
- step limit;
- total tool-call limit;
- per-step tool-call limit;
- deadline;
- provider timeout/error;
- cancellation;
- DeepSeek thinking-mode protocol field preserved internally where required.

### Runtime/task manager/events
- legal transitions accepted;
- illegal transitions rejected;
- terminal state immutable;
- exactly one terminal state;
- event sequence monotonically increasing;
- event queue bounded;
- event payload sanitized;
- task record capacity/TTL bounded;
- app shutdown cancels owned active work.

## 3. API tests

- new `/api/v1/agents/run` happy path;
- new `/api/v1/agents/run/stream` happy path;
- request validation;
- unknown prompt;
- unknown/disallowed tool;
- policy escalation rejected;
- unified error shape;
- request_id propagation;
- correct content type;
- valid SSE framing;
- terminal event emitted once;
- disconnect cleanup;
- Phase 1 endpoints unchanged.

## 4. Integration tests

Default suite must mock the provider and be deterministic. Live DeepSeek integration tests must be opt-in using an environment flag, consistent with Phase 1's `DEEPSEEK_LIVE=1` pattern. Never require a real API key for normal tests.

Live test should cover:
- a simple answer without tools;
- one safe calculator tool call;
- bounded multi-step interaction if model behavior is stable enough;
- stream only if actual provider streaming implementation is part of the supported runtime path.

## 5. Security regression tests

- prompt injection text cannot add a tool;
- model asks for unregistered shell/file/network tool -> denied;
- tool result containing fake system instructions cannot alter policy;
- secret-like strings do not appear in logs;
- reasoning content not returned;
- oversized arguments/results bounded;
- cancellation does not produce a false success;
- tool errors do not leak stack traces.

## 6. Documentation acceptance

README must explain:
- what Phase 2 adds and does not add;
- module diagram;
- API request/response examples;
- stream event contract;
- policy limits and precedence;
- task state semantics and in-memory limitation;
- security boundaries;
- how to run tests;
- how to run opt-in live tests.

## 7. Final acceptance checklist

- [ ] Baseline Phase 1 tests recorded.
- [ ] All Phase 1 tests still pass.
- [ ] All Phase 2 unit tests pass.
- [ ] All Phase 2 API/SSE tests pass.
- [ ] Security regression tests pass.
- [ ] Live tests are opt-in and secrets are environment-only.
- [ ] No Phase 1 route/schema/error/SSE contract changed.
- [ ] No unapproved dependency added.
- [ ] No persistence/distributed queue accidentally introduced.
- [ ] No chain-of-thought exposed.
- [ ] All execution/resource limits enforced in code.
- [ ] README and architecture docs updated.
- [ ] `SECURITY_REVIEW.md` documents residual risks.
- [ ] Final report includes exact test counts and known limitations.

Only after every item is verified may Cursor mark Phase 2 complete.
