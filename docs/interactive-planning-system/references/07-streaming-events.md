# SSE event contract
Use stable id and monotonically increasing per-conversation sequence; event fields: event_id, sequence, conversation_id, plan_id, run_id, type, payload, protocol_version, occurred_at.
Types: message.started, message.delta, message.completed, interaction.required, interaction.submitted, interaction.rejected, plan.updated, plan.approval_requested, plan.approved, handoff.ready, execution.started, execution.progress, execution.completed, run.failed, run.cancelled.
Ordering: persist events and state before emission; interaction.required only after validated complete schema; plan.approved only after durable approval; execution.started only after authorization.
Reconnect via Last-Event-ID or since cursor; dedupe by event_id, replay without double-submitting. Heartbeats are transport events, not business actions.
Define backpressure, heartbeat, max payload size, stream cancellation and graceful fallback to snapshot polling. Never treat a dropped SSE connection as automatic task cancellation.
