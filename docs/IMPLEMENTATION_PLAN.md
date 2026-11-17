# Implementation plan (phases from the master prompt)
0 Foundation: config, DB, models, Alembic, Docker, CI, logging ✔ · 1 Ingestion/classification ✔ (live providers untested) · 2 Drafting/approval/outbox/audit ✔ ·
3 Orchestrator, agents, tools, support/CRM/escalation ✔ (local adapters) · 4 Finance/calendar ✔ (local adapters) · 5 Reliability/security ✔ (see gaps) · 6 Deployment readiness: partial (Dockerfile/compose/CI/runbooks; Postgres + TLS unverified).
Next: real CRM/ticketing/calendar/accounting adapters, OCR, webhook subscription renewal, Redis rate limiting + Celery, React frontend if required, Prometheus/OTel, larger evaluation set.
