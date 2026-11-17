"""Business integration adapters.
Interfaces (ABC) define what a real CRM / ticketing / calendar / accounting / notification provider must implement.
ONLY the Local*Adapter classes below are bundled and they are LOCAL TEST ADAPTERS backed by this app's own database -
they are not connected to any external SaaS. To go live implement the ABC for your provider and register it."""
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from .models import (CalendarEvent, CrmRecord, Customer, Invoice, KnowledgeDoc, Notification, Order, PurchaseOrder,
                     Ticket, uid, utcnow)

LOCAL = "local-test-adapter"


class SupportAdapter(ABC):
    @abstractmethod
    def find_customers(self, email=None, name=None): ...
    @abstractmethod
    def get_customer(self, customer_id): ...
    @abstractmethod
    def get_order(self, order_ref): ...
    @abstractmethod
    def create_ticket(self, **kw): ...
    @abstractmethod
    def update_ticket(self, ticket_id, **kw): ...
    @abstractmethod
    def search_kb(self, query, limit=3): ...


class CrmAdapter(ABC):
    @abstractmethod
    def search(self, email=None, name=None, company=None): ...
    @abstractmethod
    def create_contact(self, **kw): ...
    @abstractmethod
    def update_contact(self, record_id, **kw): ...
    @abstractmethod
    def create_task(self, **kw): ...


class CalendarAdapter(ABC):
    @abstractmethod
    def busy(self, start, end): ...
    @abstractmethod
    def create_event(self, **kw): ...
    @abstractmethod
    def get_event(self, ref): ...


class AccountingAdapter(ABC):
    @abstractmethod
    def create_draft(self, invoice): ...


class LocalSupportAdapter(SupportAdapter):
    def __init__(self, db, org_id):
        self.db, self.org = db, org_id

    def find_customers(self, email=None, name=None):
        q = select(Customer).where(Customer.org_id == self.org)
        if email:
            q = q.where(func.lower(Customer.email) == email.lower())
        elif name:
            q = q.where(func.lower(Customer.name).contains(name.lower()))
        else:
            return []
        return self.db.execute(q.limit(10)).scalars().all()

    def get_customer(self, customer_id):
        c = self.db.get(Customer, customer_id)
        return c if c and c.org_id == self.org else None

    def get_order(self, order_ref):
        return self.db.execute(select(Order).where(Order.org_id == self.org, Order.order_ref == order_ref.upper())).scalars().first()

    def _next_ref(self):
        last = self.db.execute(select(Ticket.ref).where(Ticket.org_id == self.org).order_by(Ticket.ref.desc()).limit(1)).scalar()
        n = int(last.split("-")[1]) + 1 if last else 1
        return f"TCK-{n:05d}"

    def create_ticket(self, **kw):
        t = Ticket(org_id=self.org, ref=self._next_ref(), adapter=LOCAL, **kw)
        self.db.add(t)
        self.db.flush()
        return t

    def update_ticket(self, ticket_id, **kw):
        t = self.db.get(Ticket, ticket_id)
        if not t or t.org_id != self.org:
            return None
        note = kw.pop("note", None)
        for k, v in kw.items():
            setattr(t, k, v)
        if note:
            t.notes = [*(t.notes or []), {"at": utcnow().isoformat(), "note": note}]
        self.db.flush()
        return t

    def search_kb(self, query, limit=3):
        words = {w for w in re.findall(r"[a-z]{4,}", query.lower())}
        scored = []
        for d in self.db.execute(select(KnowledgeDoc).where(KnowledgeDoc.org_id == self.org)).scalars():
            text = (d.title + " " + d.content).lower()
            s = sum(1 for w in words if w in text)
            if s:
                scored.append((s, d))
        return [d for _, d in sorted(scored, key=lambda x: -x[0])[:limit]]


class LocalCrmAdapter(CrmAdapter):
    def __init__(self, db, org_id):
        self.db, self.org = db, org_id

    def search(self, email=None, name=None, company=None):
        q = select(CrmRecord).where(CrmRecord.org_id == self.org, CrmRecord.kind == "contact")
        conds = []
        if email:
            conds.append(func.lower(CrmRecord.email) == email.lower())
        if name:
            conds.append(func.lower(CrmRecord.name).contains(name.lower()))
        if company:
            conds.append(func.lower(CrmRecord.company).contains(company.lower()))
        if not conds:
            return []
        return self.db.execute(q.where(or_(*conds)).limit(10)).scalars().all()

    def create_contact(self, **kw):
        r = CrmRecord(org_id=self.org, kind="contact", adapter=LOCAL, **kw)
        self.db.add(r)
        self.db.flush()
        return r

    def update_contact(self, record_id, **kw):
        r = self.db.get(CrmRecord, record_id)
        if not r or r.org_id != self.org:
            return None
        data = kw.pop("data", None)
        for k, v in kw.items():
            setattr(r, k, v)
        if data:
            r.data = {**(r.data or {}), **data}
        self.db.flush()
        return r

    def create_task(self, **kw):
        r = CrmRecord(org_id=self.org, kind="task", adapter=LOCAL, **kw)
        self.db.add(r)
        self.db.flush()
        return r


class LocalCalendarAdapter(CalendarAdapter):
    def __init__(self, db, org_id):
        self.db, self.org = db, org_id

    def busy(self, start, end):
        rows = self.db.execute(select(CalendarEvent).where(CalendarEvent.org_id == self.org, CalendarEvent.status == "confirmed",
                                                           CalendarEvent.start < end, CalendarEvent.end > start)).scalars().all()
        return [(r.start, r.end) for r in rows]

    def create_event(self, **kw):
        ev = CalendarEvent(org_id=self.org, adapter=LOCAL, external_ref="LOCAL-EVT-" + uid()[:8].upper(), **kw)
        self.db.add(ev)
        self.db.flush()
        return ev

    def get_event(self, ref):
        return self.db.execute(select(CalendarEvent).where(CalendarEvent.org_id == self.org, CalendarEvent.external_ref == ref)).scalars().first()


class LocalAccountingAdapter(AccountingAdapter):
    """Creates a DRAFT only. Never pays, posts or approves anything."""
    def __init__(self, db, org_id):
        self.db, self.org = db, org_id

    def create_draft(self, invoice: Invoice):
        invoice.accounting_ref = invoice.accounting_ref or "LOCAL-ACC-" + uid()[:8].upper()
        invoice.adapter = LOCAL
        invoice.status = "draft_created"
        self.db.flush()
        return invoice.accounting_ref


def notify(db, org_id, title, body="", level="info", dedupe_key=None, role=""):
    key = dedupe_key or uid()
    if db.execute(select(Notification.id).where(Notification.org_id == org_id, Notification.dedupe_key == key)).first():
        return None
    n = Notification(org_id=org_id, title=title[:300], body=body, level=level, dedupe_key=key, target_role=role)
    db.add(n)
    db.flush()
    return n
