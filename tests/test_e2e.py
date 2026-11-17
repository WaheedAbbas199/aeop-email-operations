"""End-to-end tests of the three business workflows through the real API, worker handlers and DB.
External providers are NOT contacted (demo mailbox + local adapters)."""
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CalendarEvent, Invoice, Outbox, Ticket
from app.worker import run_pending


def drain():
    return run_pending(500)


@pytest.fixture(scope="module")
def loaded(admin):
    assert admin.post("/api/v1/demo/load").json()["status"] == "loaded"
    import time
    drain()
    time.sleep(6.5)  # the duplicate-invoice email is intentionally delayed
    drain()
    return admin


def runs_by_subject(c):
    return {r["subject"]: r for r in c.get("/api/v1/runs?page_size=100").json()["items"]}


def test_all_demo_emails_processed(loaded):
    rs = runs_by_subject(loaded)
    assert len(rs) == 12
    assert rs["Order ORD-10001 still not received - unacceptable"]["state"] == "awaiting_approval"
    assert rs["Invoice INV-2026-0042 attached"]["state"] == "completed"
    assert rs["Reminder: invoice INV-2026-0042 attached again"]["state"] == "needs_review"  # duplicate
    assert rs["Invoice INV-8891 for PO-7002"]["state"] == "needs_review"  # PO mismatch
    assert rs["Invoice INV-U-77"]["state"] == "needs_review"  # bank change
    assert rs["Invoice INV-G-1004 attached"]["state"] == "awaiting_approval"  # above threshold
    assert rs["Urgent update required"]["state"] == "needs_review"  # injection
    assert rs["Your weekly digest"]["state"] == "completed"
    assert rs["Hello"]["state"] == "needs_review"


def test_complaint_flow_approve_send_audit(loaded):
    aps = loaded.get("/api/v1/approvals?status=pending").json()["items"]
    ap = next(a for a in aps if a["subject"].startswith("Order ORD-10001"))
    assert "ORD-10001" in ap["payload"]["body"] and "delayed" in ap["payload"]["body"]
    assert "refund" not in ap["payload"]["body"].lower()
    r = loaded.post(f"/api/v1/approvals/{ap['id']}/decision", json={"decision": "approve", "reason": "ok"})
    assert r.status_code == 200
    drain()
    with SessionLocal() as db:
        ob = db.execute(select(Outbox)).scalars().first()
        assert ob.status == "sent" and "SIMULATED" in ob.delivery_mode
        t = db.execute(select(Ticket).where(Ticket.priority == "P1")).scalars().first()
        assert t and t.status == "awaiting_customer"
    run = runs_by_subject(loaded)["Order ORD-10001 still not received - unacceptable"]
    assert run["state"] == "completed"
    assert loaded.get("/api/v1/audit/verify").json()["ok"] is True


def test_invoice_draft_never_paid(loaded):
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.invoice_number == "INV-2026-0042")).scalars().all()
        assert [i.status for i in inv].count("draft_created") == 1
        assert all(i.status != "paid" for i in db.execute(select(Invoice)).scalars())


def test_large_invoice_requires_approval_then_draft(loaded):
    ap = next(a for a in loaded.get("/api/v1/approvals?status=pending").json()["items"] if a["action_type"] == "create_accounting_draft")
    assert ap["risk"] == "high"
    assert loaded.post(f"/api/v1/approvals/{ap['id']}/decision", json={"decision": "approve"}).status_code == 200
    drain()
    with SessionLocal() as db:
        inv = db.execute(select(Invoice).where(Invoice.invoice_number == "INV-G-1004")).scalars().first()
        assert inv.status == "draft_created" and inv.accounting_ref


def test_meeting_flow(loaded):
    rs = runs_by_subject(loaded)
    m = rs["Quick meeting about the integration"]
    assert m["state"] == "awaiting_approval"
    ap = next(a for a in loaded.get("/api/v1/approvals?status=pending").json()["items"] if a["subject"] == "Quick meeting about the integration")
    assert "isn't available" in ap["payload"]["body"]  # requested slot conflicts with seeded event
    loaded.post(f"/api/v1/approvals/{ap['id']}/decision", json={"decision": "approve"})
    drain()
    run = loaded.get(f"/api/v1/runs/{m['id']}").json()
    assert run["run"]["state"] == "awaiting_response"
    r = loaded.post(f"/api/v1/runs/{m['id']}/confirm-slot", json={"slot_index": 0})
    assert r.status_code == 200 and r.json()["state"] == "awaiting_approval"
    ap2 = next(a for a in loaded.get("/api/v1/approvals?status=pending").json()["items"] if a["action_type"] == "create_calendar_event")
    loaded.post(f"/api/v1/approvals/{ap2['id']}/decision", json={"decision": "approve"})
    drain()
    with SessionLocal() as db:
        evs = db.execute(select(CalendarEvent).where(CalendarEvent.email_id.is_not(None))).scalars().all()
        assert len(evs) == 1 and evs[0].external_ref.startswith("LOCAL-EVT")
    assert loaded.get(f"/api/v1/runs/{m['id']}").json()["run"]["state"] == "completed"


def test_sales_flow_and_crm_dedupe(loaded):
    recs = loaded.get("/api/v1/records/crm").json()["items"]
    contacts = [r for r in recs if r["kind"] == "contact" and r["email"] == "priya@contoso.example"]
    assert len(contacts) == 1 and contacts[0]["data"]["lead"].get("budget")
    assert any(r["kind"] == "task" for r in recs)


def test_injection_email_took_no_action(loaded):
    rs = runs_by_subject(loaded)
    r = loaded.get(f"/api/v1/runs/{rs['Urgent update required']['id']}").json()
    assert r["approvals"] == [] and all(s["kind"] != "tool" for s in r["steps"])
