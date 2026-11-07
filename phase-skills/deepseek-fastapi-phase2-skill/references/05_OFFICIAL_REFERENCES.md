# Official Technical References

Reference policy: prefer official vendor/specification documentation. The links below are primary sources. Cursor should re-check the current page before relying on version-specific behavior and record any material change in the implementation notes. These references are guidance, not permission to expand scope.

## DeepSeek API

1. Tool Calls — https://api-docs.deepseek.com/guides/tool_calls/
   - Official tool-call round-trip example.
   - Model returns a proposed tool call; application code implements and executes the function.
   - Documents supported strict-mode constraints and API-format limitations.
   - Phase 2 implication: only the server registry executes tools; do not assume the model executes anything.

2. Chat Completions API — https://api-docs.deepseek.com/api/create-chat-completion/
   - Official request/response schema, tool definitions and streamed chunk shape.
   - Phase 2 implication: inspect actual provider response shape and preserve tool-call IDs and protocol fields.

3. Thinking Mode — https://api-docs.deepseek.com/guides/thinking_mode/
   - Documents thinking-mode tool calling and the need to pass `reasoning_content` back in subsequent requests.
   - Phase 2 implication: preserve required provider protocol data internally when thinking is enabled, but never expose it to clients/logs.

4. API changelog — https://api-docs.deepseek.com/updates/
   - Re-check model IDs and API behavior rather than hard-coding assumptions from old examples.

## FastAPI / Python async

5. FastAPI Lifespan Events — https://fastapi.tiangolo.com/advanced/events/
   - Official shared resource startup/shutdown pattern.
   - Phase 2 implication: follow existing Phase 1 lifespan ownership; do not create one provider client per request.

6. Python asyncio Tasks — https://docs.python.org/3/library/asyncio-task.html
   - Cancellation, task groups, timeout and structured concurrency.
   - Phase 2 implication: use `try/finally`; propagate `CancelledError`; use timeout scopes carefully.

## Pydantic / validation

7. Pydantic Models — https://pydantic.dev/docs/validation/latest/concepts/models/
   - Typed data validation and serialization.
   - Phase 2 implication: validate external requests and tool arguments with typed models.

8. Pydantic Validators — https://pydantic.dev/docs/validation/latest/concepts/validators/
   - Field/model-level constraints.
   - Phase 2 implication: enforce policy ceilings and cross-field invariants.

## SSE

9. WHATWG HTML Living Standard — Server-sent events — https://html.spec.whatwg.org/multipage/server-sent-events.html
   - Normative event-stream framing, UTF-8, fields and blank-line dispatch.
   - Phase 2 implication: use `text/event-stream`, valid framing and flush behavior; keep Phase 1 SSE contract unchanged.

## Testing

10. pytest fixtures — https://docs.pytest.org/en/stable/how-to/fixtures.html
    - Test fixture lifecycle and isolation.
    - Phase 2 implication: mock provider and tool dependencies through fixtures.

11. pytest-asyncio concepts — https://pytest-asyncio.readthedocs.io/en/stable/concepts.html
    - Async test event-loop behavior and modes.
    - Phase 2 implication: configure one consistent asyncio mode compatible with the existing test suite.

## Security

12. OWASP Top 10 for LLM Applications — https://owasp.org/www-project-top-10-for-large-language-model-applications/
    - Prompt injection, insecure output handling, excessive agency, resource abuse and related risks.
    - Phase 2 implication: model instructions are not an authorization boundary. Enforce least privilege and validation in code.

13. OWASP 2025 Top 10 PDF — https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf
    - Current 2025 edition reference.
    - Phase 2 implication: review current risk taxonomy before final security sign-off.

## Versioning note

This skill was assembled on 2026-11-06. Official documentation is living content and may change. Before coding, Cursor must verify the exact provider behavior against the project's pinned SDK version and the current DeepSeek documentation. Do not upgrade dependencies solely because a documentation page shows a newer version.
