"""Persistent job records: one row per requested operation attempt-track.

A job tracks a single mission operation (today: one POST /build) across
its attempts: what ran, how long it took, what it cost (token usage),
and how it ended. The worker (Phase 3) appends attempts on retry and
claims queued rows atomically; until then each sync call creates and
finishes exactly one attempt.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobKind(str, Enum):
    BUILD = "build"


ACTIVE_JOB_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.CLAIMED, JobStatus.RUNNING})


class JobAttempt(BaseModel):
    """One execution attempt within a job (inner transport retries stay
    in server logs; attempts are logical tries)."""

    attempt_no: int = 1
    started_at: str = ""
    completed_at: str = ""
    error_class: str = ""
    error: str = ""
    ms: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)


class JobRecord(BaseModel):
    """Persisted operation job for one mission."""

    id: str
    mission_id: str
    kind: JobKind = JobKind.BUILD
    status: JobStatus = JobStatus.QUEUED
    attempt: int = 0
    max_attempts: int = 3
    idempotency_key: str | None = None
    attempts: list[JobAttempt] = Field(default_factory=list)
    stages_done: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    error_class: str = ""
    heartbeat_at: str = ""
    created_at: str = ""
    updated_at: str = ""
