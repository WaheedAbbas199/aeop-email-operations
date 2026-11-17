import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import settings
from .errors import AEOPError

# ---------------------------------------------------------------- RBAC
ALL_PERMS = {
    "email.read", "attachment.read", "customer.read", "ticket.read", "ticket.write",
    "crm.read", "crm.write", "invoice.read", "invoice.write", "accounting.draft",
    "calendar.read", "calendar.write", "email.draft", "email.send", "approval.decide",
    "user.manage", "integration.manage", "audit.read", "job.retry", "policy.manage",
    "workflow.run", "agent.manage",
}
AGENT_ONLY_PERMS = {"approval.request", "notify"}

_mgr = {"email.read", "attachment.read", "customer.read", "ticket.read", "ticket.write", "crm.read", "crm.write",
        "invoice.read", "calendar.read", "calendar.write", "email.draft", "approval.decide", "audit.read",
        "job.retry", "workflow.run"}
ROLE_PERMS = {
    "platform_admin": set(ALL_PERMS),
    "org_admin": set(ALL_PERMS),
    "manager": _mgr | {"email.send", "accounting.draft", "invoice.write"},
    "support": {"email.read", "customer.read", "ticket.read", "ticket.write", "calendar.read", "email.draft",
                "email.send", "approval.decide", "workflow.run", "crm.read"},
    "sales": {"email.read", "customer.read", "crm.read", "crm.write", "calendar.read", "calendar.write",
              "email.draft", "email.send", "approval.decide", "workflow.run"},
    "finance": {"email.read", "attachment.read", "customer.read", "invoice.read", "invoice.write",
                "accounting.draft", "email.draft", "approval.decide", "workflow.run"},
    "auditor": {"email.read", "customer.read", "ticket.read", "crm.read", "invoice.read", "calendar.read", "audit.read"},
}
ROLES = list(ROLE_PERMS)


def perms_for(role: str) -> set:
    return ROLE_PERMS.get(role, set())


# ---------------------------------------------------------------- passwords
_ITER = 210_000


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def check_password_strength(pw: str):
    if len(pw) < 10 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        raise AEOPError("Password must be at least 10 characters and contain letters and digits")


# ---------------------------------------------------------------- encryption
_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("enc:" + settings.SECRET_KEY).encode()).digest()))


def encrypt(s: str) -> str:
    return _fernet.encrypt(s.encode()).decode()


def decrypt(s: str) -> str:
    try:
        return _fernet.decrypt(s.encode()).decode()
    except InvalidToken as e:
        raise AEOPError("Stored credential cannot be decrypted (SECRET_KEY changed?)") from e


# ---------------------------------------------------------------- tokens
def make_session(user, csrf: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_HOURS)
    return jwt.encode({"sub": user.id, "org": user.org_id, "csrf": csrf, "exp": exp, "typ": "session"},
                      settings.SECRET_KEY, algorithm="HS256")


def make_state(payload: dict, minutes=10) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode({**payload, "exp": exp, "typ": "state", "nonce": secrets.token_urlsafe(8)},
                      settings.SECRET_KEY, algorithm="HS256")


def decode_token(tok: str, typ: str) -> dict:
    data = jwt.decode(tok, settings.SECRET_KEY, algorithms=["HS256"])
    if data.get("typ") != typ:
        raise jwt.InvalidTokenError("wrong token type")
    return data


# ---------------------------------------------------------------- rate limit (single process)
class RateLimiter:
    """Sliding-window limiter. In-memory => per-process. Use Redis for multi-process deployments."""
    def __init__(self):
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self):
        with self.lock:
            self.hits.clear()


limiter = RateLimiter()
