# Structured interaction protocol

## Component names (frozen)
Authoritative types (snake_case): single_select, multi_select, text_input, confirmation, plan_review, progress, error.
Registered, rendered in M3/M4: assumption_editor, plan_editor, step_reorder, plan_diff.
Naming note: legacy doc names single_choice/multi_choice are equivalent to single_select/multi_select; the latter are authoritative.

Envelope: protocol_version, interaction_id, plan_id, plan_revision, type, prompt, description, options, constraints, status, expires_at.
Options: stable id, label, description, optional recommended flag. Never render model-authored HTML/JS or arbitrary executable UI.

## Unified action protocol (InteractionAction)
Every user mutation of a plan goes through a single action contract, validated then dispatched:
- plan_id, plan_revision (CAS revision check)
- component_type, action_type, target_type, target_id, payload
- interaction_id (card locator; required when target_type=interaction)
- idempotency_key (replay-safe dedup key)
Validation chain: ownership -> idempotency -> revision CAS -> action whitelist -> target resolution -> dispatch.
Action whitelist: ActionType (18 kinds); action/target compatibility enforced by ACTION_TARGETS.

## Submission (legacy, kept for compatibility)
Legacy submission is equivalent to (action_type=submit, target_type=interaction): interaction_id, expected_plan_revision, client_submission_id, value.
Server validates auth, ownership, pending state, schema and revision before commit.

## Status
pending, submitted, processing, completed, failed, skipped, expired, superseded, cancelled.
processing/completed/failed serve LLM-reasoning actions (assumption edit, step split) landing in M3/M4.
Once submitted, disable duplicate UI; editing creates a new revision and invalidates dependent artifacts.

Card policy: one primary pending question by default; additional task cards only when explicitly requested/expanded.
Version compatibility: unknown type -> accessible plain text fallback, never silently execute.
