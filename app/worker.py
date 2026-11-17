"""Background worker: DB-backed job queue with retries (exponential backoff + jitter), dead-letter state,
crash recovery and periodic schedulers (mailbox polling, SLA scan, approval expiry)."""
import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from . import ingest, jobs, policy, workflows
from .audit import audit
from .config import settings
from .db import SessionLocal
from .errors import AmbiguousOutcome, RetryableError
from .integrations import notify
from .models import EmailAccount, Job, Organization, WorkflowRun, utcnow
from .providers import PROVIDERS

log = logging.getLogger("aeop.worker")


def h_process_email(db, job):
    workflows.orchestrate(db, job.payload["email_id"], forced=job.payload.get("forced"), user_id=job.payload.get("user_id"))


def h_execute_approval(db, job):
    workflows.execute_approval(db, job.payload["approval_id"])


def h_deliver_outbox(db, job):
    workflows.deliver_outbox(db, job.payload["outbox_id"])


def h_sync_account(db, job):
    ingest.sync_account(db, job.payload["account_id"])


def h_meeting_reply(db, job):
    workflows.handle_meeting_reply(db, job.payload["email_id"], job.payload["run_id"])


def h_sla_scan(db, job):
    workflows.sla_scan(db)


HANDLERS = {"process_email": h_process_email, "execute_approval": h_execute_approval, "deliver_outbox": h_deliver_outbox,
            "sync_account": h_sync_account, "meeting_reply": h_meeting_reply, "sla_scan": h_sla_scan}


def _claim():
    with SessionLocal() as db:
        j = db.execute(select(Job).where(Job.status == "queued", Job.run_after <= utcnow()).order_by(Job.run_after).limit(1)).scalars().first()
        if not j:
            return None
        res = db.execute(update(Job).where(Job.id == j.id, Job.status == "queued").values(status="running", started_at=utcnow()))
        db.commit()
        return j.id if res.rowcount == 1 else None


def _dead(db, job, err, klass):
    job.status, job.last_error, job.error_class, job.finished_at = "dead", str(err)[:500], klass, utcnow()
    audit(db, job.org_id, "system", "worker", "job.dead_lettered", "job", job.id, result="error",
          detail={"kind": job.kind, "error": str(err)[:300], "class": klass, "attempts": job.attempts})
    notify(db, job.org_id, f"Job failed permanently: {job.kind}", str(err)[:300], "critical", f"dead:{job.id}", "manager")
    rid = None
    if job.kind == "process_email":
        run = db.execute(select(WorkflowRun).where(WorkflowRun.email_id == job.payload.get("email_id")).order_by(WorkflowRun.created_at.desc())).scalars().first()
        if run and run.state in ("retry_scheduled", "processing"):
            run.error = str(err)[:400]
            workflows.transition(db, run, "failed", "retries exhausted" if klass == "retryable" else str(err)[:200])


def run_job(job_id: str):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        try:
            HANDLERS[job.kind](db, job)
            job = db.get(Job, job_id)
            job.status, job.finished_at, job.last_error = "done", utcnow(), None
            db.commit()
        except (RetryableError, OperationalError) as e:
            db.rollback()
            job = db.get(Job, job_id)
            job.attempts += 1
            job.last_error, job.error_class = str(e)[:500], "retryable"
            if job.attempts >= job.max_attempts:
                _dead(db, job, e, "retryable")
            else:
                job.status = "queued"
                job.run_after = jobs.run_after_from(jobs.backoff_seconds(job.attempts, retry_after=getattr(e, "retry_after", None)))
            db.commit()
        except AmbiguousOutcome as e:
            db.rollback()
            job = db.get(Job, job_id)
            _dead(db, job, e, "ambiguous")
            db.commit()
        except Exception as e:  # permanent
            db.rollback()
            job = db.get(Job, job_id)
            job.attempts += 1
            _dead(db, job, f"{type(e).__name__}: {e}", "permanent")
            db.commit()


def run_pending(max_jobs=200) -> int:
    """Synchronously drain the queue (used by tests / manual operation)."""
    n = 0
    while n < max_jobs:
        jid = _claim()
        if not jid:
            break
        run_job(jid)
        n += 1
    return n


def recover():
    """After a crash/restart: jobs stuck in 'running' go back to the queue (handlers are idempotent)."""
    with SessionLocal() as db:
        r = db.execute(update(Job).where(Job.status == "running").values(status="queued", run_after=utcnow()))
        db.commit()
        return r.rowcount


def scheduler_tick():
    with SessionLocal() as db:
        policy.expire_approvals(db)
        for org in db.execute(select(Organization)).scalars().all():
            interval = policy.org_settings(org)["poll_interval_seconds"]
            for acc in db.execute(select(EmailAccount).where(EmailAccount.org_id == org.id, EmailAccount.status == "connected",
                                                             EmailAccount.provider != "demo")).scalars():
                if not acc.last_sync_at or utcnow() - acc.last_sync_at > timedelta(seconds=interval):
                    jobs.enqueue(db, org.id, "sync_account", {"account_id": acc.id}, dedupe_key=f"sync:{acc.id}")
            jobs.enqueue(db, org.id, "sla_scan", {}, dedupe_key=f"sla:{org.id}")
        db.commit()


class Worker:
    def __init__(self, threads=2):
        self.stop = threading.Event()
        self.threads = [threading.Thread(target=self._loop, daemon=True, name=f"aeop-worker-{i}") for i in range(threads)]
        self.sched = threading.Thread(target=self._sched, daemon=True, name="aeop-scheduler")

    def start(self):
        n = recover()
        if n:
            log.info("recovered %s running jobs", n)
        for t in self.threads + [self.sched]:
            t.start()

    def _loop(self):
        while not self.stop.is_set():
            try:
                jid = _claim()
            except Exception:
                jid = None
            if jid:
                try:
                    run_job(jid)
                except Exception:
                    log.exception("job crashed")
            else:
                self.stop.wait(0.7)

    def _sched(self):
        while not self.stop.wait(20):
            try:
                scheduler_tick()
            except Exception:
                log.exception("scheduler tick failed")

    def shutdown(self):
        self.stop.set()
        for t in self.threads + [self.sched]:
            t.join(timeout=5)
