"""Job lifecycle helpers over the SQLite jobs table.

Phase 1: each synchronous POST /build creates one job and finishes it
with outcome + usage + error class (monitoring data, no behavior
change). Phase 3's worker reuses these plus atomic claim/requeue.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.store import load_active_job, load_job, load_job_by_key, save_job
from app.models.jobs import JobAttempt, JobKind, JobRecord, JobStatus


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def create_job(
    mission_id: str,
    kind: JobKind = JobKind.BUILD,
    status: JobStatus = JobStatus.RUNNING,
    idempotency_key: str | None = None,
    db_path=None,
) -> JobRecord:
    """Persist a new job. Raises sqlite3.IntegrityError when a live job
    for (mission_id, kind) already exists (the duplicate-build lock)."""
    from app.core.store import utc_now_iso

    now = utc_now_iso()
    job = JobRecord(
        id=new_job_id(),
        mission_id=mission_id,
        kind=kind,
        status=status,
        idempotency_key=idempotency_key,
        heartbeat_at=now,
        created_at=now,
        updated_at=now,
    )
    return save_job(job, path=db_path)


def record_attempt(
    job_id: str,
    ms: int,
    usage: dict[str, Any] | None = None,
    error: str = "",
    error_class: str = "",
    db_path=None,
) -> JobRecord | None:
    """Append one logical attempt to a job. Returns None when unknown."""
    from app.core.store import utc_now_iso

    job = load_job(job_id, path=db_path)
    if job is None:
        return None
    now = utc_now_iso()
    job.attempt += 1
    job.attempts.append(JobAttempt(
        attempt_no=job.attempt,
        started_at=job.heartbeat_at or now,
        completed_at=now,
        error_class=error_class,
        error=error[:2000],
        ms=ms,
        usage=dict(usage or {}),
    ))
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)):
            prior = job.usage.get(key, 0)
            job.usage[key] = (prior if isinstance(prior, (int, float)) else 0) + value
    job.heartbeat_at = now
    return save_job(job, path=db_path)


def finish_job(
    job_id: str,
    status: JobStatus,
    ms: int = 0,
    usage: dict[str, Any] | None = None,
    error: str = "",
    error_class: str = "",
    db_path=None,
) -> JobRecord | None:
    """Record the terminal attempt and close the job. Returns None when
    unknown. Terminal statuses release the duplicate-build lock."""
    job = record_attempt(job_id, ms, usage, error, error_class, db_path)
    if job is None:
        return None
    job.status = status
    job.error = error[:2000]
    job.error_class = error_class
    return save_job(job, path=db_path)


def active_job(
    mission_id: str, kind: JobKind = JobKind.BUILD, db_path=None
) -> JobRecord | None:
    return load_active_job(mission_id, kind.value, path=db_path)


def job_by_key(
    mission_id: str, idempotency_key: str, db_path=None
) -> JobRecord | None:
    return load_job_by_key(mission_id, idempotency_key, path=db_path)


def cancel_job(job_id: str, db_path=None) -> JobRecord | None:
    """Mark an active job cancelled. The worker stops at the next stage
    boundary; a still-queued job never starts. Returns None when unknown."""
    job = load_job(job_id, db_path)
    if job is None or job.status not in (
            JobStatus.QUEUED, JobStatus.CLAIMED, JobStatus.RUNNING):
        return None
    job.status = JobStatus.CANCELLED
    return save_job(job, db_path)


def monotonic_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
