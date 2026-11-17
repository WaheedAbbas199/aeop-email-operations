# Integrations

## Gmail
1. Google Cloud Console → enable Gmail API → OAuth consent screen → OAuth client (Web).
2. Redirect URI: `{BASE_URL}/api/v1/oauth/gmail/callback`. Scopes used (minimal): `gmail.readonly`, `gmail.send`.
3. Put `GOOGLE_CLIENT_ID/SECRET` in `.env`, restart, then Integrations → Connect Gmail.
4. Optional push: create a Pub/Sub topic, set `GMAIL_PUBSUB_TOPIC`, push subscription to `{BASE_URL}/api/v1/webhooks/gmail?token=WEBHOOK_SECRET`. Polling (default 120 s) always runs as fallback. Gmail `watch` expires after ~7 days and **renewal is not implemented**.
## Outlook (Microsoft Graph)
1. Entra ID → App registrations → Web redirect `{BASE_URL}/api/v1/oauth/outlook/callback`; delegated permissions `Mail.Read`, `Mail.Send`, `User.Read`, `offline_access`.
2. Set `MS_CLIENT_ID/SECRET/MS_TENANT`. With an https `BASE_URL` a Graph subscription is created (`clientState = WEBHOOK_SECRET`); **subscription renewal is not implemented**; polling uses delta queries.
## Status
Real API code, unit-tested only for surrounding logic; **never run against live Google/Microsoft accounts here**. Expect to debug first live run.
## CRM / ticketing / calendar / accounting
Only `Local*Adapter` classes (app database). To integrate a real provider implement the ABC in `app/integrations.py` (`SupportAdapter`, `CrmAdapter`, `CalendarAdapter`, `AccountingAdapter`) and swap it in `tools.py`. Until then these records are **not** synchronized anywhere.
## LLM
`GEMINI_API_KEY` enables Gemini (`app/llm.py`). To add another provider, implement `generate_json` with the same signature.
