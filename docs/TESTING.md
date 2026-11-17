# Testing
`python -m pytest tests -q` → **39 passed** (last run in this build). Evaluation: `python -m evaluation.run_eval`.
* `test_e2e.py` – the three workflows + sales through API/worker handlers/DB (demo mailbox, local adapters; **no external provider contacted**).
* `test_security.py` – tenant isolation, RBAC, CSRF, lockout, approval tamper/expiry/rejection/SoD, ambiguous send, retry, injection, attachments, tool allow-list, idempotency, audit tamper, webhooks, OAuth state, settings validation.
* `test_units.py` – state machine, backoff, dead-letter, crash recovery, DST, slots, parsing, invoice validation, policy, classification schema, ingestion idempotency.
Not tested: live Gmail/Outlook/Gemini, Postgres, browsers (a jsdom smoke run of every dashboard page was done manually, not committed), load/performance.
Evaluation set is 24 hand-written synthetic emails: results are a regression signal only.
