"""Deterministic policy + approval service. An LLM is never the authority for access control."""
import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from typing import Optional

from sqlalchemy import select

from . import jobs
from .audit import audit
from .errors import Conflict, NotFound, PermissionDenied
from .models import Approval, Invoice, Organization, User, WorkflowRun, utcnow
from .security import perms_for

DEFAULT_SETTINGS = {
    "approval_required": {"send_email": True, "create_calendar_event": True, "update_crm_sensitive": True},
    "invoice_approval_threshold": 5000.0,
    "confidence_threshold": 0.6,
    "separation_of_duties": True,
    "approval_expiry_hours": 72,
    "sla_hours": {"P1": 4, "P2": 8, "P3": 24, "P4": 72},
    "max_escalation_level": 3,
    "timezone": "UTC",
    "working_hours": {"start_hour": 9, "end_hour": 17, "days": [0, 1, 2, 3, 4]},
    "workflow_limits": {"max_steps": 40, "max_tool_calls": 30, "max_model_calls": 6, "max_seconds": 120},
    "unusual_amount_multiplier": 3.0,
    "po_total_tolerance_pct": 0.0,
    "poll_interval_seconds": 120,
}
BLOCKED_ACTIONS = {"execute_payment", "bank_transfer", "change_vendor_bank_details"}
ACTION_PERM = {"send_email": "email.send", "create_calendar_event": "calendar.write",
               "update_crm_sensitive": "crm.write", "create_accounting_draft": "accounting.draft"}
SENSITIVE_CRM_FIELDS = {"status", "company", "email"}


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a


def org_settings(org: Organization) -> dict:
    return _merge(deepcopy(DEFAULT_SETTINGS), org.settings or {})


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def decide(db, org: Organization, action: str, payload: dict) -> dict:
    """Returns {allowed, requires_approval, permission, risk, reason}."""
    s = org_settings(org)
    if action in BLOCKED_ACTIONS:
        return {"allowed": False, "requires_approval": False, "permission": None, "risk": "critical",
                "reason": f"'{action}' is not supported by this platform (payments / vendor bank changes are out of scope)"}
    perm = ACTION_PERM.get(action)
    if not perm:
        return {"allowed": False, "requires_approval": False, "permission": None, "risk": "high", "reason": "unknown action"}
    risk, requires, reason = "medium", True, ""
    if action == "send_email":
        requires = s["approval_required"].get("send_email", True)
        reason = "External email is visible outside the organization"
    elif action == "create_calendar_event":
        requires = s["approval_required"].get("create_calendar_event", True)
        reason = "Creating a calendar event commits the organization's time"
    elif action == "update_crm_sensitive":
        requires = s["approval_required"].get("update_crm_sensitive", True)
        reason = "Changes sensitive fields of an existing CRM record"
    elif action == "create_accounting_draft":
        inv = db.get(Invoice, payload.get("invoice_id")) if payload.get("invoice_id") else None
        total = (inv.total_cents or 0) / 100 if inv and inv.org_id == org.id else 0
        requires = total > float(s["invoice_approval_threshold"])
        risk = "high" if requires else "low"
        reason = f"Invoice total {total:.2f} exceeds approval threshold {s['invoice_approval_threshold']}" if requires else ""
    return {"allowed": True, "requires_approval": requires, "permission": perm, "risk": risk, "reason": reason}


def create_approval(db, org: Organization, *, run: Optional[WorkflowRun], email_id, action_type: str, payload: dict,
                    requested_by: str, reason: str = "") -> Approval:
    d = decide(db, org, action_type, payload)
    if not d["allowed"]:
        raise PermissionDenied(d["reason"])
    s = org_settings(org)
    ap = Approval(org_id=org.id, run_id=run.id if run else None, email_id=email_id, action_type=action_type,
                  risk=d["risk"], required_permission=d["permission"], payload=payload, payload_hash=payload_hash(payload),
                  reason=reason or d["reason"], requested_by=requested_by,
                  expires_at=utcnow() + timedelta(hours=s["approval_expiry_hours"]))
    db.add(ap)
    db.flush()
    audit(db, org.id, "agent", requested_by, "approval.requested", "approval", ap.id, detail={
        "action": action_type, "risk": ap.risk, "requires_human": d["requires_approval"], "hash": ap.payload_hash},
        run_id=ap.run_id, email_id=email_id, correlation_id=run.correlation_id if run else None)
    if not d["requires_approval"]:
        ap.status, ap.decided_by, ap.decided_at, ap.decision_reason = "approved", "policy:auto", utcnow(), "Policy: approval not required"
        audit(db, org.id, "system", "policy", "approval.auto_approved", "approval", ap.id, run_id=ap.run_id)
        jobs.enqueue(db, org.id, "execute_approval", {"approval_id": ap.id}, dedupe_key=f"exec:{ap.id}")
    return ap


def decide_approval(db, user: User, approval_id: str, decision: str, reason: str = "") -> Approval:
    from . import workflows  # late import (cycle)
    ap = db.get(Approval, approval_id)
    if not ap or ap.org_id != user.org_id:
        raise NotFound("Approval not found")
    perms = perms_for(user.role)
    run = db.get(WorkflowRun, ap.run_id) if ap.run_id else None
    cid = run.correlation_id if run else None

    def deny(msg):
        audit(db, user.org_id, "user", user.id, "approval.decision_denied", "approval", ap.id, authorization="denied",
              result="denied", detail={"why": msg}, run_id=ap.run_id, correlation_id=cid)
        db.commit()
        raise PermissionDenied(msg)

    if "approval.decide" not in perms or ap.required_permission not in perms:
        deny("You do not have the permission required to decide this approval")
    if ap.status != "pending":
        raise Conflict(f"Approval is already {ap.status}")
    if ap.expires_at and ap.expires_at < utcnow():
        ap.status = "expired"
        audit(db, user.org_id, "system", "policy", "approval.expired", "approval", ap.id, run_id=ap.run_id)
        db.commit()
        raise Conflict("Approval has expired")
    if decision == "approve":
        if payload_hash(ap.payload) != ap.payload_hash:
            audit(db, user.org_id, "system", "policy", "approval.tamper_detected", "approval", ap.id, result="blocked",
                  detail={"expected": ap.payload_hash}, run_id=ap.run_id)
            ap.status = "cancelled"
            db.commit()
            raise Conflict("Payload changed after approval was requested; re-approval required")
        sod = org_settings(db.get(Organization, user.org_id))["separation_of_duties"]
        if sod and ap.risk in ("high", "critical") and run and run.created_by == user.id:
            deny("Separation of duties: you started this workflow and cannot approve its high-risk action")
        if ap.risk in ("high", "critical") and user.role not in ("org_admin", "platform_admin", "manager", "finance"):
            deny("High-risk actions require manager, finance or admin approval")
    ap.status = "approved" if decision == "approve" else "rejected"
    ap.decided_by, ap.decided_at, ap.decision_reason = user.id, utcnow(), reason
    audit(db, user.org_id, "user", user.id, f"approval.{ap.status}", "approval", ap.id, detail={"reason": reason, "action": ap.action_type},
          run_id=ap.run_id, correlation_id=cid, email_id=ap.email_id)
    if ap.status == "approved":
        jobs.enqueue(db, user.org_id, "execute_approval", {"approval_id": ap.id}, dedupe_key=f"exec:{ap.id}")
        if run:
            workflows.on_approval_decided(db, run, ap)
    elif run:
        workflows.on_approval_decided(db, run, ap)
    db.commit()
    return ap


def expire_approvals(db) -> int:
    n = 0
    rows = db.execute(select(Approval).where(Approval.status == "pending", Approval.expires_at < utcnow())).scalars().all()
    for ap in rows:
        ap.status = "expired"
        audit(db, ap.org_id, "system", "policy", "approval.expired", "approval", ap.id, run_id=ap.run_id)
        n += 1
    db.commit()
    return n
