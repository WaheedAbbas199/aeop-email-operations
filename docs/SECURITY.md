# Security

Implemented
* AuthN: PBKDF2-SHA256 (210k iters), JWT session in HttpOnly SameSite=Lax cookie, **double-submit CSRF** (header must equal cookie and JWT claim), login rate-limit + 5-failure lockout (15 min), password policy.
* AuthZ: 7 roles → permissions (`security.py`), enforced **server-side on every endpoint**; tenant (org) derived from the validated session, never from client input; every query filtered by `org_id`.
* Tools: strict allow-list per agent, permission intersection (agent ∩ triggering user), Pydantic arg validation, idempotency keys, approval gates, budgets (steps/tools/model calls/time).
* Approvals: payload SHA-256 binding (tamper ⇒ cancelled), expiry, separation of duties for high-risk actions, approver permission check, rejected/expired cannot execute. Payments and vendor bank changes are blocked by policy.
* Prompt injection: email/attachment text is untrusted data, delimited in prompts, injection patterns flag the email → automation disabled → manual review; the LLM cannot call tools; replies may only go to the original sender; grounding check on drafts.
* Attachments: extension allow-list, magic-byte check, size cap, PDF active-content block, sanitized filenames, never executed; HTML → text (never rendered).
* Secrets: OAuth tokens Fernet-encrypted at rest (key derived from SECRET_KEY), never returned by the API, redacted from audit/logs.
* Headers: CSP (script-src 'self'), X-Frame-Options, nosniff, HSTS when COOKIE_SECURE.
* Audit: hash-chained, verify endpoint, no update/delete API.

Known gaps (do not ignore)
* Malware scanning and OCR not implemented. Webhook auth is shared-secret/clientState (Google OIDC JWT verification not implemented).
* Rate limiting is in-memory per process. SSRF: the app performs no user-supplied URL fetching.
* Audit immutability is application-level; in Postgres also `REVOKE UPDATE, DELETE ON audit_logs` from the app role.
* Not penetration-tested. Use `pip-audit` (CI step) for dependency vulnerabilities.
