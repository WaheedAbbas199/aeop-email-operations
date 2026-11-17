import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import jobs, policy, seed, workflows
from .audit import audit, verify_chain
from .config import settings
from .db import get_db
from .errors import AEOPError, Conflict, NotFound, PermanentError, PermissionDenied
from .llm import engine_name, llm_enabled
from .models import (AgentConfig, Approval, Attachment, Classification, CalendarEvent, CrmRecord, Customer, Email, EmailAccount,
                     EvaluationRun, IdempotencyRecord, Invoice, Job, Notification, Organization, Outbox, Ticket, User,
                     WorkflowRun, WorkflowStep, utcnow)
from .providers import PROVIDERS, RawEmail
from .security import (ALL_PERMS, ROLES, check_password_strength, decode_token, encrypt, hash_password, limiter, make_session,
                       make_state, perms_for, verify_password)
from .tools import AGENT_INFO, AGENT_PERMS, AGENT_TOOLS, describe_tools
import json as _json

router = APIRouter(prefix="/api/v1")
SECRET_COLS = {"password_hash", "token_enc"}
ACTIVE_STATES = ["queued", "processing", "awaiting_approval", "awaiting_response", "approved", "executing", "retry_scheduled"]


def to_dict(o, exclude=()):
    d = {}
    for c in o.__table__.columns:
        if c.name in SECRET_COLS or c.name in exclude:
            continue
        v = getattr(o, c.name)
        d[c.name] = v.isoformat() if isinstance(v, datetime) else v
    return d


def page_of(db, q, page, size, ser):
    total = db.execute(select(func.count()).select_from(q.order_by(None).subquery())).scalar()
    rows = db.execute(q.limit(size).offset((page - 1) * size)).all()
    return {"items": [ser(r) for r in rows], "total": total, "page": page, "page_size": size}


# ------------------------------------------------------------------ auth dependencies
def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth, via_cookie = request.headers.get("authorization", ""), False
    if auth.lower().startswith("bearer "):
        tok = auth[7:]
    else:
        tok, via_cookie = request.cookies.get("aeop_session"), True
    if not tok:
        raise HTTPException(401, "Not authenticated")
    try:
        data = decode_token(tok, "session")
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired or invalid")
    if via_cookie and request.method not in ("GET", "HEAD", "OPTIONS"):
        h = request.headers.get("x-csrf-token", "")
        if not h or not hmac.compare_digest(h, data["csrf"]) or h != request.cookies.get("aeop_csrf"):
            raise HTTPException(403, "CSRF validation failed")
    u = db.get(User, data["sub"])
    if not u or not u.is_active or u.org_id != data["org"]:
        raise HTTPException(401, "Account disabled or not found")
    return u


def require(perm: str):
    def dep(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        if perm not in perms_for(user.role):
            audit(db, user.org_id, "user", user.id, "access.denied", authorization="denied", result="denied", detail={"permission": perm})
            db.commit()
            raise HTTPException(403, f"Missing permission: {perm}")
        return user
    return dep


def org_of(db, user) -> Organization:
    return db.get(Organization, user.org_id)


def own(db, model, id_, user):
    o = db.get(model, id_)
    if not o or o.org_id != user.org_id:
        raise HTTPException(404, "Not found")
    return o


# ------------------------------------------------------------------ schemas
class LoginIn(BaseModel):
    email: str
    password: str


class RegisterIn(BaseModel):
    org_name: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str


class ManualAttachment(BaseModel):
    filename: str = Field(max_length=200)
    text: str = Field(max_length=200_000)


class ManualEmailIn(BaseModel):
    sender: EmailStr
    sender_name: str = ""
    subject: str = Field(max_length=500)
    body: str = Field(max_length=50_000)
    attachments: list[ManualAttachment] = []
    thread_id: Optional[str] = None


class RunIn(BaseModel):
    workflow: str

    @field_validator("workflow")
    @classmethod
    def ok(cls, v):
        if v not in ("complaint", "invoice", "meeting", "sales_lead", "reclassify"):
            raise ValueError("unknown workflow")
        return v


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str = Field("", max_length=1000)


class SlotIn(BaseModel):
    slot_index: int = Field(ge=0, le=10)


class UserIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=100)
    role: str
    password: str


class UserPatch(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    name: Optional[str] = None
    password: Optional[str] = None


class AgentPatch(BaseModel):
    enabled: bool


class SettingsIn(BaseModel):
    org_name: Optional[str] = Field(None, min_length=2, max_length=100)
    confidence_threshold: Optional[float] = Field(None, ge=0, le=1)
    invoice_approval_threshold: Optional[float] = Field(None, ge=0)
    separation_of_duties: Optional[bool] = None
    approval_expiry_hours: Optional[int] = Field(None, ge=1, le=720)
    timezone: Optional[str] = None
    working_hours: Optional[dict] = None
    approval_required: Optional[dict] = None
    sla_hours: Optional[dict] = None
    unusual_amount_multiplier: Optional[float] = Field(None, ge=1)
    po_total_tolerance_pct: Optional[float] = Field(None, ge=0, le=100)
    poll_interval_seconds: Optional[int] = Field(None, ge=30)


# ------------------------------------------------------------------ health
@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request, db: Session = Depends(get_db)):
    db.execute(select(1))
    w = getattr(request.app.state, "worker", None)
    return {"status": "ready", "database": "ok", "worker": "running" if w and w.threads[0].is_alive() else ("disabled" if not settings.WORKER_ENABLED else "stopped")}


# ------------------------------------------------------------------ auth
def _session(resp: Response, user: User):
    csrf = secrets.token_urlsafe(16)
    kw = dict(samesite="lax", secure=settings.COOKIE_SECURE, max_age=settings.SESSION_HOURS * 3600, path="/")
    resp.set_cookie("aeop_session", make_session(user, csrf), httponly=True, **kw)
    resp.set_cookie("aeop_csrf", csrf, httponly=False, **kw)


def me_payload(db, u: User):
    o = org_of(db, u)
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "org": {"id": o.id, "name": o.name},
            "permissions": sorted(perms_for(u.role)), "llm_engine": engine_name(), "demo_enabled": settings.ENABLE_DEMO,
            "signup_enabled": settings.ALLOW_SIGNUP}


@router.get("/config")
def public_config():
    return {"signup_enabled": settings.ALLOW_SIGNUP, "llm_engine": engine_name()}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, resp: Response, db: Session = Depends(get_db)):
    key = f"login:{request.client.host if request.client else '?'}:{body.email.lower()}"
    if not limiter.allow(key, 10, 300):
        raise HTTPException(429, "Too many login attempts; try again later")
    u = db.execute(select(User).where(User.email == body.email.lower())).scalars().first()
    if u and u.locked_until and u.locked_until > utcnow():
        raise HTTPException(423, "Account temporarily locked; try again later")
    if not u or not u.is_active or not verify_password(body.password, u.password_hash):
        if u:
            u.failed_logins += 1
            if u.failed_logins >= 5:
                u.locked_until, u.failed_logins = utcnow() + timedelta(minutes=15), 0
            audit(db, u.org_id, "user", u.id, "auth.login_failed", authorization="denied", result="denied")
            db.commit()
        raise HTTPException(401, "Invalid email or password")
    u.failed_logins, u.locked_until = 0, None
    audit(db, u.org_id, "user", u.id, "auth.login", "user", u.id)
    db.commit()
    _session(resp, u)
    return me_payload(db, u)


@router.post("/auth/register")
def register(body: RegisterIn, request: Request, resp: Response, db: Session = Depends(get_db)):
    if not settings.ALLOW_SIGNUP:
        raise HTTPException(403, "Self-service sign-up is disabled")
    if not limiter.allow(f"reg:{request.client.host if request.client else '?'}", 5, 3600):
        raise HTTPException(429, "Too many sign-ups")
    check_password_strength(body.password)
    if db.execute(select(User.id).where(User.email == body.email.lower())).first():
        raise HTTPException(409, "Email already registered")
    _, u = seed.create_org_with_admin(db, body.org_name, body.email, body.name, body.password)
    _session(resp, u)
    return me_payload(db, u)


@router.post("/auth/logout")
def logout(resp: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    audit(db, user.org_id, "user", user.id, "auth.logout", "user", user.id)
    db.commit()
    resp.delete_cookie("aeop_session", path="/")
    resp.delete_cookie("aeop_csrf", path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return me_payload(db, user)


# ------------------------------------------------------------------ analytics
@router.get("/analytics")
def analytics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    o = user.org_id
    runs = db.execute(select(WorkflowRun).where(WorkflowRun.org_id == o)).scalars().all()
    by_state, by_wf = {}, {}
    for r in runs:
        by_state[r.state] = by_state.get(r.state, 0) + 1
        by_wf[r.workflow] = by_wf.get(r.workflow, 0) + 1
    done = [r for r in runs if r.state == "completed" and r.finished_at]
    lat = [(r.finished_at - r.started_at).total_seconds() for r in done]
    finished = by_state.get("completed", 0) + by_state.get("failed", 0) + by_state.get("rejected", 0) + by_state.get("needs_review", 0)
    cats = dict(db.execute(select(Classification.category, func.count()).where(Classification.org_id == o).group_by(Classification.category)).all())
    ti = sum(r.tokens_in for r in runs)
    to_ = sum(r.tokens_out for r in runs)
    accounts = db.execute(select(EmailAccount).where(EmailAccount.org_id == o)).scalars().all()
    recent = db.execute(select(WorkflowRun, Email.subject).join(Email, Email.id == WorkflowRun.email_id).where(WorkflowRun.org_id == o)
                        .order_by(WorkflowRun.created_at.desc()).limit(8)).all()
    out = {
        "emails_total": db.execute(select(func.count(Email.id)).where(Email.org_id == o)).scalar(),
        "emails_processed": db.execute(select(func.count(Classification.id)).where(Classification.org_id == o)).scalar(),
        "pending_approvals": db.execute(select(func.count(Approval.id)).where(Approval.org_id == o, Approval.status == "pending")).scalar(),
        "active_workflows": sum(by_state.get(s, 0) for s in ACTIVE_STATES),
        "needs_review": by_state.get("needs_review", 0),
        "failed_jobs": db.execute(select(func.count(Job.id)).where(Job.org_id == o, Job.status == "dead")).scalar(),
        "avg_latency_s": round(sum(lat) / len(lat), 2) if lat else None,
        "completion_rate": round(by_state.get("completed", 0) / finished * 100, 1) if finished else None,
        "runs_by_state": by_state, "runs_by_workflow": by_wf, "categories": cats,
        "approvals_by_status": dict(db.execute(select(Approval.status, func.count()).where(Approval.org_id == o).group_by(Approval.status)).all()),
        "tokens": {"in": ti, "out": to_, "estimated_cost": round(ti / 1000 * settings.LLM_PRICE_IN_PER_1K + to_ / 1000 * settings.LLM_PRICE_OUT_PER_1K, 4)},
        "engine": engine_name(),
        "integrations": [{"id": a.id, "provider": a.provider, "address": a.address, "status": a.status, "last_sync_at": a.last_sync_at.isoformat() if a.last_sync_at else None} for a in accounts],
        "recent_runs": [{"id": r.id, "workflow": r.workflow, "state": r.state, "subject": s, "summary": r.summary, "created_at": r.created_at.isoformat()} for r, s in recent],
    }
    if "audit.read" in perms_for(user.role):
        from .models import AuditLog
        out["recent_audit"] = [{"ts": a.ts.isoformat(), "action": a.action, "actor": f"{a.actor_type}:{a.actor_id}", "result": a.result}
                               for a in db.execute(select(AuditLog).where(AuditLog.org_id == o).order_by(AuditLog.id.desc()).limit(8)).scalars()]
    return out


# ------------------------------------------------------------------ emails
@router.get("/emails")
def list_emails(q: str = "", category: str = "", status: str = "", priority: str = "", page: int = Query(1, ge=1),
                page_size: int = Query(20, ge=1, le=100), user: User = Depends(require("email.read")), db: Session = Depends(get_db)):
    stmt = select(Email, Classification).outerjoin(Classification, Classification.email_id == Email.id).where(Email.org_id == user.org_id)
    if q:
        stmt = stmt.where(or_(Email.subject.ilike(f"%{q}%"), Email.sender.ilike(f"%{q}%")))
    if category:
        stmt = stmt.where(Classification.category == category)
    if status:
        stmt = stmt.where(Email.status == status)
    if priority:
        stmt = stmt.where(Classification.priority == priority)
    stmt = stmt.order_by(Email.received_at.desc())
    res = page_of(db, stmt, page, page_size, lambda r: {**to_dict(r[0], exclude=("body_text",)), "snippet": r[0].body_text[:140],
                                                          "category": r[1].category if r[1] else None, "priority": r[1].priority if r[1] else None,
                                                          "confidence": r[1].confidence if r[1] else None,
                                                          "workflow": r[1].recommended_workflow if r[1] else None})
    return res


@router.get("/emails/{eid}")
def get_email(eid: str, user: User = Depends(require("email.read")), db: Session = Depends(get_db)):
    e = own(db, Email, eid, user)
    can_att = "attachment.read" in perms_for(user.role)
    atts = db.execute(select(Attachment).where(Attachment.email_id == e.id)).scalars().all()
    cls = db.execute(select(Classification).where(Classification.email_id == e.id)).scalars().first()
    runs = db.execute(select(WorkflowRun).where(WorkflowRun.email_id == e.id).order_by(WorkflowRun.created_at.desc())).scalars().all()
    aps = db.execute(select(Approval).where(Approval.email_id == e.id).order_by(Approval.created_at.desc())).scalars().all()
    inv = db.execute(select(Invoice).where(Invoice.email_id == e.id)).scalars().all()
    tix = db.execute(select(Ticket).where(Ticket.email_id == e.id)).scalars().all()
    return {"email": to_dict(e), "attachments": [to_dict(a, exclude=() if can_att else ("text_content", "storage_path")) | ({} if can_att else {"text_content": "[requires attachment.read]"}) for a in atts],
            "classification": to_dict(cls) if cls else None, "runs": [to_dict(r) for r in runs],
            "approvals": [to_dict(a) for a in aps], "invoices": [to_dict(i) for i in inv], "tickets": [to_dict(t) for t in tix]}


@router.post("/emails/manual")
def add_manual_email(body: ManualEmailIn, user: User = Depends(require("workflow.run")), db: Session = Depends(get_db)):
    if not limiter.allow(f"manual:{user.id}", 30, 60):
        raise HTTPException(429, "Slow down")
    acc = seed.demo_account(db, user.org_id)
    from .providers import RawAttachment
    raw = RawEmail(f"manual-{secrets.token_hex(8)}", body.thread_id or f"thr-{secrets.token_hex(6)}", body.sender.lower(), body.sender_name,
                   [{"email": acc.address, "name": "", "type": "to"}], body.subject, body.body, utcnow(), ["INBOX"],
                   [RawAttachment(a.filename, "text/plain", a.text.encode()) for a in body.attachments])
    from .ingest import ingest_raw_email
    em, _ = ingest_raw_email(db, acc, raw)
    audit(db, user.org_id, "user", user.id, "email.added_manually", "email", em.id, email_id=em.id)
    db.commit()
    return {"id": em.id}


@router.post("/emails/{eid}/run")
def run_email(eid: str, body: RunIn, user: User = Depends(require("workflow.run")), db: Session = Depends(get_db)):
    e = own(db, Email, eid, user)
    active = db.execute(select(WorkflowRun).where(WorkflowRun.email_id == e.id, WorkflowRun.state.in_(workflows.ACTIVE + ["awaiting_approval", "approved", "executing"]))).scalars().first()
    if active:
        raise HTTPException(409, f"A workflow is already {active.state} for this email")
    for r in db.execute(select(WorkflowRun).where(WorkflowRun.email_id == e.id, WorkflowRun.state == "needs_review")).scalars():
        workflows.transition(db, r, "cancelled", "superseded by manual run")
    forced = "__reclassify__" if body.workflow == "reclassify" else body.workflow
    audit(db, user.org_id, "user", user.id, "workflow.manual_start", "email", e.id, email_id=e.id, detail={"workflow": body.workflow})
    jobs.enqueue(db, user.org_id, "process_email", {"email_id": e.id, "forced": forced, "user_id": user.id})
    db.commit()
    return {"queued": True}


# ------------------------------------------------------------------ runs
@router.get("/runs")
def list_runs(state: str = "", workflow: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              user: User = Depends(require("email.read")), db: Session = Depends(get_db)):
    stmt = select(WorkflowRun, Email.subject).join(Email, Email.id == WorkflowRun.email_id).where(WorkflowRun.org_id == user.org_id)
    if state:
        stmt = stmt.where(WorkflowRun.state == state)
    if workflow:
        stmt = stmt.where(WorkflowRun.workflow == workflow)
    return page_of(db, stmt.order_by(WorkflowRun.created_at.desc()), page, page_size,
                   lambda r: {**to_dict(r[0], exclude=("context",)), "subject": r[1]})


@router.get("/runs/{rid}")
def get_run(rid: str, user: User = Depends(require("email.read")), db: Session = Depends(get_db)):
    r = own(db, WorkflowRun, rid, user)
    steps = db.execute(select(WorkflowStep).where(WorkflowStep.run_id == r.id).order_by(WorkflowStep.seq)).scalars().all()
    aps = db.execute(select(Approval).where(Approval.run_id == r.id).order_by(Approval.created_at)).scalars().all()
    e = db.get(Email, r.email_id)
    return {"run": to_dict(r), "subject": e.subject if e else "", "steps": [to_dict(s) for s in steps], "approvals": [to_dict(a) for a in aps]}


@router.post("/runs/{rid}/cancel")
def cancel_run(rid: str, user: User = Depends(require("workflow.run")), db: Session = Depends(get_db)):
    r = own(db, WorkflowRun, rid, user)
    try:
        for a in db.execute(select(Approval).where(Approval.run_id == r.id, Approval.status == "pending")).scalars():
            a.status, a.decision_reason = "cancelled", "Workflow cancelled"
        workflows.transition(db, r, "cancelled", f"cancelled by {user.email}")
    except AEOPError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/runs/{rid}/confirm-slot")
def confirm_slot(rid: str, body: SlotIn, user: User = Depends(require("calendar.write")), db: Session = Depends(get_db)):
    r = own(db, WorkflowRun, rid, user)
    audit(db, user.org_id, "user", user.id, "meeting.slot_confirmed", "workflow_run", r.id, run_id=r.id, detail={"slot": body.slot_index})
    db.commit()
    try:
        r = workflows.confirm_slot(db, r, body.slot_index, user, "dashboard")
    except PermanentError as e:
        raise HTTPException(409, str(e))
    from .worker import run_pending  # approvals created here are decided by humans; nothing to run now
    return {"state": r.state}


# ------------------------------------------------------------------ approvals
@router.get("/approvals")
def list_approvals(status: str = "pending", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   user: User = Depends(require("email.read")), db: Session = Depends(get_db)):
    stmt = select(Approval, Email.subject, WorkflowRun.workflow).outerjoin(Email, Email.id == Approval.email_id) \
        .outerjoin(WorkflowRun, WorkflowRun.id == Approval.run_id).where(Approval.org_id == user.org_id)
    if status != "all":
        stmt = stmt.where(Approval.status == status)
    mine = perms_for(user.role)
    return page_of(db, stmt.order_by(Approval.created_at.desc()), page, page_size,
                   lambda r: {**to_dict(r[0]), "subject": r[1], "workflow": r[2],
                              "can_decide": "approval.decide" in mine and r[0].required_permission in mine and r[0].status == "pending"})


@router.post("/approvals/{aid}/decision")
def decide(aid: str, body: DecisionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        ap = policy.decide_approval(db, user, aid, body.decision, body.reason)
    except PermissionDenied as e:
        raise HTTPException(403, str(e))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Conflict as e:
        raise HTTPException(409, str(e))
    return to_dict(ap)


# ------------------------------------------------------------------ agents / tools
@router.get("/agents")
def agents(user: User = Depends(require("email.read")), db: Session = Depends(get_db)):
    cfg = {a.name: a for a in db.execute(select(AgentConfig).where(AgentConfig.org_id == user.org_id)).scalars()}
    return [{"name": n, "description": d, "workflows": w, "enabled": cfg[n].enabled if n in cfg else True,
             "version": cfg[n].version if n in cfg else "1.0.0", "tools": AGENT_TOOLS.get(n, []), "permissions": sorted(AGENT_PERMS.get(n, [])),
             "toggleable": n not in ("policy_layer", "orchestrator")} for n, (d, w) in AGENT_INFO.items()]


@router.patch("/agents/{name}")
def patch_agent(name: str, body: AgentPatch, user: User = Depends(require("agent.manage")), db: Session = Depends(get_db)):
    if name not in AGENT_INFO or name in ("policy_layer", "orchestrator"):
        raise HTTPException(400, "This agent cannot be toggled")
    a = db.execute(select(AgentConfig).where(AgentConfig.org_id == user.org_id, AgentConfig.name == name)).scalars().first()
    a.enabled = body.enabled
    audit(db, user.org_id, "user", user.id, "agent.toggled", "agent", name, detail={"enabled": body.enabled})
    db.commit()
    return {"name": name, "enabled": a.enabled}


@router.get("/tools")
def tools(user: User = Depends(require("audit.read"))):
    return describe_tools()


# ------------------------------------------------------------------ integrations
def _acc_public(a: EmailAccount):
    d = to_dict(a)
    d["label"] = (a.meta or {}).get("label") or {"gmail": "Gmail", "outlook": "Microsoft Outlook", "demo": "Demo mailbox"}[a.provider]
    return d


@router.get("/integrations")
def integrations(user: User = Depends(require("integration.manage")), db: Session = Depends(get_db)):
    accs = db.execute(select(EmailAccount).where(EmailAccount.org_id == user.org_id).order_by(EmailAccount.created_at)).scalars().all()
    return {"accounts": [_acc_public(a) for a in accs],
            "providers": [{"name": n, "configured": p.configured(), "scopes": p.scopes} for n, p in PROVIDERS.items()],
            "adapters": [{"kind": k, "adapter": "local-test-adapter", "note": "Backed by this app's database; not an external SaaS"}
                         for k in ("support/ticketing", "crm", "calendar", "accounting")]}


@router.post("/integrations/demo")
def connect_demo(user: User = Depends(require("integration.manage")), db: Session = Depends(get_db)):
    a = seed.demo_account(db, user.org_id)
    db.commit()
    return _acc_public(a)


@router.get("/oauth/{provider}/start")
def oauth_start(provider: str, user: User = Depends(require("integration.manage"))):
    p = PROVIDERS.get(provider)
    if provider not in ("gmail", "outlook") or not p.configured():
        raise HTTPException(400, f"{provider} OAuth is not configured on the server (see docs/INTEGRATIONS.md)")
    return {"url": p.auth_url(make_state({"uid": user.id, "org": user.org_id, "p": provider}))}


@router.get("/oauth/{provider}/callback")
def oauth_callback(provider: str, code: str = "", state: str = "", error: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if error:
        return RedirectResponse(f"/#/integrations?error={error[:60]}")
    try:
        st = decode_token(state, "state")
    except jwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired OAuth state")
    if st.get("uid") != user.id or st.get("org") != user.org_id or st.get("p") != provider or "integration.manage" not in perms_for(user.role):
        raise HTTPException(400, "OAuth state does not match this session")
    p = PROVIDERS[provider]
    res = p.exchange_code(code)
    acc = db.execute(select(EmailAccount).where(EmailAccount.org_id == user.org_id, EmailAccount.provider == provider, EmailAccount.address == res["address"])).scalars().first()
    if not acc:
        acc = EmailAccount(org_id=user.org_id, provider=provider, address=res["address"])
        db.add(acc)
    acc.token_enc = encrypt(json.dumps(res["tokens"]))
    acc.scopes, acc.status, acc.last_error = res["scopes"], "connected", None
    acc.cursor = res.get("cursor") or acc.cursor
    db.flush()
    try:
        if provider == "gmail":
            p.watch(acc)
        else:
            sub = p.ensure_subscription(acc)
            if sub:
                acc.meta = {**(acc.meta or {}), "subscription_id": sub.get("id"), "subscription_expires": sub.get("expirationDateTime")}
    except Exception as e:
        acc.meta = {**(acc.meta or {}), "webhook_note": f"Webhook not enabled (polling fallback active): {str(e)[:100]}"}
    audit(db, user.org_id, "user", user.id, "integration.connected", "email_account", acc.id, detail={"provider": provider, "scopes": res["scopes"]})
    jobs.enqueue(db, user.org_id, "sync_account", {"account_id": acc.id}, dedupe_key=f"sync:{acc.id}")
    db.commit()
    return RedirectResponse(f"/#/integrations?connected={provider}")


@router.post("/integrations/{aid}/sync")
def sync_now(aid: str, user: User = Depends(require("integration.manage")), db: Session = Depends(get_db)):
    a = own(db, EmailAccount, aid, user)
    if a.provider == "demo":
        return {"queued": False, "note": "Demo mailbox has nothing to sync; use 'Add test email'"}
    jobs.enqueue(db, user.org_id, "sync_account", {"account_id": a.id}, dedupe_key=f"sync:{a.id}")
    db.commit()
    return {"queued": True}


@router.delete("/integrations/{aid}")
def disconnect(aid: str, user: User = Depends(require("integration.manage")), db: Session = Depends(get_db)):
    a = own(db, EmailAccount, aid, user)
    if a.token_enc:
        PROVIDERS[a.provider].revoke(a)
    a.status, a.token_enc = "disconnected", None
    audit(db, user.org_id, "user", user.id, "integration.disconnected", "email_account", a.id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ webhooks (provider-authenticated, replay-protected)
def _dedupe(db, org_id, key):
    if db.execute(select(IdempotencyRecord.id).where(IdempotencyRecord.key == key[:200])).first():
        return True
    db.add(IdempotencyRecord(org_id=org_id, key=key[:200], result={}))
    return False


@router.post("/webhooks/gmail")
async def gmail_hook(request: Request, token: str = "", db: Session = Depends(get_db)):
    if not settings.WEBHOOK_SECRET:
        raise HTTPException(503, "Webhooks not configured")
    if not hmac.compare_digest(token, settings.WEBHOOK_SECRET):
        raise HTTPException(401, "Bad webhook token")
    body = await request.json()
    import base64
    msg = body.get("message", {})
    data = json.loads(base64.b64decode(msg.get("data", "e30=")))
    acc = db.execute(select(EmailAccount).where(EmailAccount.provider == "gmail", EmailAccount.address == data.get("emailAddress", ""))).scalars().first()
    if not acc:
        return {"ok": True}
    if _dedupe(db, acc.org_id, f"wh:gmail:{msg.get('messageId')}"):
        return {"ok": True, "duplicate": True}
    jobs.enqueue(db, acc.org_id, "sync_account", {"account_id": acc.id}, dedupe_key=f"sync:{acc.id}")
    db.commit()
    return {"ok": True}


@router.post("/webhooks/outlook")
async def outlook_hook(request: Request, validationToken: str = "", db: Session = Depends(get_db)):
    if validationToken:
        return PlainTextResponse(validationToken)
    if not settings.WEBHOOK_SECRET:
        raise HTTPException(503, "Webhooks not configured")
    body = await request.json()
    for n in body.get("value", []):
        if not hmac.compare_digest(n.get("clientState", ""), settings.WEBHOOK_SECRET):
            continue
        acc = next((a for a in db.execute(select(EmailAccount).where(EmailAccount.provider == "outlook")).scalars()
                    if (a.meta or {}).get("subscription_id") == n.get("subscriptionId")), None)
        if acc and not _dedupe(db, acc.org_id, f"wh:outlook:{n.get('subscriptionId')}:{n.get('resourceData', {}).get('id')}"):
            jobs.enqueue(db, acc.org_id, "sync_account", {"account_id": acc.id}, dedupe_key=f"sync:{acc.id}")
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ jobs
@router.get("/jobs")
def list_jobs(status: str = "dead", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              user: User = Depends(require("job.retry")), db: Session = Depends(get_db)):
    stmt = select(Job).where(Job.org_id == user.org_id)
    if status != "all":
        stmt = stmt.where(Job.status == status)
    return page_of(db, stmt.order_by(Job.created_at.desc()), page, page_size, lambda r: to_dict(r[0]))


@router.post("/jobs/{jid}/retry")
def retry_job(jid: str, user: User = Depends(require("job.retry")), db: Session = Depends(get_db)):
    j = own(db, Job, jid, user)
    if j.status != "dead":
        raise HTTPException(409, "Only dead-lettered jobs can be retried")
    if j.error_class == "ambiguous":
        raise HTTPException(409, "Outcome was ambiguous: verify the external system manually before retrying")
    if j.kind == "deliver_outbox" and j.error_class == "permanent":
        raise HTTPException(409, "Permanent delivery failure: fix the cause and create a new reply")
    if j.kind == "process_email":
        r = db.execute(select(WorkflowRun).where(WorkflowRun.email_id == j.payload.get("email_id"), WorkflowRun.state == "failed")).scalars().first()
        if r:
            workflows.transition(db, r, "queued", "manual retry")
    j.status, j.attempts, j.run_after = "queued", 0, utcnow()
    audit(db, user.org_id, "user", user.id, "job.manual_retry", "job", j.id, detail={"kind": j.kind})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ audit
@router.get("/audit")
def audit_logs(actor: str = "", action: str = "", resource_type: str = "", run_id: str = "", since: str = "", until: str = "",
               page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=200), user: User = Depends(require("audit.read")),
               db: Session = Depends(get_db)):
    from .models import AuditLog
    stmt = select(AuditLog).where(AuditLog.org_id == user.org_id)
    if actor:
        stmt = stmt.where(AuditLog.actor_id.ilike(f"%{actor}%"))
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if run_id:
        stmt = stmt.where(AuditLog.run_id == run_id)
    try:
        if since:
            stmt = stmt.where(AuditLog.ts >= datetime.fromisoformat(since))
        if until:
            stmt = stmt.where(AuditLog.ts <= datetime.fromisoformat(until))
    except ValueError:
        raise HTTPException(422, "since/until must be ISO datetimes")
    return page_of(db, stmt.order_by(AuditLog.id.desc()), page, page_size, lambda r: to_dict(r[0], exclude=("prev_hash",)))


@router.get("/audit/verify")
def audit_verify(user: User = Depends(require("audit.read")), db: Session = Depends(get_db)):
    return verify_chain(db, user.org_id)


# ------------------------------------------------------------------ users & settings
def _ser_user(u):
    return to_dict(u, exclude=("failed_logins",))


@router.get("/users")
def users(user: User = Depends(require("user.manage")), db: Session = Depends(get_db)):
    return {"items": [_ser_user(u) for u in db.execute(select(User).where(User.org_id == user.org_id).order_by(User.created_at)).scalars()],
            "roles": {r: sorted(perms_for(r)) for r in ROLES if r != "platform_admin"}, "all_permissions": sorted(ALL_PERMS)}


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(require("user.manage")), db: Session = Depends(get_db)):
    if body.role not in ROLES or body.role == "platform_admin":
        raise HTTPException(422, "Invalid role")
    check_password_strength(body.password)
    if db.execute(select(User.id).where(User.email == body.email.lower())).first():
        raise HTTPException(409, "Email already registered")
    u = User(org_id=user.org_id, email=body.email.lower(), name=body.name, role=body.role, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    audit(db, user.org_id, "user", user.id, "user.created", "user", u.id, detail={"role": u.role})
    db.commit()
    return _ser_user(u)


@router.patch("/users/{uid_}")
def patch_user(uid_: str, body: UserPatch, user: User = Depends(require("user.manage")), db: Session = Depends(get_db)):
    u = own(db, User, uid_, user)
    if body.role is not None:
        if body.role not in ROLES or body.role == "platform_admin":
            raise HTTPException(422, "Invalid role")
        if u.id == user.id and body.role != u.role:
            raise HTTPException(409, "You cannot change your own role")
        u.role = body.role
    if body.is_active is not None:
        if u.id == user.id and not body.is_active:
            raise HTTPException(409, "You cannot deactivate yourself")
        u.is_active = body.is_active
    if body.name:
        u.name = body.name
    if body.password:
        check_password_strength(body.password)
        u.password_hash = hash_password(body.password)
    audit(db, user.org_id, "user", user.id, "user.updated", "user", u.id, detail={"role": body.role, "active": body.is_active, "password_changed": bool(body.password)})
    db.commit()
    return _ser_user(u)


@router.get("/settings")
def get_settings(user: User = Depends(require("email.read")), db: Session = Depends(get_db)):
    o = org_of(db, user)
    return {"org_name": o.name, "settings": policy.org_settings(o), "can_edit": "policy.manage" in perms_for(user.role),
            "engine": engine_name(), "llm_configured": llm_enabled()}


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(require("policy.manage")), db: Session = Depends(get_db)):
    from zoneinfo import ZoneInfo
    o = org_of(db, user)
    ch = body.model_dump(exclude_none=True)
    if "org_name" in ch:
        o.name = ch.pop("org_name")
    if "timezone" in ch:
        try:
            ZoneInfo(ch["timezone"])
        except Exception:
            raise HTTPException(422, "Unknown timezone")
    if "working_hours" in ch:
        w = ch["working_hours"]
        if not (isinstance(w.get("start_hour"), int) and isinstance(w.get("end_hour"), int) and 0 <= w["start_hour"] < w["end_hour"] <= 24
                and isinstance(w.get("days"), list) and all(isinstance(d, int) and 0 <= d <= 6 for d in w["days"])):
            raise HTTPException(422, "working_hours must have start_hour<end_hour and days 0-6")
    for k in ("approval_required", "sla_hours"):
        if k in ch:
            allowed = set(policy.DEFAULT_SETTINGS[k])
            if set(ch[k]) - allowed:
                raise HTTPException(422, f"Unknown keys in {k}: {sorted(set(ch[k]) - allowed)}")
    cur = dict(o.settings or {})
    o.settings = policy._merge(cur, ch)
    audit(db, user.org_id, "user", user.id, "settings.updated", "organization", o.id, detail=ch)
    db.commit()
    return {"settings": policy.org_settings(o)}


# ------------------------------------------------------------------ business records
RECORDS = {"tickets": (Ticket, "ticket.read"), "crm": (CrmRecord, "crm.read"), "invoices": (Invoice, "invoice.read"),
           "events": (CalendarEvent, "calendar.read"), "customers": (Customer, "customer.read")}


@router.get("/records/{kind}")
def records(kind: str, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if kind not in RECORDS:
        raise HTTPException(404, "Unknown record type")
    model, perm = RECORDS[kind]
    if perm not in perms_for(user.role):
        raise HTTPException(403, f"Missing permission: {perm}")
    stmt = select(model).where(model.org_id == user.org_id).order_by(model.created_at.desc())
    return page_of(db, stmt, page, page_size, lambda r: to_dict(r[0], exclude=("source_refs",) if kind == "invoices" else ()))


# ------------------------------------------------------------------ notifications / demo / evaluation
@router.get("/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Notification).where(Notification.org_id == user.org_id).order_by(Notification.created_at.desc()).limit(50)).scalars().all()
    wide = user.role in ("org_admin", "platform_admin", "manager")
    rows = [n for n in rows if wide or not n.target_role or n.target_role == user.role]
    return {"items": [to_dict(n) for n in rows], "unread": sum(1 for n in rows if not n.read)}


@router.post("/notifications/read")
def read_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    for n in db.execute(select(Notification).where(Notification.org_id == user.org_id, Notification.read.is_(False))).scalars():
        n.read = True
    db.commit()
    return {"ok": True}


@router.post("/demo/load")
def load_demo(user: User = Depends(require("user.manage")), db: Session = Depends(get_db)):
    if not settings.ENABLE_DEMO:
        raise HTTPException(403, "Demo data is disabled")
    return seed.load_demo(db, org_of(db, user))


@router.get("/evaluations")
def evaluations(user: User = Depends(require("audit.read")), db: Session = Depends(get_db)):
    rows = db.execute(select(EvaluationRun).where(EvaluationRun.org_id == user.org_id).order_by(EvaluationRun.created_at.desc()).limit(10)).scalars().all()
    return {"items": [to_dict(r) for r in rows]}


@router.post("/evaluations/run")
def run_evaluation(user: User = Depends(require("policy.manage")), db: Session = Depends(get_db)):
    from evaluation.run_eval import evaluate
    m = evaluate()
    r = EvaluationRun(org_id=user.org_id, engine=m["engine"], prompt_version=m["prompt_version"], metrics=m)
    db.add(r)
    audit(db, user.org_id, "user", user.id, "evaluation.run", "evaluation", r.id, detail={"accuracy": m["accuracy"], "n": m["n"]})
    db.commit()
    return to_dict(r)
