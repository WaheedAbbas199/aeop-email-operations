"""Durable job queue primitives (DB-backed). Handlers live in worker.py."""
import random
from datetime import timedelta
from typing import Optional

from sqlalchemy import select

from .models import Job, utcnow


def enqueue(db, org_id: str, kind: str, payload: dict, *, run_after=None, max_attempts=5,
            dedupe_key: Optional[str] = None) -> Job:
    if dedupe_key:
        ex = db.execute(select(Job).where(Job.org_id == org_id, Job.dedupe_key == dedupe_key,
                                          Job.status.in_(["queued", "running"]))).scalars().first()
        if ex:
            return ex
    job = Job(org_id=org_id, kind=kind, payload=payload, run_after=run_after or utcnow(),
              max_attempts=max_attempts, dedupe_key=dedupe_key)
    db.add(job)
    db.flush()
    return job


def backoff_seconds(attempt: int, base: float = 2.0, cap: float = 300.0, retry_after: Optional[float] = None) -> float:
    """Exponential backoff with jitter. attempt starts at 1."""
    d = min(cap, base * (2 ** (attempt - 1)))
    d = d * random.uniform(0.5, 1.0)
    return max(d, retry_after or 0)


def run_after_from(seconds: float):
    return utcnow() + timedelta(seconds=seconds)
