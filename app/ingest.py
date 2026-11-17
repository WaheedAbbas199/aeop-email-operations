"""Email ingestion: idempotent persistence, attachment safety validation, HTML -> text, job enqueue."""
import hashlib
import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from sqlalchemy import select

from . import jobs
from .audit import audit
from .config import DATA_DIR, settings
from .intelligence import detect_injection
from .models import Attachment, Email, EmailAccount, Organization, WorkflowRun, utcnow, uid
from .providers import PROVIDERS, RawAttachment, RawEmail

ALLOWED_EXT = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".txt": "text/plain", ".csv": "text/csv"}


class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        if tag in ("br", "p", "div", "tr", "li"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def html_to_text(html: str) -> str:
    p = _Strip()
    p.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(p.out)).strip()


def sanitize_filename(name: str) -> str:
    name = Path((name or "file").replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120]
    return name or "file"


def inspect_attachment(filename: str, data: bytes):
    """Returns (status, reason, text). Never executes or opens the content beyond text extraction."""
    ext = Path(filename).suffix.lower()
    if len(data) > settings.ATTACH_MAX_MB * 1024 * 1024:
        return "blocked", "file too large", ""
    if ext not in ALLOWED_EXT:
        return "blocked", f"file type '{ext or 'unknown'}' not allowed", ""
    if ext == ".pdf":
        if not data.startswith(b"%PDF"):
            return "blocked", "content is not a PDF (extension/content mismatch)", ""
        if re.search(rb"/(JavaScript|JS|Launch|EmbeddedFile)\b", data[:2_000_000]):
            return "blocked", "PDF contains active content (JavaScript/Launch/EmbeddedFile)", ""
        try:
            from pypdf import PdfReader
            txt = "\n".join((pg.extract_text() or "") for pg in PdfReader(BytesIO(data)).pages[:30])
        except Exception:
            return "ok", "PDF text extraction failed (scanned?); OCR is not configured", ""
        return "ok", "" if txt.strip() else "no text layer (OCR not configured)", txt
    if ext in (".png", ".jpg", ".jpeg"):
        magic = data.startswith(b"\x89PNG") if ext == ".png" else data.startswith(b"\xff\xd8")
        if not magic:
            return "blocked", "content does not match image extension", ""
        return "ok", "image: OCR is not configured, text not extracted", ""
    if b"\x00" in data[:4096]:
        return "blocked", "binary content in text file", ""
    return "ok", "", data.decode("utf-8", "replace")[:200_000]


def _store(org_id: str, att_id: str, data: bytes) -> str:
    d = DATA_DIR / "attachments" / org_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{att_id}.bin"
    p.write_bytes(data)
    return str(p.relative_to(DATA_DIR))


def ingest_raw_email(db, account: EmailAccount, raw: RawEmail, *, process=True):
    """Returns (email, created). Idempotent on (account, provider_msg_id)."""
    ex = db.execute(select(Email).where(Email.account_id == account.id, Email.provider_msg_id == raw.provider_msg_id)).scalars().first()
    if ex:
        return ex, False
    body = html_to_text(raw.body_text) if raw.body_is_html or re.search(r"<(html|body|div|p)[ >]", raw.body_text or "", re.I) else (raw.body_text or "")
    em = Email(org_id=account.org_id, account_id=account.id, provider_msg_id=raw.provider_msg_id, thread_id=raw.thread_id or raw.provider_msg_id,
               sender=raw.sender.lower(), sender_name=raw.sender_name, recipients=raw.recipients, subject=(raw.subject or "")[:500],
               body_text=body[:100_000], labels=raw.labels, received_at=raw.received_at, status="received")
    db.add(em)
    db.flush()
    atts = []
    for ra in raw.attachments:
        fname = sanitize_filename(ra.filename)
        status, reason, text = inspect_attachment(fname, ra.data)
        a = Attachment(org_id=account.org_id, email_id=em.id, filename=fname, mime=ra.mime[:100], size=len(ra.data),
                       sha256=hashlib.sha256(ra.data).hexdigest(), text_content=text, safety_status=status, safety_reason=reason)
        db.add(a)
        db.flush()
        if status == "ok":
            a.storage_path = _store(account.org_id, a.id, ra.data)
        atts.append(a)
    em.injection_flags = detect_injection(f"{em.subject}\n{em.body_text}\n" + "\n".join(a.text_content[:3000] for a in atts))
    audit(db, account.org_id, "integration", account.provider, "email.ingested", "email", em.id, email_id=em.id,
          correlation_id=em.correlation_id, detail={"attachments": len(atts), "blocked": [a.filename for a in atts if a.safety_status == "blocked"],
                                                  "injection_suspected": bool(em.injection_flags)})
    if account.address and em.sender == account.address.lower():
        em.status = "ignored"  # our own outgoing mail: avoid loops
        return em, True
    if process:
        run = db.execute(select(WorkflowRun).where(WorkflowRun.org_id == account.org_id, WorkflowRun.state == "awaiting_response",
                                                   WorkflowRun.workflow == "meeting")).scalars().all()
        linked = next((r for r in run if _thread_of(db, r) == em.thread_id), None)
        if linked:
            em.status = "linked_reply"
            jobs.enqueue(db, account.org_id, "meeting_reply", {"email_id": em.id, "run_id": linked.id}, dedupe_key=f"reply:{em.id}")
        else:
            jobs.enqueue(db, account.org_id, "process_email", {"email_id": em.id}, dedupe_key=f"proc:{em.id}")
    return em, True


def _thread_of(db, run):
    e = db.get(Email, run.email_id)
    return e.thread_id if e else None


def sync_account(db, account_id: str) -> int:
    acc = db.get(EmailAccount, account_id)
    if not acc or acc.status in ("disconnected",):
        return 0
    prov = PROVIDERS[acc.provider]
    try:
        raws = prov.sync(db, acc)
    except Exception as e:
        acc.last_error = str(e)[:300]
        if acc.status != "reauth_required":
            acc.status = "error" if not isinstance(e, __import__("app.errors", fromlist=["RetryableError"]).RetryableError) else acc.status
        db.commit()
        raise
    n = 0
    for raw in raws:
        _, created = ingest_raw_email(db, acc, raw)
        n += int(created)
    acc.last_sync_at, acc.last_error = utcnow(), None
    if acc.status == "error":
        acc.status = "connected"
    audit(db, acc.org_id, "integration", acc.provider, "mailbox.synced", "email_account", acc.id, detail={"new_emails": n})
    db.commit()
    return n
