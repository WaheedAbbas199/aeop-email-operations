"""Tamper-evident audit log. Each row hashes the previous row of the same organization (hash chain).
Rows are inserted in the caller's transaction, so an audit event is atomic with the business change.
The org row is locked (no-op UPDATE) so chain appends are serialized per organization."""
import hashlib
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update

from .models import AuditLog, Organization, utcnow

_SENSITIVE = ("token", "password", "secret", "api_key", "apikey", "authorization", "cookie", "credential")


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if any(s in str(k).lower() for s in _SENSITIVE) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, str) and len(obj) > 2000:
        return obj[:2000] + "…[truncated]"
    return obj


def _canon(row: dict) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev: str, d: dict) -> str:
    return hashlib.sha256((prev + _canon(d)).encode()).hexdigest()


def _material(r: AuditLog) -> dict:
    return {"org": r.org_id, "ts": r.ts.isoformat(), "actor_type": r.actor_type, "actor_id": r.actor_id,
            "action": r.action, "rt": r.resource_type, "rid": r.resource_id, "run": r.run_id,
            "corr": r.correlation_id, "email": r.email_id, "authz": r.authorization,
            "result": r.result, "detail": r.detail}


def audit(db, org_id: str, actor_type: str, actor_id: str, action: str, resource_type: Optional[str] = None,
          resource_id: Optional[str] = None, *, authorization="allowed", result="ok", detail=None,
          run_id=None, correlation_id=None, email_id=None) -> AuditLog:
    db.execute(update(Organization).where(Organization.id == org_id).values(id=Organization.id))  # serialize
    prev = db.execute(select(AuditLog.hash).where(AuditLog.org_id == org_id)
                      .order_by(AuditLog.id.desc()).limit(1)).scalar() or ""
    row = AuditLog(org_id=org_id, ts=utcnow(), actor_type=actor_type, actor_id=str(actor_id or ""), action=action,
                   resource_type=resource_type, resource_id=str(resource_id) if resource_id else None,
                   run_id=run_id, correlation_id=correlation_id, email_id=email_id, authorization=authorization,
                   result=result, detail=redact(detail or {}), prev_hash=prev)
    row.hash = _digest(prev, _material(row))
    db.add(row)
    db.flush()
    return row


def verify_chain(db, org_id: str) -> dict:
    prev, n = "", 0
    for r in db.execute(select(AuditLog).where(AuditLog.org_id == org_id).order_by(AuditLog.id)).scalars():
        n += 1
        if r.prev_hash != prev or r.hash != _digest(prev, _material(r)):
            return {"ok": False, "checked": n, "broken_at_id": r.id}
        prev = r.hash
    return {"ok": True, "checked": n, "broken_at_id": None}
