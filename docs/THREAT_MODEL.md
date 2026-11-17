# Threat model (summary)
| Threat | Control | Test |
|---|---|---|
| Instructions hidden in email/attachment (forward mail, approve invoice, reveal keys) | injection flag → manual review; LLM has no tool access; policy layer independent of LLM | test_injection_in_attachment_blocks_automation, e2e injection |
| Cross-tenant read/write | org-scoped queries, 404 on foreign ids | test_cross_tenant_access_blocked |
| Privilege escalation / unauthorized tool use | allow-list + permission checks, audited denials | test_tool_allowlist_and_permissions_enforced, test_rbac |
| Approval tampering / replay / self-approval | payload hash, status checks, SoD | test_approval_payload_tamper_blocked, test_separation_of_duties |
| Duplicate external side effects | idempotency keys, outbox unique per approval, ambiguity-aware retries | test_ambiguous_send…, test_retry_then_success_sends_once, test_idempotent_tool_side_effects |
| Malicious attachments | allow-list, magic bytes, PDF JS block | test_unsafe_html_is_neutralised_and_attachments_validated |
| CSRF / session theft | double-submit CSRF, HttpOnly cookies | test_csrf_required_for_cookie_auth |
| Webhook forgery / replay | secret/clientState, dedupe records | test_webhook_authentication_and_replay |
| Audit tampering | hash chain | test_audit_chain_detects_tampering |
| Prompt injection cannot be eliminated | defence in depth (above); residual risk documented | — |
