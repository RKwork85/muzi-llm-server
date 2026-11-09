# Persistence and state machine
Plan states: understanding -> clarifying -> drafting -> awaiting_approval -> approved -> ready_for_handoff -> executing -> completed; also paused, cancelled, failed, superseded.
Use transition table validated server-side. No direct jump from clarifying to executing.
Persist plan revision, decisions (source: user/assumed/system; confidence and dependencies), pending interaction, timestamps and audit events.
Atomic transaction: verify expected revision, validate response, insert submission keyed by client_submission_id, update decisions, increment revision, append event/outbox.
Concurrency: compare-and-swap revision, return 409 with latest state on conflict. Replayed submission returns original result. Persist outbox before streaming when possible.
Resume: GET snapshot + events after last_event_id. Expired cards cannot be submitted. Cancellation and interruption preserve last durable state.
Revisions immutable once approved; editing creates new draft revision and invalidates old handoff approval.
