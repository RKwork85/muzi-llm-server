# Planning runtime
Pipeline: load authorized conversation + plan state -> assess ambiguity and risk -> choose answer/ask/revise/finalize -> schema validate -> persist atomically -> emit events.
Decision action enum: respond, ask_user, update_plan, request_approval, ready_for_handoff. The model proposes actions, the orchestrator validates and applies them.
Question policy: ask only if materially affects architecture, cost, safety or implementation; default and disclose reasonable assumptions for low-risk unknowns; avoid repeated questions.
Maintain explicit confirmed/assumed/unresolved/conflicted requirements. Detect contradictory answers and request reconciliation. Never treat inferred assumption as confirmed.
When user changes earlier choice, mark downstream dependent decisions and plan steps stale; recompute impacted sections rather than erasing unrelated decisions.
Planning may query read-only tools; side-effecting tools are denied by policy regardless of model instructions.
Plan readiness requires objective, scope, constraints, steps, dependencies, validation, risks and open questions; approval requires explicit user action.
