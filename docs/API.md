# API
Interactive OpenAPI at `/api/docs`. Prefix `/api/v1`. Cookie session + `X-CSRF-Token` header for unsafe methods (or `Authorization: Bearer <session JWT>`).
Main groups: auth, analytics, emails, runs, approvals, agents, tools, integrations/oauth/webhooks, jobs, audit, users, settings, records, notifications, evaluations, demo.
Errors: JSON `{"detail": "..."}` with 401/403/404/409/422/423/429 as appropriate; no stack traces are returned.
