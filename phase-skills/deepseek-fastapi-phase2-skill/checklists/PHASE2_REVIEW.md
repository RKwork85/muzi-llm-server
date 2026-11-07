# Phase 2 Code Review Checklist

## Architecture
- [ ] API endpoint is thin.
- [ ] Runtime is independent of FastAPI.
- [ ] AgentLoop reuses the existing provider.
- [ ] Registry is the only tool dispatch authority.
- [ ] Context manager preserves tool-call/result protocol pairs.
- [ ] Task/event storage is bounded and in-process limitations are documented.

## Security
- [ ] No eval/exec/shell/dynamic imports.
- [ ] Model cannot register tools or elevate permissions.
- [ ] Pydantic validation rejects unexpected fields.
- [ ] Tool output is bounded and treated as untrusted data.
- [ ] No chain-of-thought in API/events/logs.
- [ ] No secrets or message bodies in logs.
- [ ] No automatic tool retries.
- [ ] Deadlines and cancellation are enforced.

## Compatibility
- [ ] Phase 1 routes unchanged.
- [ ] Phase 1 schemas/errors/SSE unchanged.
- [ ] Phase 1 built-in tools behave identically.
- [ ] Phase 1 test suite passes unchanged.

## Reliability
- [ ] Exactly one terminal task state.
- [ ] Exactly one terminal event attempted.
- [ ] Event sequence monotonic.
- [ ] Event queue bounded.
- [ ] Cancellation cleanup tested.
- [ ] App shutdown cleanup tested.

## Tests and docs
- [ ] Mocked tests deterministic.
- [ ] Live tests opt-in.
- [ ] Failure matrix covered.
- [ ] README examples match actual OpenAPI.
- [ ] Residual risks documented.
