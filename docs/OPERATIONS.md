# Operations
* Start: `python main.py` (dev) · production: set `AEOP_ENV=production`, `SECRET_KEY`, `DATABASE_URL` (Postgres), `ALLOW_SIGNUP=false`, https `BASE_URL`; run `alembic upgrade head`; run behind a TLS reverse proxy; **run exactly one process** (in-memory rate limiter, single worker pool).
* Health: `/api/v1/health` (liveness), `/api/v1/ready` (DB + worker). Logs: one JSON line per request (`rid`, path, status, ms); request id in `X-Request-ID`.
* Failed work: dashboard → *Failed jobs* (dead-letter). Retry only for retryable/permanent-fixed jobs; **ambiguous** outcomes (email send unverified) must be checked manually in the provider's Sent folder.
* Crash recovery: on start `running` jobs return to the queue; handlers are idempotent.
* SLA/escalation: scheduler tick every 20 s (`escalation_agent`), notifications de-duplicated per level.
* Backup (SQLite): stop app or use `sqlite3 data/aeop.db ".backup backup.db"`; also back up `data/attachments/` and `SECRET_KEY` (without it stored OAuth tokens are unreadable). Postgres: `pg_dump`. Restore: replace files / `pg_restore`, then `alembic upgrade head`.
* Integrity: Audit → *Verify integrity*.
* Not provided: Prometheus/OpenTelemetry metrics, alerting integrations, HA.
