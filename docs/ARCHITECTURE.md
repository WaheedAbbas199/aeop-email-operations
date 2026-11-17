# Architecture

```
Gmail / Outlook / Demo mailbox ──► ingest.py (idempotent, attachment safety, injection flag)
        webhook / polling                │
                                         ▼
                                  jobs (DB queue) ──► worker threads (retry, backoff+jitter, dead-letter, crash recovery)
                                         │
                                         ▼
 workflows.orchestrate ─ classify (intelligence.py: LLM→schema-validated | heuristics) ─ state machine
                                         │ LangGraph: orchestrator node ─► exactly one agent node
              ┌───────────┬──────────────┼──────────────┬──────────────┐
          support_agent  sales_agent  finance_agent  meeting_agent   (manual review)
              └───────────┴──── tools.execute_tool ────┴──────────────┘
                 allow-list · permission check · schema validation · tenant scope · idempotency · approval gate · audit
                                         │
                       policy.py (deterministic) ─► Approval (bound to payload SHA-256) ─► human decision
                                         │ approved
                              execute_approval job ─► tool re-check ─► adapters / outbox ─► provider send ─► verify ─► audit
```

Key decisions (trade-offs)
* **LLM never decides authority.** Workflows are deterministic code; the LLM only classifies/drafts and its output is validated by Pydantic. Identifiers come from regex + DB lookups, never from the model.
* **One process, DB-backed queue** instead of Celery/Redis: zero-setup `python main.py`. Trade-off: single node. Handlers are idempotent so swapping in Celery/Redis is mechanical.
* **SQLite default, Postgres supported** through SQLAlchemy 2; Alembic migration `0001` included (verified up/down on SQLite).
* **Audit log in the business transaction** with a per-org hash chain (org row lock serializes appends).
* **Money in integer cents** (no float drift). All datetimes stored UTC; scheduling is DST-safe via zoneinfo.
* **Vanilla JS dashboard** served by FastAPI (no build step). All data rendered with `textContent` (no HTML injection).

Workflow states: `received → classified → queued → processing → awaiting_approval → approved → executing → completed`
plus `awaiting_response`, `retry_scheduled`, `needs_review`, `rejected`, `failed`, `cancelled` (transition table in `workflows.TRANSITIONS`; invalid transitions raise).
