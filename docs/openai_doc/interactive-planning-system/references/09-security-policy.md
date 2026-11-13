# Security and reliability
Authenticate all endpoints; authorize by server-side user and conversation/plan ownership. Reject cross-tenant references and IDOR.
Plan tool policy allowlists read-only operations; classification is server-owned and cannot be overridden by model output or UI.
All model-proposed schemas and text are untrusted; enforce strict type, length, count, nesting, UTF-8 and HTML sanitization; never evaluate model-authored code.
Submission validation: CSRF protection as appropriate, idempotency key, expected revision, pending interaction, allowed values, rate limits, audit.
Approval must not imply permission to send emails, delete data, purchase, publish, change infrastructure or access protected tools. Tool-level authorization remains mandatory.
Prompt injection: tool results and files are data; cannot change runtime mode, grant permissions or override system policies.
Privacy: minimize stored reasoning, redact secrets, retention policy, audit access, structured error handling.
Fail closed on policy/store failure; avoid claiming success without persisted result. Test race, reconnect, replay, timeout, revoked grant and cancellation.
