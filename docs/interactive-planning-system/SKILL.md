---
name: interactive-planning-system
description: Implement an interactive planning system with structured UI, persistent decisions, streaming and safe Plan-to-Agent handoff in an existing LLM application.
---
# Interactive Planning System — Implementation Skill

## Trigger
Use when asked to audit, design, implement, test or extend interactive Plan mode, structured interaction messages, or Plan-to-Agent handoff.

## Non-negotiable constraints
- Audit the real repository before changing code; never invent file paths, endpoints, tables or test results.
- Keep existing Agent Runtime, Tool Registry, Tool Executor, ExecutionPolicy, MCP and Skill orchestration compatible.
- Plan is read-only at server policy level, not merely in prompts. No write-capable tools or side effects during Plan.
- A model-generated UI description is untrusted data; validate against allowlisted versioned schemas, sanitize displayed text, cap lengths/options/depth.
- The backend owns state, authorization, lifecycle, idempotency, event sequencing and mode transitions.
- Never auto-execute merely because a plan is approved. Plan approval, mode change, execution authorization are distinct operations.
- Ask only decisions that materially affect the plan; one primary decision per turn by default. Allow free-form corrections and skips.
- Maintain stable conversation_id and durable plan_id; never rely solely on chat history to recover decisions.
- Prefer additive migrations and backward compatibility; avoid wholesale runtime refactors.
- Every stage must include tests, explicit pass/fail evidence and stop on unresolved P0/P1 risks.

## Reading order
1. references/01-product-experience.md
2. references/02-system-architecture.md
3. references/03-planning-runtime.md
4. references/04-interaction-protocol.md
5. references/05-frontend-design.md
6. references/06-state-management.md
7. references/07-streaming-events.md
8. references/08-agent-handoff.md
9. references/09-security-policy.md
Then read relevant schemas, examples and phase file.

## Execution protocol
- First run M0 and publish actual findings + compatibility matrix.
- Implement M1–M8 sequentially only after previous acceptance gates pass.
- Before each stage: state impacted modules, migrations, rollback, tests and risks.
- After each stage: summarize changed files, commands run, passing/failing tests, known gaps and next gate.
- Do not commit, push, tag, deploy or claim production-ready without explicit authorization and real evidence.
- If repository structure differs, adapt implementation to real code while preserving contracts and document differences.

## Expected deliverables
Versioned domain contracts, interaction protocol and JSON schemas, persistent plan/decision/interaction state, backend orchestrator, frontend component registry, SSE events, resumable handoff, permissions, tests and docs.
