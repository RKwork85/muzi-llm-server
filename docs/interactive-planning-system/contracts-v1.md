# Interactive Planning contracts v1

This document records the additive contract baseline introduced in M1. The
Python source of truth is `app/planning/contracts.py`; the JSON Schemas under
`schemas/` are the frontend and external-client validation artifacts.

## Compatibility

- Existing Chat and Agent routes and payloads remain unchanged in M1.
- Planning wire objects declare `protocol_version: "1.0"`; unknown fields,
  interaction types, event types, and protocol versions are rejected.
- JSON Schemas are additive and do not replace FastAPI's existing Chat/Agent
  request or response models.
- Unknown future interaction types must be rendered as safe plain text by the
  client. A client must never execute model-authored markup or code.

## Separate user actions

Approval, UI mode selection, and execution authorization have distinct
request contracts: `PlanApprovalRequest`, `ModeSwitchRequest`, and
`ExecutionAuthorizationRequest`. A plan approval or mode switch alone cannot
authorize the creation of a run. A handoff records
`authorization: "revalidate_at_execution"`; the runtime must recheck the
current principal, grants and execution policy before starting tools.

## Plan state transition table

| Current | Allowed next states |
|---|---|
| understanding | clarifying, drafting, paused, cancelled, failed |
| clarifying | drafting, paused, cancelled, failed, superseded |
| drafting | clarifying, awaiting_approval, paused, cancelled, failed, superseded |
| awaiting_approval | drafting, approved, paused, cancelled, superseded |
| approved | drafting, ready_for_handoff, paused, cancelled, superseded |
| ready_for_handoff | drafting, executing, paused, cancelled, superseded |
| executing | completed, paused, cancelled, failed |
| paused | understanding, clarifying, drafting, awaiting_approval, approved, ready_for_handoff, executing, cancelled, failed, superseded |
| completed, cancelled, failed, superseded | terminal; no transitions |

The transition table is exposed as `PLAN_TRANSITIONS` and checked through
`can_transition_plan`. In particular, clarification cannot jump directly to
execution. Persistence and authorization checks will be added in later stages;
these contracts alone do not provide those guarantees.

## Registered API routes

The authenticated conversation and planning routes are:

- `POST /api/v1/conversations`, `GET /api/v1/conversations/{conversation_id}`,
  `POST /api/v1/conversations/{conversation_id}/mode`, and
  `GET /api/v1/conversations/{conversation_id}/events?after=`
- `POST /api/v1/plans`, `GET /api/v1/plans/{plan_id}`, and
  `POST /api/v1/plans/{plan_id}/messages`
- `POST /api/v1/plans/{plan_id}/interactions/{interaction_id}/submissions` and
  `/skip`
- `POST /api/v1/plans/{plan_id}/approve`, `/switch-to-agent`, and
  `/authorize-execution`
- `GET /api/v1/plans/{plan_id}/events?after=`

Chat keeps `POST /api/v1/chat/completions/stream` and its existing SSE event
names. Authenticated clients may add `conversation_id` and `client_run_id` to
persist messages and events. `POST /api/v1/chat/runs/{client_run_id}/cancel`
cancels an active persisted Chat run.

An authorized handoff starts `POST /api/v1/agents/runs` internally with an
immutable approved plan snapshot. Standalone Agent API callers can opt into
database persistence by supplying `conversation_id` with a valid bearer token.
Run state and replay are exposed at `/api/v1/agents/runs/{task_id}` and
`/api/v1/agents/runs/{task_id}/events`; cancellation remains
`POST /api/v1/agents/runs/{task_id}/cancel`.

Chat's public SSE framing remains compatible while `plan_events` stores stable
`chat.*`, `plan.*`, `interaction.*`, and `run.*` event types with conversation
sequence numbers. M7/M8 still own the broader security review and end-to-end
acceptance gates; the route map alone is not a production-readiness claim.
