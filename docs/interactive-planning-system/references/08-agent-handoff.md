# Plan to Agent handoff
Handoff is explicit, versioned and auditable: conversation_id, approved_plan_id, approved_revision, goal, confirmed decisions, constraints, ordered steps, dependency graph, read-only evidence references, allowed execution scope and provenance.
Approval, switching UI mode, creating handoff, and starting execution are separate actions.
Agent revalidates user identity, current permissions, policy, tool grants, plan revision and side-effect approval before executing; do not inherit stale authorizations.
Create immutable handoff snapshot with digest and idempotency key. Agent run references snapshot and reports per-step progress.
When a plan is revised, old handoffs become superseded or require explicit reconfirmation; running Agent pauses/replans at safe checkpoint, never silently rewrites an active run.
Interrupt/resume: durable run state, safe checkpoints, pending approvals and tool-result reconciliation. Unknown outcome of non-idempotent external action must not be blindly retried.
Plan can be viewed/edited from Agent mode, but edits must not bypass execution policy.
