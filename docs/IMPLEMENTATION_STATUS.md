# Implementation status (honest)
Classification: **Production Candidate** (not Production Ready).

| Feature | Status | Notes |
|---|---|---|
| Gmail connector | implemented, **untested live** | OAuth, history/list sync, send with Message-ID verification, watch (no renewal) |
| Outlook connector | implemented, **untested live** | OAuth, delta sync, draft→send with verification, subscription (no renewal) |
| Demo mailbox | tested | send is SIMULATED |
| Classification/extraction | tested (heuristics); Gemini path untested | schema-validated, thresholds configurable |
| Orchestrator + state machine + LangGraph | tested | |
| Support/complaint workflow | tested E2E | local adapter |
| Invoice workflow | tested E2E | text/PDF-with-text; **no OCR**; drafts only |
| Meeting workflow | tested E2E | reply-to-confirm by email parsing is basic ("Option N") |
| Sales/CRM | tested | local adapter |
| Escalation (SLA) | unit-level via scheduler code; no dedicated test | |
| Policy/approvals/SoD/tamper | tested | |
| Outbox + ambiguity handling | tested | |
| Retry/backoff/dead-letter/recovery | tested | |
| RBAC/tenant isolation/CSRF | tested | |
| Audit hash chain | tested | |
| Dashboard (all required pages) | implemented; jsdom smoke only | vanilla JS, not React/Next.js |
| Alembic | up/down verified on SQLite | Postgres unverified |

Deviations from the master prompt (deliberate, for zero-setup single-command run): SQLite default; DB-backed queue + threads instead of Celery/Redis; vanilla JS instead of React/Next.js; roles/permissions defined in code (not tables); threads/recipients/versions stored as JSON/columns rather than separate tables; no Prometheus/OpenTelemetry; tool timeouts declared but not enforced for in-process local tools.
**Definition-of-done items NOT met:** real CRM/ticketing/calendar/accounting adapters; live-verified Gmail/Outlook; OCR; Postgres verification; formal load/security testing.
