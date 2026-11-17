# Autonomous Email Operations Platform (AEOP)

Multi-agent email operations: **agents propose → deterministic policy decides → humans approve → everything is audited.**
Backend (FastAPI + LangGraph + background worker) and the neon dashboard run from **one command**.

## Quick start (Windows / macOS / Linux)

```bash
cd aeop
python main.py          # installs requirements on first run, then starts API + worker + dashboard
```
Open **http://127.0.0.1:8000**.
(Roman Urdu: `python main.py` chalayen, browser mein 127.0.0.1:8000 kholen. Pehli dafa dependencies khud install hongi.)

* First run creates an administrator. Credentials are written to `data/first_run_credentials.txt` (not printed). Or set `ADMIN_EMAIL`/`ADMIN_PASSWORD` in `.env`.
* You can also **Sign up** on the landing page: that creates a brand-new, isolated organization (disable with `ALLOW_SIGNUP=false`).
* In the dashboard click **Load demo data (synthetic)** on the Overview: 12 sample emails run through the real pipeline (complaint, support, 5 invoice cases, meeting, sales lead, prompt-injection, newsletter, ambiguous). Then open **Approval center**.
* No API key needed. Without `GEMINI_API_KEY` the system uses deterministic heuristics (shown as engine `local-heuristic`).

API docs: `/api/docs` · Health: `/api/v1/health`, `/api/v1/ready`

## What is real, what is not (read this)

| Area | Status |
|---|---|
| Workflows (complaint, invoice, meeting, sales), orchestrator (LangGraph), 7 agents/policy layer, 26 tools, approvals, outbox, audit chain, RBAC, tenant isolation, retries/dead-letter, SLA escalation | Implemented and covered by 39 automated tests (SQLite, demo mailbox) |
| Gmail + Outlook connectors (OAuth, sync, send, webhooks) | Real provider API code. **Not exercised against live accounts** (needs your OAuth credentials) |
| CRM, ticketing, calendar, accounting | **Local test adapters only** (backed by this app's DB). Adapter interfaces exist; no real SaaS adapter is included |
| LLM (Gemini) | Code path implemented with schema validation; **not exercised live** (no key in this build) |
| OCR for scanned/image invoices, malware scanning | Not implemented (such files are routed to manual review / not scanned) |
| Postgres / Docker | Provided; **not exercised** in this build |
| Production readiness | **Production candidate**, not production-ready. See `docs/IMPLEMENTATION_STATUS.md` |

See `docs/` for architecture, security, threat model, integrations, operations, testing.

## Common commands
`make test` · `make eval` · `make migrate` · `python main.py`
