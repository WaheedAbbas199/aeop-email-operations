import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator

from .db import Base


def uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, always returns timezone-aware UTC (works on SQLite and Postgres)."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value else None


def pk():
    return mapped_column(String(32), primary_key=True, default=uid)


def org_fk():
    return mapped_column(String(32), ForeignKey("organizations.id"), index=True, nullable=False)


def ts():
    return mapped_column(UTCDateTime, default=utcnow)


def js(default=dict):
    return mapped_column(JSON, default=default)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict] = js()
    created_at: Mapped[datetime] = ts()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = ts()


class EmailAccount(Base):
    __tablename__ = "email_accounts"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    provider: Mapped[str] = mapped_column(String(20))  # gmail | outlook | demo
    address: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(30), default="connected")  # connected|reauth_required|error|disconnected
    token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scopes: Mapped[list] = js(list)
    cursor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = js()
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts()
    __table_args__ = (UniqueConstraint("org_id", "provider", "address"),)


class Email(Base):
    __tablename__ = "emails"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    account_id: Mapped[str] = mapped_column(ForeignKey("email_accounts.id"), index=True)
    provider_msg_id: Mapped[str] = mapped_column(String(300))
    thread_id: Mapped[str] = mapped_column(String(300), index=True)
    sender: Mapped[str] = mapped_column(String(320), index=True)
    sender_name: Mapped[str] = mapped_column(String(200), default="")
    recipients: Mapped[list] = js(list)
    subject: Mapped[str] = mapped_column(String(500), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    labels: Mapped[list] = js(list)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(30), default="received", index=True)
    correlation_id: Mapped[str] = mapped_column(String(32), default=uid)
    injection_flags: Mapped[list] = js(list)
    created_at: Mapped[datetime] = ts()
    __table_args__ = (UniqueConstraint("account_id", "provider_msg_id"), Index("ix_email_org_recv", "org_id", "received_at"))


class Attachment(Base):
    __tablename__ = "email_attachments"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    storage_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    text_content: Mapped[str] = mapped_column(Text, default="")
    safety_status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | blocked | unsupported
    safety_reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = ts()


class Classification(Base):
    __tablename__ = "email_classifications"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), unique=True)
    category: Mapped[str] = mapped_column(String(40))
    intent: Mapped[str] = mapped_column(String(200), default="")
    urgency: Mapped[str] = mapped_column(String(20), default="normal")
    priority: Mapped[str] = mapped_column(String(4), default="P3")
    sentiment: Mapped[str] = mapped_column(String(20), default="neutral")
    language: Mapped[str] = mapped_column(String(20), default="en")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    entities: Mapped[dict] = js()
    requested_action: Mapped[str] = mapped_column(String(300), default="")
    missing_info: Mapped[list] = js(list)
    recommended_workflow: Mapped[str] = mapped_column(String(30), default="manual_review")
    evidence: Mapped[str] = mapped_column(Text, default="")
    engine: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(30), default="")
    usage: Mapped[dict] = js()
    created_at: Mapped[datetime] = ts()


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), index=True)
    company: Mapped[str] = mapped_column(String(200), default="")
    tier: Mapped[str] = mapped_column(String(20), default="standard")
    created_at: Mapped[datetime] = ts()


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    order_ref: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30))
    carrier_status: Mapped[str] = mapped_column(String(200), default="")
    eta: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("org_id", "order_ref"),)


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)


class Ticket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    ref: Mapped[str] = mapped_column(String(20), index=True)
    customer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("customers.id"), nullable=True)
    email_id: Mapped[Optional[str]] = mapped_column(ForeignKey("emails.id"), nullable=True, unique=True)
    subject: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(40), default="support")
    priority: Mapped[str] = mapped_column(String(4), default="P3")
    team: Mapped[str] = mapped_column(String(80), default="Support Tier 1")
    status: Mapped[str] = mapped_column(String(30), default="open")
    summary: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[list] = js(list)
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    adapter: Mapped[str] = mapped_column(String(40), default="local-test-adapter")
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("org_id", "ref"),)


class CrmRecord(Base):
    __tablename__ = "crm_records"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    kind: Mapped[str] = mapped_column(String(20))  # contact | task
    name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(320), default="", index=True)
    company: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="new")
    data: Mapped[dict] = js()
    email_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    adapter: Mapped[str] = mapped_column(String(40), default="local-test-adapter")
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    po_number: Mapped[str] = mapped_column(String(40))
    vendor: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    total_cents: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("org_id", "po_number"),)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    email_id: Mapped[Optional[str]] = mapped_column(ForeignKey("emails.id"), nullable=True)
    attachment_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, unique=True)
    vendor: Mapped[str] = mapped_column(String(200), default="")
    vendor_norm: Mapped[str] = mapped_column(String(200), default="", index=True)
    invoice_number: Mapped[str] = mapped_column(String(80), default="", index=True)
    invoice_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    due_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="")
    subtotal_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tax_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    po_number: Mapped[str] = mapped_column(String(40), default="")
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(30), default="extracted")
    source_refs: Mapped[dict] = js()
    validation: Mapped[dict] = js()
    accounting_ref: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    adapter: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = ts()


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    title: Mapped[str] = mapped_column(String(300))
    start: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    end: Mapped[datetime] = mapped_column(UTCDateTime)
    attendees: Mapped[list] = js(list)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    external_ref: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    email_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    adapter: Mapped[str] = mapped_column(String(40), default="local-test-adapter")
    created_at: Mapped[datetime] = ts()


class AgentConfig(Base):
    __tablename__ = "agents"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    name: Mapped[str] = mapped_column(String(60))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    config: Mapped[dict] = js()
    __table_args__ = (UniqueConstraint("org_id", "name"),)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), index=True)
    workflow: Mapped[str] = mapped_column(String(30), default="pending")
    state: Mapped[str] = mapped_column(String(30), default="received", index=True)
    correlation_id: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = js()
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = ts()
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = ts()


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default="step")  # step | tool
    status: Mapped[str] = mapped_column(String(20), default="running")
    input: Mapped[dict] = js()
    output: Mapped[dict] = js()
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = ts()
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = js()
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_class: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = ts()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    key: Mapped[str] = mapped_column(String(200), unique=True)
    result: Mapped[dict] = js()
    created_at: Mapped[datetime] = ts()


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("workflow_runs.id"), nullable=True, index=True)
    email_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(String(40))
    risk: Mapped[str] = mapped_column(String(10), default="medium")
    required_permission: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = js()
    payload_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(String(60), default="")
    decided_by: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    result: Mapped[dict] = js()
    created_at: Mapped[datetime] = ts()


class Outbox(Base):
    __tablename__ = "action_outbox"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), unique=True)
    account_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(20), default="email")
    payload: Mapped[dict] = js()
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|ambiguous
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    provider_msg_id: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    delivery_mode: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts()
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = org_fk()
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(80), default="")
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    email_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    authorization: Mapped[str] = mapped_column(String(10), default="allowed")
    result: Mapped[str] = mapped_column(String(20), default="ok")
    detail: Mapped[dict] = js()
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    level: Mapped[str] = mapped_column(String(10), default="info")
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    target_role: Mapped[str] = mapped_column(String(40), default="")
    dedupe_key: Mapped[str] = mapped_column(String(200))
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()
    __table_args__ = (UniqueConstraint("org_id", "dedupe_key"),)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = pk()
    org_id: Mapped[str] = org_fk()
    engine: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(30))
    metrics: Mapped[dict] = js()
    created_at: Mapped[datetime] = ts()
