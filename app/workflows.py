"""Durable workflow engine: explicit state machine, LangGraph orchestration, three business workflows,
approval execution, outbox delivery and SLA escalation. All side effects go through the tool registry."""
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import TypedDict

from sqlalchemy import select

from . import intelligence as intel
from . import jobs, policy, scheduling
from .audit import audit, redact
from .errors import (AmbiguousOutcome, BudgetExceeded, InvalidTransition, NeedsReview, PermanentError, PermissionDenied,
                     RetryableError)
from .integrations import notify
from .models import (AgentConfig, Approval, Attachment, Classification, Email, EmailAccount, Organization, Outbox, Ticket,
                     User, WorkflowRun, WorkflowStep, utcnow)
from .providers import PROVIDERS
from .tools import execute_tool as call

TERMINAL = {"completed", "rejected", "failed", "cancelled"}
ACTIVE = {"received", "classified", "queued", "processing", "retry_scheduled"}
TRANSITIONS = {
    "received": {"classified", "needs_review", "failed", "cancelled"},
    "classified": {"queued", "needs_review", "failed", "cancelled"},
    "queued": {"processing", "cancelled", "failed"},
    "processing": {"awaiting_approval", "awaiting_response", "needs_review", "completed", "failed", "retry_scheduled", "cancelled"},
    "awaiting_approval": {"approved", "rejected", "cancelled", "needs_review"},
    "approved": {"executing", "failed", "cancelled", "needs_review"},
    "executing": {"completed", "awaiting_response", "failed", "retry_scheduled", "needs_review"},
    "awaiting_response": {"processing", "completed", "cancelled", "needs_review"},
    "retry_scheduled": {"processing", "failed", "cancelled"},
    "needs_review": {"cancelled"},
    "failed": {"queued"},
    "rejected": set(), "completed": set(), "cancelled": set(),
}


def transition(db, run: WorkflowRun, new: str, note: str = None):
    if new == run.state:
        return
    if new not in TRANSITIONS.get(run.state, set()):
        raise InvalidTransition(f"Invalid workflow transition {run.state} -> {new}")
    old, run.state = run.state, new
    if new in TERMINAL:
        run.finished_at = utcnow()
    em = db.get(Email, run.email_id)
    if em:
        em.status = new
    audit(db, run.org_id, "system", "orchestrator", "workflow.transition", "workflow_run", run.id,
          detail={"from": old, "to": new, "note": note}, run_id=run.id, correlation_id=run.correlation_id, email_id=run.email_id)
    db.commit()


def _ctxset(run, **kw):
    run.context = {**(run.context or {}), **kw}


class _Box:
    def __init__(self):
        self.output = {}


class RunCtx:
    def __init__(self, db, org, run, agent, email=None, user=None, cls=None):
        self.db, self.org, self.run, self.agent, self.email, self.user, self.cls = db, org, run, agent, email, user, cls
        self.settings = policy.org_settings(org)
        self.t0 = time.monotonic()

    def as_agent(self, name):
        c = RunCtx(self.db, self.org, self.run, name, self.email, self.user, self.cls)
        c.t0 = self.t0
        return c

    def _lim(self):
        return self.settings["workflow_limits"]

    def _check_time(self):
        if time.monotonic() - self.t0 > self._lim()["max_seconds"]:
            raise BudgetExceeded("Workflow exceeded max execution time")

    def count_tool(self):
        if not self.run:
            return
        self._check_time()
        self.run.tool_calls += 1
        if self.run.tool_calls > self._lim()["max_tool_calls"]:
            raise BudgetExceeded("Workflow exceeded max tool invocations")

    def count_model(self):
        if self.run:
            self.run.model_calls += 1
            if self.run.model_calls > self._lim()["max_model_calls"]:
                raise BudgetExceeded("Workflow exceeded max model calls")

    def add_usage(self, usage):
        if self.run and usage:
            self.run.tokens_in += int(usage.get("in", 0) or 0)
            self.run.tokens_out += int(usage.get("out", 0) or 0)

    def _row(self, kind, name, inp):
        self.run.step_count += 1
        if self.run.step_count > self._lim()["max_steps"]:
            raise BudgetExceeded("Workflow exceeded max steps")
        st = WorkflowStep(org_id=self.org.id, run_id=self.run.id, seq=self.run.step_count, agent=self.agent, name=name,
                          kind=kind, input=redact(inp or {}))
        self.db.add(st)
        self.db.flush()
        return st

    def log_tool(self, name, args, status, out, err=None):
        if not self.run:
            return
        st = self._row("tool", name, args)
        st.status, st.output, st.error, st.finished_at = status, redact(out or {}), (err or None) and str(err)[:500], utcnow()

    @contextmanager
    def step(self, name, input=None):
        self._check_time()
        st = self._row("step", name, input)
        self.db.commit()
        box, sid = _Box(), st.id
        try:
            yield box
        except NeedsReview:
            st.status, st.finished_at, st.output = "review", utcnow(), redact(box.output)
            self.db.commit()
            raise
        except Exception as e:
            self.db.rollback()
            st = self.db.get(WorkflowStep, sid)
            st.status, st.error, st.finished_at = "error", f"{type(e).__name__}: {e}"[:500], utcnow()
            self.db.commit()
            raise
        st.status, st.output, st.finished_at = "ok", redact(box.output), utcnow()
        self.db.commit()


def agent_enabled(db, org_id, name):
    a = db.execute(select(AgentConfig).where(AgentConfig.org_id == org_id, AgentConfig.name == name)).scalars().first()
    return a is None or a.enabled


def _atts(db, email_id):
    return db.execute(select(Attachment).where(Attachment.email_id == email_id)).scalars().all()


def classify_and_store(db, em: Email, org: Organization):
    s = policy.org_settings(org)
    atts = _atts(db, em.id)
    res, ents, engine, usage, flags = intel.classify_email(em.subject, em.body_text, [(a.filename, a.text_content) for a in atts],
                                                           s["confidence_threshold"])
    cls = db.execute(select(Classification).where(Classification.email_id == em.id)).scalars().first()
    if not cls:
        cls = Classification(org_id=em.org_id, email_id=em.id)
        db.add(cls)
    cls.category, cls.intent, cls.urgency, cls.sentiment = res.category, res.intent, res.urgency, res.sentiment
    cls.language, cls.confidence, cls.entities = res.language, res.confidence, ents
    cls.requested_action, cls.missing_info, cls.recommended_workflow = res.requested_action, res.missing_info, res.recommended_workflow
    cls.evidence, cls.engine, cls.prompt_version, cls.usage = res.evidence, engine, intel.PROMPT_VERSION, usage
    cls.priority = intel.compute_priority(res.urgency, res.sentiment, res.category)
    em.injection_flags = flags or em.injection_flags
    audit(db, em.org_id, "agent", "email_intelligence", "email.classified", "email", em.id, email_id=em.id, correlation_id=em.correlation_id,
          detail={"category": res.category, "confidence": res.confidence, "workflow": res.recommended_workflow, "engine": engine})
    db.commit()
    return cls


# ================================================================== agents
def agent_complaint(c0: RunCtx):
    ctx = c0.as_agent("support_agent")
    em, cls = ctx.email, ctx.cls
    ents = cls.entities or {}
    customer, order, note = None, None, None
    with ctx.step("identify_customer") as st:
        r = call(ctx, "search_customers", {"email": em.sender})
        customer = r["items"][0] if r["items"] else None
        st.output = {"customer_found": bool(customer), "tier": customer and customer["tier"]}
    with ctx.step("verify_order", {"candidates": ents.get("order_refs", [])}) as st:
        for ref in ents.get("order_refs", [])[:3]:
            o = call(ctx, "get_order", {"order_ref": ref})["order"]
            if o and customer and o["customer_id"] == customer["id"]:
                order = o
                break
            if o:
                note = "Order exists but is not linked to the sender; details withheld"
        st.output = {"verified_order": order and order["order_ref"], "note": note}
    kb = call(ctx, "search_knowledge_base", {"query": f"{em.subject} {em.body_text[:300]}"})["items"]
    prio = intel.compute_priority(cls.urgency, cls.sentiment, cls.category, customer["tier"] if customer else "standard",
                                  order["status"] if order else "")
    team = "Support Tier 2" if prio in ("P1", "P2") else "Support Tier 1"
    with ctx.step("create_ticket", {"priority": prio, "team": team}) as st:
        t = call(ctx, "create_support_ticket", {"email_id": em.id, "subject": em.subject, "category": cls.category, "priority": prio,
                                                "team": team, "customer_id": customer["id"] if customer else None,
                                                "summary": cls.intent + ("; refs: " + ", ".join(ents.get("ticket_refs", [])) if ents.get("ticket_refs") else "")})["ticket"]
        st.output = t
    if prio == "P1":
        call(ctx, "notify_responsible_user", {"title": f"P1 ticket {t['ref']}", "body": em.subject, "level": "critical",
                                              "role": "manager", "dedupe_key": f"p1:{t['id']}"})
    facts = {"customer_name": customer["name"] if customer else em.sender_name, "order": order, "ticket_ref": t["ref"], "team": team,
             "kb": [k["content"] for k in kb]}
    with ctx.step("draft_reply") as st:
        d = call(ctx, "draft_email_reply", {"email_id": em.id, "kind": "complaint", "facts": facts})
        ap = call(ctx, "request_human_approval", {
            "action_type": "send_email", "reason": "Reply to customer complaint" + (f" (draft flags: {d['flags']})" if d["flags"] else ""),
            "payload": {k: d[k] for k in ("to", "subject", "body", "account_id", "thread_id", "in_reply_to")}})
        st.output = {"approval_id": ap["approval_id"], "draft_engine": d["engine"], "flags": d["flags"]}
    _ctxset(ctx.run, ticket_id=t["id"], ticket_ref=t["ref"])
    return {"state": "awaiting_approval", "summary": f"Ticket {t['ref']} ({prio}) created; reply drafted and awaiting approval."}


def _lead_fields(text, sender_name):
    f = {}
    m = re.search(r"company\s*[:\-]\s*(.+)", text, re.I) or re.search(
        r"\b(?:from|at|of)\s+((?:[A-Z][A-Za-z0-9&.-]+)(?:\s+[A-Z][A-Za-z0-9&.-]+){0,3})", text)
    if m and m.group(1).strip().lower() not in ("monday", "tuesday", "the", "our", "your"):
        f["company"] = m.group(1).strip().rstrip(".,")[:80]
    m = re.search(r"budget[^.\n]{0,40}?((?:USD|\$|€|£)\s?\d[\d,]*(?:\.\d+)?\s?[kKmM]?)", text, re.I)
    if m:
        f["budget"] = m.group(1)
    m = re.search(r"(?:timeline|launch|go[- ]live|rollout|deploy|start)[^.\n]{0,40}?((?:within|in|by|before)\s+(?:the next\s+)?(?:\d+\s+(?:days?|weeks?|months?)|Q[1-4]|end of \w+))", text, re.I)
    if m:
        f["timeline"] = m.group(1)
    m = re.search(r"interested in ([^.\n]{3,80})", text, re.I)
    if m:
        f["product_interest"] = m.group(1).strip()
    m = re.search(r"(\d{2,6})\s+(?:seats|users|licenses|employees)", text, re.I)
    if m:
        f["seats"] = m.group(1)
    return f


def agent_sales(c0: RunCtx):
    ctx = c0.as_agent("sales_agent")
    em = ctx.email
    text = f"{em.subject}\n{em.body_text}"
    lead = _lead_fields(text, em.sender_name)
    pending = False
    with ctx.step("crm_upsert", {"extracted": lead}) as st:
        found = call(ctx, "search_crm_contacts", {"email": em.sender})["items"]
        data = {"lead": lead, "marketing_consent": False, "source_email": em.id}
        if found:
            rec = found[0]
            r = call(ctx, "update_crm_contact", {"record_id": rec["id"], "data": data})
            if lead.get("company") and rec.get("company") and rec["company"].lower() != lead["company"].lower():
                r2 = call(ctx, "update_crm_contact", {"record_id": rec["id"], "fields": {"company": lead["company"]}})
                pending = r2.get("status") == "pending_approval"
            rid, existed = rec["id"], True
        else:
            rid = call(ctx, "create_crm_contact", {"name": em.sender_name, "email": em.sender, "company": lead.get("company", ""),
                                                   "email_id": em.id, "data": data})["record"]["id"]
            existed = False
        st.output = {"record_id": rid, "existing_contact": existed, "sensitive_update_pending_approval": pending}
    call(ctx, "create_follow_up_task", {"title": f"Follow up with {em.sender_name or em.sender}", "due_days": 2, "email_id": em.id, "related_email": em.sender})
    qs = [q for q, k in (("your target timeline", "timeline"), ("the expected number of users", "seats"), ("your company name", "company")) if k not in lead]
    with ctx.step("draft_reply") as st:
        d = call(ctx, "draft_email_reply", {"email_id": em.id, "kind": "sales", "facts": {"product": lead.get("product_interest", "our product"), "questions": qs or ["any specific requirements"]}})
        ap = call(ctx, "request_human_approval", {"action_type": "send_email", "reason": "Reply to sales inquiry",
                                                  "payload": {k: d[k] for k in ("to", "subject", "body", "account_id", "thread_id", "in_reply_to")}})
        st.output = {"approval_id": ap["approval_id"]}
    return {"state": "awaiting_approval", "summary": "Lead recorded in CRM, follow-up task created, reply awaiting approval."}


def _validate_invoice(f: dict, s: dict) -> list:
    issues = []
    for k in ("vendor", "invoice_number", "invoice_date", "currency", "total_cents"):
        if not f.get(k):
            issues.append({"code": f"missing_{k}", "severity": "block", "msg": f"Required field '{k}' is missing or unreadable"})
    if f.get("currency") and f["currency"] not in ("USD", "EUR", "GBP", "PKR", "AED", "CAD", "AUD"):
        issues.append({"code": "currency_unsupported", "severity": "block", "msg": f"Unsupported currency {f['currency']}"})
    if f.get("total_cents") is not None and f["total_cents"] <= 0:
        issues.append({"code": "non_positive_total", "severity": "block", "msg": "Total must be positive"})
    if None not in (f.get("subtotal_cents"), f.get("tax_cents"), f.get("total_cents")) and f["subtotal_cents"] + f["tax_cents"] != f["total_cents"]:
        issues.append({"code": "arithmetic_mismatch", "severity": "block",
                       "msg": f"subtotal + tax ({(f['subtotal_cents'] + f['tax_cents']) / 100:.2f}) != total ({f['total_cents'] / 100:.2f})"})
    if f.get("invoice_date") and f.get("due_date") and f["due_date"] < f["invoice_date"]:
        issues.append({"code": "due_before_invoice", "severity": "block", "msg": "Due date is before invoice date"})
    return issues


def agent_invoice(c0: RunCtx):
    ctx = c0.as_agent("finance_agent")
    em = ctx.email
    atts = [a for a in _atts(ctx.db, em.id) if a.safety_status == "ok"]
    if not atts:
        raise NeedsReview("No supported (safe) invoice attachment found")
    att = atts[0]
    with ctx.step("extract_fields", {"attachment": att.filename}) as st:
        ex = call(ctx, "extract_invoice_fields", {"attachment_id": att.id})
        if ex.get("error"):
            st.output = {"error": ex["error"]}
            raise NeedsReview(ex["error"])
        f = ex["fields"]
        st.output = {"fields": f, "source_refs": ex["source_refs"], "engine": ex["engine"]}
    issues = _validate_invoice(f, ctx.settings)
    if ex.get("bank_change_language"):
        issues.append({"code": "vendor_bank_change", "severity": "block",
                       "msg": "Document mentions changed bank details: restricted; requires independent verification by phone with a known contact"})
    with ctx.step("checks") as st:
        dup = call(ctx, "find_duplicate_invoice", {"vendor": f.get("vendor", ""), "invoice_number": f.get("invoice_number", ""),
                                                   "total_cents": f.get("total_cents"), "invoice_date": f.get("invoice_date"),
                                                   "currency": f.get("currency", "")})
        if dup["duplicates"]:
            issues.append({"code": "duplicate_invoice", "severity": "block", "msg": f"Likely duplicate of {len(dup['duplicates'])} existing invoice(s)"})
        hist = dup["vendor_history"]
        if len(hist) >= 3 and f.get("total_cents") and f["total_cents"] > ctx.settings["unusual_amount_multiplier"] * (sum(hist) / len(hist)):
            issues.append({"code": "unusual_amount", "severity": "block", "msg": "Amount is unusually high versus this vendor's history"})
        if f.get("po_number"):
            po = call(ctx, "get_purchase_order", {"po_number": f["po_number"]})["po"]
            if not po:
                issues.append({"code": "po_not_found", "severity": "block", "msg": f"Purchase order {f['po_number']} not found"})
            else:
                tol = ctx.settings["po_total_tolerance_pct"] / 100
                if intel.vendor_norm(po["vendor"]) != intel.vendor_norm(f.get("vendor", "")):
                    issues.append({"code": "po_vendor_mismatch", "severity": "block", "msg": "Vendor differs from purchase order"})
                if po["currency"] != f.get("currency"):
                    issues.append({"code": "po_currency_mismatch", "severity": "block", "msg": "Currency differs from purchase order"})
                if f.get("total_cents") is not None and abs(f["total_cents"] - po["total_cents"]) > po["total_cents"] * tol:
                    issues.append({"code": "po_total_mismatch", "severity": "block",
                                   "msg": f"Invoice total {f['total_cents'] / 100:.2f} differs from PO total {po['total_cents'] / 100:.2f}"})
        else:
            issues.append({"code": "no_po_reference", "severity": "warn", "msg": "No purchase order referenced"})
        st.output = {"issues": issues}
    blocking = [i for i in issues if i["severity"] == "block"]
    val = {"issues": issues, "checked_at": utcnow().isoformat()}
    save = {"attachment_id": att.id, "email_id": em.id, "fields": f, "source_refs": ex["source_refs"], "validation": val}
    if blocking:
        r = call(ctx, "save_invoice_validation", {**save, "status": "needs_review"})
        call(ctx, "notify_responsible_user", {"title": "Invoice needs manual review", "body": "; ".join(i["msg"] for i in blocking)[:500],
                                              "level": "warning", "role": "finance", "dedupe_key": f"inv-review:{ctx.run.id}"})
        _ctxset(ctx.run, invoice_id=r["invoice_id"])
        raise NeedsReview("Invoice routed to manual review: " + "; ".join(i["msg"] for i in blocking))
    r = call(ctx, "save_invoice_validation", {**save, "status": "validated"})
    _ctxset(ctx.run, invoice_id=r["invoice_id"])
    with ctx.step("accounting_draft") as st:
        d = call(ctx, "create_accounting_draft", {"invoice_id": r["invoice_id"]})
        st.output = d
    if d.get("status") == "pending_approval":
        call(ctx, "save_invoice_validation", {**save, "status": "draft_pending_approval"})
        return {"state": "awaiting_approval", "summary": "Invoice validated; accounting draft awaits finance approval (above threshold)."}
    return {"state": "completed", "summary": f"Invoice validated; accounting DRAFT {d['accounting_ref']} created (not paid, not posted)."}


def _fmt_all(slots, tz):
    return [scheduling.fmt_slot(s, e, tz) for s, e in slots]


def agent_meeting(c0: RunCtx):
    ctx = c0.as_agent("meeting_agent")
    em, s = ctx.email, ctx.settings
    with ctx.step("extract_requirements") as st:
        p = scheduling.parse_meeting_request(f"{em.subject}\n{em.body_text}", em.received_at, s["timezone"])
        st.output = {"windows": [{"start": w["start"].isoformat(), "end": w["end"].isoformat()} for w in p["windows"]],
                     "duration_min": p["duration_min"], "tz": p["tz"], "tz_detected": p["tz_detected"]}
    dur = p["duration_min"]
    show_tz = p["tz"] if p["tz_detected"] else s["timezone"]
    good = []
    with ctx.step("check_availability") as st:
        for w in p["windows"][:3]:
            if w["start"] <= em.received_at:
                continue
            r = call(ctx, "check_calendar_availability", {"start": w["start"], "end": w["end"]})
            if r["available"]:
                good.append((w["start"], w["end"]))
        st.output = {"requested_available": len(good), "requested": len(p["windows"])}
    slots = list(good)
    if len(slots) < 3:
        fs = call(ctx, "find_free_slots", {"duration_min": dur, "count": 3 - len(slots), "from_time": em.received_at})["slots"]
        have = {a for a, _ in slots}
        for x in fs:
            a, b = datetime.fromisoformat(x["start"]), datetime.fromisoformat(x["end"])
            if a not in have:
                slots.append((a, b))
    if not slots:
        raise NeedsReview("No free slots found inside working hours")
    unavailable = None if not p["windows"] else (len(good) == 0)
    with ctx.step("draft_proposal") as st:
        d = call(ctx, "draft_email_reply", {"email_id": em.id, "kind": "meeting", "facts": {
            "slots": _fmt_all(slots, show_tz), "requested_unavailable": unavailable}})
        ap = call(ctx, "request_human_approval", {"action_type": "send_email", "reason": "Meeting proposal reply",
                                                  "payload": {k: d[k] for k in ("to", "subject", "body", "account_id", "thread_id", "in_reply_to")}})
        st.output = {"approval_id": ap["approval_id"], "slots": _fmt_all(slots, show_tz)}
    _ctxset(ctx.run, proposed_slots=[{"start": a.isoformat(), "end": b.isoformat()} for a, b in slots], duration_min=dur, tz=show_tz,
            await_confirmation=True, meeting_title=re.sub(r"^(re|fwd?):\s*", "", em.subject, flags=re.I)[:200])
    return {"state": "awaiting_approval", "summary": f"{len(slots)} verified slots proposed; reply awaiting approval."}


def agent_review(c0: RunCtx):
    raise NeedsReview(c0.cls.evidence or "Routed to manual review")


AGENTS = {"complaint": agent_complaint, "sales_lead": agent_sales, "invoice": agent_invoice, "meeting": agent_meeting,
          "manual_review": agent_review}
AGENT_FOR = {"complaint": "support_agent", "sales_lead": "sales_agent", "invoice": "finance_agent", "meeting": "meeting_agent"}


class GState(TypedDict, total=False):
    workflow: str
    outcome: dict


def run_graph(ctx: RunCtx, workflow: str) -> dict:
    """LangGraph: orchestrator node routes to exactly one agent node (explicit allow-list)."""
    def route(state):
        with ctx.as_agent("orchestrator").step("route", {"workflow": workflow, "category": ctx.cls.category,
                                                          "confidence": ctx.cls.confidence}) as st:
            st.output = {"selected": workflow}
        return {"workflow": workflow}
    try:
        from langgraph.graph import END, StateGraph
    except Exception:  # pragma: no cover - deterministic fallback if langgraph is unavailable
        route({})
        return AGENTS[workflow](ctx)
    g = StateGraph(GState)
    g.add_node("orchestrate", route)
    for name, fn in AGENTS.items():
        g.add_node(f"agent_{name}", (lambda state, fn=fn: {"outcome": fn(ctx)}))
        g.add_edge(f"agent_{name}", END)
    g.set_entry_point("orchestrate")
    g.add_conditional_edges("orchestrate", lambda s: f"agent_{s['workflow']}", {f"agent_{k}": f"agent_{k}" for k in AGENTS})
    return g.compile().invoke({"workflow": workflow})["outcome"]


# ================================================================== orchestrator entry point
def _notify_review(db, run, reason):
    notify(db, run.org_id, "Workflow needs manual review", reason[:500], "warning", f"review:{run.id}", "manager")


def orchestrate(db, email_id: str, forced: str = None, user_id: str = None):
    em = db.get(Email, email_id)
    if not em:
        return
    org = db.get(Organization, em.org_id)
    user = db.get(User, user_id) if user_id else None
    run = db.execute(select(WorkflowRun).where(WorkflowRun.email_id == em.id, WorkflowRun.state.in_(ACTIVE))
                     .order_by(WorkflowRun.created_at.desc())).scalars().first()
    if not run:
        if not forced and db.execute(select(WorkflowRun.id).where(WorkflowRun.email_id == em.id)).first():
            return  # already handled; idempotent no-op
        run = WorkflowRun(org_id=em.org_id, email_id=em.id, state="received", correlation_id=em.correlation_id, created_by=user_id,
                          context={})
        db.add(run)
        db.flush()
        audit(db, em.org_id, "user" if user_id else "system", user_id or "ingest", "workflow.created", "workflow_run", run.id,
              run_id=run.id, correlation_id=em.correlation_id, email_id=em.id, detail={"forced": forced})
        db.commit()
    ctx = RunCtx(db, org, run, "orchestrator", em, user)
    try:
        cls = db.execute(select(Classification).where(Classification.email_id == em.id)).scalars().first()
        if not cls or forced == "__reclassify__":
            cls = classify_and_store(db, em, org)
            forced = None if forced == "__reclassify__" else forced
        ctx.cls = cls
        if run.state == "received":
            transition(db, run, "classified")
        if run.state == "classified":
            transition(db, run, "queued")
        if run.state in ("queued", "retry_scheduled"):
            transition(db, run, "processing")
        wf = forced or cls.recommended_workflow
        run.workflow = wf
        db.commit()
        if wf == "none":
            transition(db, run, "completed", "No action required")
            run.summary = "No action required."
            em.status = "ignored"
            db.commit()
            return
        if em.injection_flags and not forced:
            raise NeedsReview("Prompt-injection / instruction-hijack content detected; automation disabled for this email")
        if wf not in AGENTS:
            raise NeedsReview(f"Unknown workflow '{wf}'")
        if wf in AGENT_FOR and not agent_enabled(db, org.id, AGENT_FOR[wf]):
            raise NeedsReview(f"Agent '{AGENT_FOR[wf]}' is disabled")
        outcome = run_graph(ctx, wf)
        pending = db.execute(select(Approval.id).where(Approval.run_id == run.id, Approval.status == "pending")).first()
        run.summary = outcome["summary"]
        if pending:
            transition(db, run, "awaiting_approval")
        elif outcome["state"] == "completed":
            transition(db, run, "completed")
        else:
            approved_waiting = db.execute(select(Approval.id).where(Approval.run_id == run.id, Approval.status == "approved")).first()
            if approved_waiting:
                transition(db, run, "awaiting_approval")
                transition(db, run, "approved")
            else:
                transition(db, run, "completed")
        db.commit()
    except NeedsReview as e:
        db.rollback()
        run = db.get(WorkflowRun, run.id)
        run.summary, run.error = str(e)[:500], None
        if run.state in ("received", "classified"):
            transition(db, run, "needs_review", str(e))
        else:
            if run.state == "queued":
                transition(db, run, "processing")
            transition(db, run, "needs_review", str(e))
        _notify_review(db, run, str(e))
        db.commit()
    except (BudgetExceeded, PermissionDenied) as e:
        db.rollback()
        run = db.get(WorkflowRun, run.id)
        run.summary = f"Stopped by safety control: {e}"[:500]
        if run.state == "queued":
            transition(db, run, "processing")
        transition(db, run, "needs_review", str(e))
        _notify_review(db, run, str(e))
        db.commit()
    except RetryableError:
        db.rollback()
        run = db.get(WorkflowRun, run.id)
        if run.state == "processing":
            transition(db, run, "retry_scheduled")
        raise
    except Exception as e:
        db.rollback()
        run = db.get(WorkflowRun, run.id)
        run.error = f"{type(e).__name__}: {e}"[:500]
        if run.state in TRANSITIONS and "failed" in TRANSITIONS[run.state]:
            transition(db, run, "failed", run.error)
        db.commit()
        raise PermanentError(run.error)


# ================================================================== approvals -> execution
def on_approval_decided(db, run: WorkflowRun, ap: Approval):
    if ap.status == "rejected":
        for o in db.execute(select(Approval).where(Approval.run_id == run.id, Approval.status == "pending")).scalars():
            o.status, o.decision_reason = "cancelled", "Another action in this workflow was rejected"
        if run.state == "awaiting_approval":
            run.summary = f"Rejected: {ap.action_type}"
            transition(db, run, "rejected", ap.decision_reason)
    elif ap.status == "approved":
        pend = db.execute(select(Approval.id).where(Approval.run_id == run.id, Approval.status == "pending")).first()
        if not pend and run.state == "awaiting_approval":
            transition(db, run, "approved")


def _after_action(db, org, run: WorkflowRun):
    if not run:
        return
    open_ap = db.execute(select(Approval.id).where(Approval.run_id == run.id, Approval.status.in_(["pending", "approved"]))).first()
    if open_ap:
        return
    ctx_ = run.context or {}
    if ctx_.get("await_confirmation") and not ctx_.get("event_id"):
        if run.state == "executing":
            run.summary = "Proposal sent; waiting for the recipient's confirmation."
            transition(db, run, "awaiting_response")
    elif run.state in ("executing", "approved"):
        if run.state == "approved":
            transition(db, run, "executing")
        run.summary = (run.summary or "") + " Executed and verified."
        transition(db, run, "completed")


def execute_approval(db, approval_id: str):
    ap = db.get(Approval, approval_id)
    if not ap or ap.status != "approved":
        return
    org = db.get(Organization, ap.org_id)
    run = db.get(WorkflowRun, ap.run_id) if ap.run_id else None
    em = db.get(Email, ap.email_id) if ap.email_id else None
    ctx = RunCtx(db, org, run, "executor", em)
    if run and run.state == "approved":
        transition(db, run, "executing")
    try:
        if ap.action_type == "send_email":
            call(ctx, "send_approved_email", {"approval_id": ap.id})
            db.commit()
            return
        if ap.action_type == "create_calendar_event":
            out = call(ctx, "create_calendar_event", {**ap.payload, "approval_id": ap.id})
            if run:
                _ctxset(run, event_id=out["event_id"])
        elif ap.action_type == "create_accounting_draft":
            out = call(ctx, "create_accounting_draft", {**ap.payload, "approval_id": ap.id})
        elif ap.action_type == "update_crm_sensitive":
            out = call(ctx, "update_crm_contact", {**ap.payload, "approval_id": ap.id})
        else:
            raise PermanentError("Unknown approval action")
        ap.status, ap.result = "executed", out
        audit(db, ap.org_id, "system", "executor", "approval.executed", "approval", ap.id, run_id=ap.run_id,
              detail={"action": ap.action_type, "verified": True}, correlation_id=run.correlation_id if run else None)
        db.commit()
        _after_action(db, org, run)
    except (PermanentError, AmbiguousOutcome, PermissionDenied) as e:
        db.rollback()
        ap = db.get(Approval, approval_id)
        ap.status, ap.result = "failed", {"error": str(e)[:300], "ambiguous": isinstance(e, AmbiguousOutcome)}
        run = db.get(WorkflowRun, ap.run_id) if ap.run_id else None
        if run and run.state in ("executing", "approved"):
            if run.state == "approved":
                transition(db, run, "executing")
            run.summary = f"Approved action could not be executed: {e}"[:400]
            transition(db, run, "needs_review", str(e))
            _notify_review(db, run, run.summary)
        audit(db, ap.org_id, "system", "executor", "approval.execution_failed", "approval", ap.id, result="error", detail={"error": str(e)[:300]})
        db.commit()


def deliver_outbox(db, outbox_id: str):
    ob = db.get(Outbox, outbox_id)
    if not ob or ob.status == "sent":
        return
    ap = db.get(Approval, ob.approval_id)
    run = db.get(WorkflowRun, ap.run_id) if ap.run_id else None
    acc = db.get(EmailAccount, ob.account_id)
    org = db.get(Organization, ob.org_id)
    prov = PROVIDERS[acc.provider]
    state = dict(ob.payload.get("_state") or {})
    payload = {k: v for k, v in ob.payload.items() if k != "_state"}
    ob.attempts += 1

    def persist():
        ob.payload = {**payload, "_state": state}
        db.commit()

    def done(mid, mode):
        ob.status, ob.provider_msg_id, ob.delivery_mode, ob.sent_at, ob.error = "sent", mid, mode, utcnow(), None
        ap.status, ap.result = "executed", {"provider_msg_id": mid, "mode": mode}
        audit(db, ob.org_id, "integration", acc.provider, "email.sent", "outbox", ob.id, run_id=ap.run_id, email_id=ap.email_id,
              correlation_id=run.correlation_id if run else None, detail={"provider_msg_id": mid, "mode": mode, "to": payload["to"]})
        persist()
        t = (run.context or {}).get("ticket_id") if run else None
        if t:
            call(RunCtx(db, org, run, "executor", db.get(Email, ap.email_id)), "update_support_ticket",
                 {"ticket_id": t, "status": "awaiting_customer", "note": f"Reply sent to customer ({mode})"})
            db.commit()
        _after_action(db, org, run)

    def ambiguous(msg):
        ob.status, ob.error = "ambiguous", msg[:300]
        ap.status, ap.result = "failed", {"error": msg[:300], "ambiguous": True}
        audit(db, ob.org_id, "integration", acc.provider, "email.send_ambiguous", "outbox", ob.id, result="ambiguous", detail={"error": msg[:300]})
        if run and run.state in ("executing", "approved"):
            if run.state == "approved":
                transition(db, run, "executing")
            transition(db, run, "needs_review", "Ambiguous email send outcome")
            _notify_review(db, run, "Email send outcome could not be verified; check the Sent folder before re-sending.")
        persist()

    if ob.status == "ambiguous" or state.get("draft_id") or ob.attempts > 1:
        try:
            mid = prov.verify_sent(db, acc, ob.idempotency_key, state)
        except Exception:
            mid = None
        if mid:
            return done(mid, "verified-after-retry")
        if ob.status == "ambiguous":
            return ambiguous("Outcome still unverified; not retrying automatically")
    try:
        mid, mode = prov.send(db, acc, payload, ob.idempotency_key, state)
    except AmbiguousOutcome as e:
        persist()
        try:
            mid = prov.verify_sent(db, acc, ob.idempotency_key, state)
        except Exception:
            mid = None
        return done(mid, "verified-after-ambiguity") if mid else ambiguous(str(e))
    except RetryableError as e:
        ob.error = str(e)[:300]
        persist()
        raise
    except PermanentError as e:
        ob.status, ob.error = "failed", str(e)[:300]
        ap.status, ap.result = "failed", {"error": str(e)[:300]}
        if run and run.state in ("executing", "approved"):
            if run.state == "approved":
                transition(db, run, "executing")
            transition(db, run, "failed", str(e))
        persist()
        raise
    done(mid, mode)


# ================================================================== meeting confirmation
def confirm_slot(db, run: WorkflowRun, idx: int, user=None, source="user"):
    slots = (run.context or {}).get("proposed_slots") or []
    if run.state != "awaiting_response" or not (0 <= idx < len(slots)):
        raise PermanentError("Run is not waiting for a slot confirmation or the slot index is invalid")
    org, em = db.get(Organization, run.org_id), db.get(Email, run.email_id)
    cls = db.execute(select(Classification).where(Classification.email_id == em.id)).scalars().first()
    transition(db, run, "processing", f"slot {idx + 1} confirmed via {source}")
    ctx = RunCtx(db, org, run, "meeting_agent", em, user, cls)
    acc = db.get(EmailAccount, em.account_id)
    sl = slots[idx]
    try:
        with ctx.step("confirm_slot", {"slot": sl, "source": source}) as st:
            r = call(ctx, "create_calendar_event", {"title": (run.context or {}).get("meeting_title") or em.subject,
                                                    "start": sl["start"], "end": sl["end"], "attendees": [em.sender, acc.address],
                                                    "email_id": em.id})
            st.output = r
    except PermanentError as e:
        db.rollback()
        run = db.get(WorkflowRun, run.id)
        run.summary = f"Slot could not be booked: {e}"
        transition(db, run, "needs_review", str(e))
        _notify_review(db, run, run.summary)
        return run
    run = db.get(WorkflowRun, run.id)
    _ctxset(run, confirmed_slot=idx)
    if r.get("status") == "pending_approval":
        run.summary = "Slot confirmed by recipient; calendar booking awaits approval."
        transition(db, run, "awaiting_approval")
    else:
        _ctxset(run, event_id=r["event_id"])
        run.summary = "Meeting booked and verified."
        transition(db, run, "completed")
    db.commit()
    return run


def handle_meeting_reply(db, email_id: str, run_id: str):
    em, run = db.get(Email, email_id), db.get(WorkflowRun, run_id)
    if not em or not run or run.state != "awaiting_response":
        return
    t = em.body_text.lower()
    n = len((run.context or {}).get("proposed_slots") or [])
    idx = None
    m = re.search(r"\b(?:option|slot|number|#)\s*([1-9])\b", t) or re.search(r"\b([1-9])\)", t)
    if m and int(m.group(1)) <= n:
        idx = int(m.group(1)) - 1
    elif n == 1 and re.search(r"\b(yes|confirm|works|sounds good|perfect|go ahead)\b", t):
        idx = 0
    if idx is None or re.search(r"\b(can't|cannot|not available|unavailable|reschedule)\b", t):
        em.status = "needs_review"
        notify(db, em.org_id, "Meeting reply needs a human", f"Could not determine the chosen slot in reply: {em.subject}"[:300],
               "info", f"mreply:{em.id}", "manager")
        db.commit()
        return
    confirm_slot(db, run, idx, None, "email_reply")
    em.status = "linked_reply"
    db.commit()


# ================================================================== escalation agent
def sla_scan(db):
    n = 0
    for org in db.execute(select(Organization)).scalars().all():
        if not agent_enabled(db, org.id, "escalation_agent"):
            continue
        s = policy.org_settings(org)
        ctx = RunCtx(db, org, None, "escalation_agent")
        now = utcnow()
        for t in db.execute(select(Ticket).where(Ticket.org_id == org.id, Ticket.status.in_(["open", "in_progress"]),
                                                 Ticket.sla_due_at < now)).scalars().all():
            grace = timedelta(hours=s["sla_hours"].get(t.priority, 24) / 2 * t.escalation_level)
            if now >= t.sla_due_at + grace and t.escalation_level < s["max_escalation_level"]:
                call(ctx, "create_escalation", {"ticket_id": t.id, "reason": f"SLA breached for {t.priority} ticket {t.ref}"})
                n += 1
        for ap in db.execute(select(Approval).where(Approval.org_id == org.id, Approval.status == "pending",
                                                    Approval.created_at < now - timedelta(hours=24))).scalars().all():
            notify(db, org.id, "Approval pending > 24h", f"{ap.action_type} ({ap.risk})", "warning", f"stale-ap:{ap.id}", "manager")
        db.commit()
    return n
