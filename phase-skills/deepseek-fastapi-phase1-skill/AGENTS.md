# Repository Instructions — DeepSeek Phase 1

- Read `.cursor/skills/deepseek-fastapi-phase1/SKILL.md` before implementing DeepSeek Phase 1.
- Follow `specs/PHASE1_SCOPE.md`, `specs/API_CONTRACTS.md`, and `specs/ACCEPTANCE_CRITERIA.md`.
- Inspect and preserve the existing repository architecture; make minimal, scoped changes.
- Keep provider, service, API, schemas, prompts, and tool execution responsibilities separate.
- Use async HTTP/SDK operations and application lifespan for shared clients.
- Never hardcode secrets or current model IDs in business logic.
- Never execute model-generated code with `eval` or `exec`.
- Only execute server-registered tools after schema validation.
- Do not add Harness, MCP, RAG, vector DB, Redis/RQ, or Multi-Agent in Phase 1.
- Mock external API calls in tests; never claim live API verification without a real successful request.
- Run tests and report exact results. Do not fabricate test output.
- Do not overwrite unrelated files or remove existing comments/features.
