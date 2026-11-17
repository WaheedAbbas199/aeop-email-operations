"""Email provider adapters. Gmail (Gmail API) and Outlook (Microsoft Graph) talk to the REAL provider APIs over OAuth.
NOTE: they need your own OAuth client credentials (see docs/INTEGRATIONS.md) and have NOT been exercised against live
accounts in this build. The Demo provider is a local, clearly-labelled mailbox that never contacts any provider."""
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Optional
from urllib.parse import urlencode

import httpx

from .config import settings
from .errors import AmbiguousOutcome, PermanentError, RetryableError
from .models import utcnow
from .security import decrypt, encrypt


@dataclass
class RawAttachment:
    filename: str
    mime: str
    data: bytes


@dataclass
class RawEmail:
    provider_msg_id: str
    thread_id: str
    sender: str
    sender_name: str
    recipients: list
    subject: str
    body_text: str
    received_at: datetime
    labels: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    body_is_html: bool = False


def _http(method, url, *, ambiguous_on_timeout=False, **kw):
    kw.setdefault("timeout", 30)
    try:
        r = httpx.request(method, url, **kw)
    except httpx.TimeoutException as e:
        if ambiguous_on_timeout:
            raise AmbiguousOutcome(f"Timeout after sending request: {e}")
        raise RetryableError(f"Provider timeout: {e}")
    except httpx.HTTPError as e:
        if ambiguous_on_timeout:
            raise AmbiguousOutcome(f"Network error after sending request: {e}")
        raise RetryableError(f"Provider network error: {e}")
    if r.status_code == 429:
        raise RetryableError("Provider rate limit", retry_after=float(r.headers.get("Retry-After", 30) or 30))
    if r.status_code >= 500:
        if ambiguous_on_timeout:
            raise AmbiguousOutcome(f"Provider 5xx ({r.status_code}) during side-effecting call")
        raise RetryableError(f"Provider error {r.status_code}")
    if r.status_code >= 400:
        raise PermanentError(f"Provider rejected request ({r.status_code}): {r.text[:200]}")
    return r


class EmailProvider:
    name = ""
    token_url = ""
    scopes: list = []

    def configured(self) -> bool: return False
    def auth_url(self, state: str) -> str: raise NotImplementedError
    def exchange_code(self, code: str) -> dict: raise NotImplementedError
    def sync(self, db, account) -> list: raise NotImplementedError
    def send(self, db, account, payload: dict, idem_key: str, state: dict): raise NotImplementedError
    def verify_sent(self, db, account, idem_key: str, state: dict) -> Optional[str]: return None
    def revoke(self, account): pass
    def redirect_uri(self): return f"{settings.BASE_URL}/api/v1/oauth/{self.name}/callback"

    def _client(self): raise NotImplementedError

    def access_token(self, account) -> str:
        tok = json.loads(decrypt(account.token_enc))
        if tok.get("expires_at", 0) > time.time() + 60:
            return tok["access_token"]
        cid, secret = self._client()
        try:
            r = _http("POST", self.token_url, data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                                    "client_id": cid, "client_secret": secret})
        except PermanentError as e:
            account.status, account.last_error = "reauth_required", "Token refresh rejected; reconnect the mailbox"
            raise PermanentError("Mailbox needs re-authorization") from e
        data = r.json()
        tok.update(access_token=data["access_token"], expires_at=time.time() + int(data.get("expires_in", 3600)))
        if data.get("refresh_token"):
            tok["refresh_token"] = data["refresh_token"]
        account.token_enc = encrypt(json.dumps(tok))
        return tok["access_token"]

    def _tokens_from(self, data: dict) -> dict:
        return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token", ""),
                "expires_at": time.time() + int(data.get("expires_in", 3600))}


# ------------------------------------------------------------------ Gmail
class GmailProvider(EmailProvider):
    name = "gmail"
    token_url = "https://oauth2.googleapis.com/token"
    scopes = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]
    API = "https://gmail.googleapis.com/gmail/v1/users/me"

    def _client(self): return settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET
    def configured(self): return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)

    def auth_url(self, state):
        q = {"client_id": settings.GOOGLE_CLIENT_ID, "redirect_uri": self.redirect_uri(), "response_type": "code",
             "scope": " ".join(self.scopes), "access_type": "offline", "prompt": "consent", "state": state}
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(q)

    def exchange_code(self, code):
        r = _http("POST", self.token_url, data={"code": code, "client_id": settings.GOOGLE_CLIENT_ID,
                                                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                                                "redirect_uri": self.redirect_uri(), "grant_type": "authorization_code"})
        tok = self._tokens_from(r.json())
        prof = _http("GET", f"{self.API}/profile", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
        return {"tokens": tok, "address": prof["emailAddress"], "cursor": str(prof.get("historyId", "")), "scopes": self.scopes}

    def _get(self, account, path, **params):
        return _http("GET", f"{self.API}/{path}", headers={"Authorization": f"Bearer {self.access_token(account)}"}, params=params).json()

    def sync(self, db, account):
        prof = self._get(account, "profile")
        ids = []
        if account.cursor:
            try:
                page = None
                while True:
                    h = self._get(account, "history", startHistoryId=account.cursor, historyTypes="messageAdded", labelId="INBOX",
                                  **({"pageToken": page} if page else {}))
                    for rec in h.get("history", []):
                        ids += [m["message"]["id"] for m in rec.get("messagesAdded", [])]
                    page = h.get("nextPageToken")
                    if not page:
                        break
            except PermanentError:
                account.cursor = None  # history expired -> full resync (dedupe protects us)
        if not account.cursor:
            page, n = None, 0
            while n < 3:
                r = self._get(account, "messages", labelIds="INBOX", maxResults=25, q="newer_than:7d", **({"pageToken": page} if page else {}))
                ids += [m["id"] for m in r.get("messages", [])]
                page, n = r.get("nextPageToken"), n + 1
                if not page:
                    break
        out = [self._parse(account, self._get(account, f"messages/{i}", format="full")) for i in dict.fromkeys(ids)]
        account.cursor = str(prof.get("historyId", account.cursor or ""))
        return out

    def _parse(self, account, m):
        hdr = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
        name, addr = parseaddr(hdr.get("from", ""))
        text, html, atts = [], [], []

        def walk(p):
            mt, body = p.get("mimeType", ""), p.get("body", {})
            if p.get("filename") and body.get("attachmentId"):
                if int(body.get("size", 0)) <= settings.ATTACH_MAX_MB * 1024 * 1024:
                    d = self._get(account, f"messages/{m['id']}/attachments/{body['attachmentId']}")
                    atts.append(RawAttachment(p["filename"], mt, base64.urlsafe_b64decode(d["data"] + "==")))
            elif mt == "text/plain" and body.get("data"):
                text.append(base64.urlsafe_b64decode(body["data"] + "==").decode("utf-8", "replace"))
            elif mt == "text/html" and body.get("data"):
                html.append(base64.urlsafe_b64decode(body["data"] + "==").decode("utf-8", "replace"))
            for c in p.get("parts", []) or []:
                walk(c)
        walk(m["payload"])
        rec = [{"email": parseaddr(x)[1], "name": parseaddr(x)[0], "type": "to"} for x in hdr.get("to", "").split(",") if x.strip()]
        return RawEmail(m["id"], m.get("threadId", m["id"]), addr.lower(), name, rec, hdr.get("subject", ""),
                        "\n".join(text) or "\n".join(html), datetime.fromtimestamp(int(m["internalDate"]) / 1000, timezone.utc),
                        m.get("labelIds", []), atts, body_is_html=not text and bool(html))

    def send(self, db, account, payload, idem_key, state):
        msg = EmailMessage()
        msg["To"], msg["Subject"] = payload["to"], payload["subject"]
        msg["From"] = account.address
        msg["Message-ID"] = f"<{idem_key}@aeop.local>"
        if payload.get("in_reply_to"):
            msg["In-Reply-To"] = payload["in_reply_to"]
        msg.set_content(payload["body"])
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = _http("POST", f"{self.API}/messages/send", ambiguous_on_timeout=True,
                  headers={"Authorization": f"Bearer {self.access_token(account)}"},
                  json={"raw": raw, **({"threadId": payload["thread_id"]} if payload.get("thread_id") else {})})
        return r.json()["id"], "gmail-api"

    def verify_sent(self, db, account, idem_key, state):
        r = self._get(account, "messages", q=f"in:sent rfc822msgid:{idem_key}@aeop.local", maxResults=1)
        return (r.get("messages") or [{}])[0].get("id")

    def revoke(self, account):
        try:
            tok = json.loads(decrypt(account.token_enc))
            httpx.post("https://oauth2.googleapis.com/revoke", data={"token": tok.get("refresh_token") or tok.get("access_token")}, timeout=10)
        except Exception:
            pass

    def watch(self, account):
        if not settings.GMAIL_PUBSUB_TOPIC:
            return None
        r = _http("POST", f"{self.API}/watch", headers={"Authorization": f"Bearer {self.access_token(account)}"},
                  json={"topicName": settings.GMAIL_PUBSUB_TOPIC, "labelIds": ["INBOX"]})
        return r.json()


# ------------------------------------------------------------------ Outlook
class OutlookProvider(EmailProvider):
    name = "outlook"
    scopes = ["offline_access", "Mail.Read", "Mail.Send", "User.Read"]
    API = "https://graph.microsoft.com/v1.0"

    @property
    def token_url(self): return f"https://login.microsoftonline.com/{settings.MS_TENANT}/oauth2/v2.0/token"
    def _client(self): return settings.MS_CLIENT_ID, settings.MS_CLIENT_SECRET
    def configured(self): return bool(settings.MS_CLIENT_ID and settings.MS_CLIENT_SECRET)

    def auth_url(self, state):
        q = {"client_id": settings.MS_CLIENT_ID, "response_type": "code", "redirect_uri": self.redirect_uri(),
             "response_mode": "query", "scope": " ".join(self.scopes), "state": state, "prompt": "select_account"}
        return f"https://login.microsoftonline.com/{settings.MS_TENANT}/oauth2/v2.0/authorize?" + urlencode(q)

    def exchange_code(self, code):
        r = _http("POST", self.token_url, data={"code": code, "client_id": settings.MS_CLIENT_ID, "client_secret": settings.MS_CLIENT_SECRET,
                                                "redirect_uri": self.redirect_uri(), "grant_type": "authorization_code",
                                                "scope": " ".join(self.scopes)})
        tok = self._tokens_from(r.json())
        me = _http("GET", f"{self.API}/me", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
        return {"tokens": tok, "address": (me.get("mail") or me.get("userPrincipalName")), "cursor": None, "scopes": self.scopes}

    def _h(self, account, **extra):
        return {"Authorization": f"Bearer {self.access_token(account)}", **extra}

    def sync(self, db, account):
        url = account.cursor or (f"{self.API}/me/mailFolders/inbox/messages/delta?$top=25&$select=id,conversationId,from,toRecipients,"
                                 "subject,body,receivedDateTime,hasAttachments,categories,isDraft")
        out, pages, delta = [], 0, None
        while url and pages < 6:
            d = _http("GET", url, headers=self._h(account, Prefer='odata.maxpagesize=25, outlook.body-content-type="text"')).json()
            for m in d.get("value", []):
                if "@removed" in m or m.get("isDraft"):
                    continue
                out.append(self._parse(account, m))
            url, delta, pages = d.get("@odata.nextLink"), d.get("@odata.deltaLink", delta), pages + 1
        if delta:
            account.cursor = delta
        return out

    def _parse(self, account, m):
        fa = (m.get("from") or {}).get("emailAddress", {})
        atts = []
        if m.get("hasAttachments"):
            for a in _http("GET", f"{self.API}/me/messages/{m['id']}/attachments", headers=self._h(account)).json().get("value", []):
                if a.get("@odata.type", "").endswith("fileAttachment") and a.get("size", 0) <= settings.ATTACH_MAX_MB * 1024 * 1024:
                    atts.append(RawAttachment(a["name"], a.get("contentType", ""), base64.b64decode(a["contentBytes"])))
        body = m.get("body", {})
        rec = [{"email": r["emailAddress"].get("address", ""), "name": r["emailAddress"].get("name", ""), "type": "to"} for r in m.get("toRecipients", [])]
        rd = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
        return RawEmail(m["id"], m.get("conversationId", m["id"]), (fa.get("address") or "").lower(), fa.get("name", ""), rec,
                        m.get("subject", ""), body.get("content", ""), rd, m.get("categories", []), atts,
                        body_is_html=body.get("contentType", "").lower() == "html")

    def send(self, db, account, payload, idem_key, state):
        did = state.get("draft_id")
        if not did:
            if payload.get("in_reply_to"):
                did = _http("POST", f"{self.API}/me/messages/{payload['in_reply_to']}/createReply", headers=self._h(account)).json()["id"]
                _http("PATCH", f"{self.API}/me/messages/{did}", headers=self._h(account),
                      json={"body": {"contentType": "Text", "content": payload["body"]}})
            else:
                did = _http("POST", f"{self.API}/me/messages", headers=self._h(account), json={
                    "subject": payload["subject"], "body": {"contentType": "Text", "content": payload["body"]},
                    "toRecipients": [{"emailAddress": {"address": payload["to"]}}]}).json()["id"]
            state["draft_id"] = did  # persisted by the worker BEFORE sending => safe verification on retry
        _http("POST", f"{self.API}/me/messages/{did}/send", ambiguous_on_timeout=True, headers=self._h(account))
        return did, "graph-api"

    def verify_sent(self, db, account, idem_key, state):
        did = state.get("draft_id")
        if not did:
            return None
        r = httpx.get(f"{self.API}/me/messages/{did}", headers=self._h(account), timeout=30)
        if r.status_code == 404:
            return did  # draft is gone => it was sent
        if r.status_code == 200 and not r.json().get("isDraft", True):
            return did
        return None

    def ensure_subscription(self, account):
        if not settings.BASE_URL.startswith("https://"):
            return None
        exp = (utcnow().replace(microsecond=0)).isoformat().replace("+00:00", "Z")
        body = {"changeType": "created", "notificationUrl": f"{settings.BASE_URL}/api/v1/webhooks/outlook",
                "resource": "me/mailFolders('inbox')/messages", "expirationDateTime": exp,
                "clientState": settings.WEBHOOK_SECRET}
        from datetime import timedelta
        body["expirationDateTime"] = (utcnow() + timedelta(minutes=4200)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return _http("POST", f"{self.API}/subscriptions", headers=self._h(account), json=body).json()


# ------------------------------------------------------------------ Demo (local)
class DemoProvider(EmailProvider):
    name = "demo"
    def configured(self): return True
    def sync(self, db, account): return []

    def send(self, db, account, payload, idem_key, state):
        return f"demo-{uuid.uuid4().hex[:12]}", "SIMULATED (demo mailbox: no real email was sent)"


PROVIDERS = {"gmail": GmailProvider(), "outlook": OutlookProvider(), "demo": DemoProvider()}
