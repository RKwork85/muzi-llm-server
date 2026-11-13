# Architecture
Conversation API -> Mode Resolver -> Conversation Orchestrator -> Ask Runtime / Planning Runtime / Agent Runtime.
Planning Runtime uses read-only tools through existing policy-gated registry, writes only its own plan-state records (internal bookkeeping, not external effects), and emits typed UI events.
Interaction Manager validates interaction definitions and submissions; Decision Manager versions decisions; Plan Builder creates immutable approved revisions; Handoff Manager builds execution snapshots.
Frontend uses shared conversation shell and message renderer with typed component registry. Mode selection changes runtime policy, not conversation identity.
Persistence: conversations, plans, plan_revisions, plan_decisions, plan_interactions, interaction_submissions, plan_events, plan_handoffs. Reuse existing tables when possible; do not duplicate identities.
Invariants: no client-supplied user identity; no unauthorized tools; no duplicate submissions; no stale decisions silently overwrite; approval != execution; all transitions audited.
Observability: trace_id, conversation_id, plan_id, revision, interaction_id, run_id; redact secrets.
