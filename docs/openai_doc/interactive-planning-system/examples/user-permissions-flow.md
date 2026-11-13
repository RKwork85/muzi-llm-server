# Example: user permissions
User: Design user-level permissions.
Planner: Ask whether the app is single-org or multi-tenant; display single_select card.
User: Select single-org and submit.
Backend: validate pending interaction, expected revision, idempotency, persist decision and emit submitted + plan.updated.
Planner: Ask RBAC vs ABAC vs hybrid only if meaningful; permit free-form reply.
User: Choose RBAC; later edit to hybrid.
Backend: create revision, mark affected plan steps stale and update plan.
Planner: Present compact plan review and assumptions.
User: Approve plan, remain in Plan mode.
Backend: mark approved; do not start tools.
User: Switch to Agent and explicitly request execution.
Backend: create immutable handoff, revalidate permissions and start Agent with progress events.
User: Interrupt and resume; backend resumes durable run without replaying non-idempotent side effects.
