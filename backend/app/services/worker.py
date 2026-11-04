"""SQLite-backed background worker for build jobs (max 1 concurrent).

When Settings.build_async is on, POST /build only enqueues a QUEUED job
and returns the mission; this worker's single thread claims jobs
atomically, runs the same generate -> verify -> coverage stages as the
synchronous path, and checkpoints each stage so a restart resumes
without repeating model calls. Cancel takes effect at stage
boundaries. All progress stays visible through the existing mission
polling (progress_step/label); operational detail goes to server logs.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from pydantic import ValidationError

from app.core.store import (
    list_mission_ids,
    load_job,
    load_mission,
    save_job,
    save_mission,
    utc_now_iso,
)
from app.demo.restaurant_app import APP_ROOT
from app.demo.scaffold import scaffold_files
from app.models.generation import GenerationRecord, GenerationStatus
from app.models.jobs import JobKind, JobRecord, JobStatus
from app.models.mission import Mission, MissionStatus
from app.services import jobs as job_tracker
from app.services.coverage import build_coverage_report
from app.services.developer import GenerationValidationError, generate_application
from app.services.pipeline import reconstruct_output, run_mission_verification
from app.services.spark_client import SparkError

logger = logging.getLogger("field.worker")

#: Seconds between queue polls and heartbeats while a job runs.
POLL_INTERVAL_S = 2.0
HEARTBEAT_INTERVAL_S = 60.0
#: A claimed/running job silent this long is presumed orphaned (process
#: died mid-stage) and becomes eligible for requeue on startup recovery.
STALE_AFTER_S = 600.0

#: Checkpoint names persisted in job.result["stages"] (result is unused
#: otherwise; stages completed are never re-executed on resume).
STAGE_GENERATED = "generated"
STAGE_VERIFIED = "verified"


def claim_next_job(db_path: Path | None = None) -> JobRecord | None:
    """Atomically claim the oldest QUEUED build job. Single-writer safe
    via BEGIN IMMEDIATE; returns None when the queue is empty."""
    from app.core.store import init_db, resolve_db_path

    db = init_db(db_path if db_path is not None else resolve_db_path())
    conn = sqlite3.connect(db, timeout=30.0)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT data FROM jobs WHERE kind = 'build' AND status = 'queued' "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job = JobRecord.model_validate_json(row[0])
            now = utc_now_iso()
            cursor = conn.execute(
                "UPDATE jobs SET status = 'claimed', heartbeat_at = ?, updated_at = ?, "
                "data = json_set(json_set(data, '$.status', 'claimed'), "
                "'$.heartbeat_at', ?) WHERE id = ? AND status = 'queued'",
                (now, now, now, job.id),
            )
            conn.execute("COMMIT")
            if cursor.rowcount != 1:
                return None
            job.status = JobStatus.CLAIMED
            job.heartbeat_at = now
            job.updated_at = now
            return job
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _generated_files_present(mission: Mission) -> bool:
    manifest = mission.generation.manifest if mission.generation else []
    files = mission.files or {}
    return bool(manifest) and all(f"{APP_ROOT}/{path}" in files for path in manifest)


def _cancelled(job_id: str, db_path) -> bool:
    job = load_job(job_id, db_path)
    return job is not None and job.status == JobStatus.CANCELLED


def _restore_approved(mission: Mission, message: str, db_path) -> Mission:
    mission.status = MissionStatus.APPROVED
    mission.error = message
    mission.progress_step = ""
    mission.progress_label = ""
    return save_mission(mission, db_path)


def run_build_job(job: JobRecord, db_path=None) -> JobRecord:
    """Execute one claimed build job to a terminal state. Stage results
    checkpoint into the job row; re-running after a crash resumes instead
    of repeating completed stages (in particular: no duplicate model
    spend for generation)."""
    started = time.monotonic()
    job.status = JobStatus.RUNNING
    job.heartbeat_at = utc_now_iso()
    save_job(job, db_path)
    mission = load_mission(job.mission_id, db_path)
    if mission is None or mission.spec is None:  # pragma: no cover - defensive
        return job_tracker.finish_job(
            job.id, JobStatus.FAILED, ms=job_tracker.monotonic_ms(started),
            error="Build job has no mission or approved spec.", error_class="ValueError",
            db_path=db_path,
        )
    spec = mission.spec
    # Reload fresh: a previous run may have checkpointed before crashing.
    job = load_job(job.id, db_path)
    done: set[str] = set(job.stages_done or [])
    usage: dict = dict(mission.generation.usage) if mission.generation else {}

    def checkpoint(stage: str) -> None:
        done.add(stage)
        current = load_job(job.id, db_path)
        current.stages_done = sorted(done)
        save_job(current, db_path)

    try:
        if _cancelled(job.id, db_path):
            _restore_approved(mission, "Build cancelled before it started.", db_path)
            return load_job(job.id, db_path)
        if STAGE_GENERATED not in done or not _generated_files_present(mission):
            mission.progress_step = "generating"
            mission.progress_label = "Generating application files with Muse Spark…"
            save_mission(mission, db_path)
            try:
                output, fresh_usage, model_id = generate_application(spec)
            except (SparkError, GenerationValidationError, ValidationError) as exc:
                record = mission.generation or GenerationRecord(
                    status=GenerationStatus.GENERATING, started_at=utc_now_iso())
                record.status = GenerationStatus.FAILED
                record.completed_at = utc_now_iso()
                record.error = f"Generation failed: {exc}"[:2000]
                record.error_class = type(exc).__name__
                mission.generation = record
                _restore_approved(mission, record.error, db_path)
                logger.warning("job_stage job=%s stage=generate outcome=failed class=%s",
                               job.id, record.error_class)
                return job_tracker.finish_job(
                    job.id, JobStatus.FAILED, ms=job_tracker.monotonic_ms(started),
                    error=record.error, error_class=record.error_class, db_path=db_path)
            merged = dict(scaffold_files())
            for item in output.files:
                merged[f"{APP_ROOT}/{item.path}"] = item.content
            mission.files = merged
            mission.generation = GenerationRecord(
                status=GenerationStatus.GENERATED, started_at=record_started(mission),
                completed_at=utc_now_iso(), model_id=model_id,
                usage=dict(fresh_usage), summary=output.summary,
                manifest=[item.path for item in output.files],
                feature_map=list(output.feature_map),
                unimplemented=list(output.unimplemented),
                assumptions=list(output.assumptions),
            )
            usage = dict(fresh_usage)
            save_mission(mission, db_path)
            checkpoint(STAGE_GENERATED)
            logger.info("job_stage job=%s stage=generate outcome=ok files=%d",
                        job.id, len(output.files))
        else:
            output = reconstruct_output(mission)
            logger.info("job_stage job=%s stage=generate outcome=resumed", job.id)
            if output is None:  # pragma: no cover - presence checked above
                raise ValueError("Checkpoint claims generated files that are missing.")

        if _cancelled(job.id, db_path):
            _restore_approved(mission, "Build cancelled by user.", db_path)
            job_tracker.record_attempt(
                job.id, job_tracker.monotonic_ms(started), usage, db_path=db_path)
            return load_job(job.id, db_path)

        if STAGE_VERIFIED not in done or mission.verification is None:
            mission = run_mission_verification(mission.id, db_path=db_path)
            checkpoint(STAGE_VERIFIED)
            logger.info("job_stage job=%s stage=verify outcome=%s",
                        job.id, (mission.verification.status.value
                                 if mission.verification else "none"))

        if _cancelled(job.id, db_path):
            _restore_approved(mission, "Build cancelled by user.", db_path)
            job_tracker.record_attempt(
                job.id, job_tracker.monotonic_ms(started), usage, db_path=db_path)
            return load_job(job.id, db_path)

        if mission.verification is not None:
            mission.verification.coverage = build_coverage_report(
                spec, output, mission.verification.e2b)
            save_mission(mission, db_path)
        logger.info("job_done job=%s status=succeeded ms=%d",
                    job.id, job_tracker.monotonic_ms(started))
        return job_tracker.finish_job(
            job.id, JobStatus.SUCCEEDED, ms=job_tracker.monotonic_ms(started),
            usage=usage, db_path=db_path)
    except Exception as exc:
        # The unattended worker must never strand a mission mid-build.
        fresh = load_mission(job.mission_id, db_path)
        if fresh is not None and fresh.status not in (
                MissionStatus.APPROVED, MissionStatus.VERIFIED,
                MissionStatus.VERIFIED_WITH_WARNINGS, MissionStatus.FAILED,
                MissionStatus.AUDIT_LIMITED):
            _restore_approved(
                fresh, f"Build failed: {exc}"[:2000], db_path)
        logger.warning("job_done job=%s status=failed class=%s",
                       job.id, type(exc).__name__)
        return job_tracker.finish_job(
            job.id, JobStatus.FAILED, ms=job_tracker.monotonic_ms(started),
            usage=usage, error=f"Build failed: {exc}"[:2000],
            error_class=type(exc).__name__, db_path=db_path)


def record_started(mission: Mission) -> str:
    if mission.generation is not None and mission.generation.started_at:
        return mission.generation.started_at
    return utc_now_iso()


def recover_stale_state(db_path=None) -> dict[str, int]:
    """Startup recovery: requeue orphaned jobs, restore stranded missions.

    A job is orphaned when claimed/running with a heartbeat older than
    STALE_AFTER_S. A mission is stranded when mid-build with no active
    job. Returns counts for server logs. Never touches terminal states.
    """
    from app.core.store import init_db, resolve_db_path

    db = init_db(db_path if db_path is not None else resolve_db_path())
    now = time.time()
    requeued = 0
    with sqlite3.connect(db, timeout=30.0) as conn:
        rows = conn.execute(
            "SELECT id, heartbeat_at FROM jobs "
            "WHERE kind = 'build' AND status IN ('claimed', 'running')"
        ).fetchall()
        for job_id, heartbeat_at in rows:
            try:
                age = now - _parse_utc(heartbeat_at)
            except (TypeError, ValueError):
                age = float("inf")
            if age >= STALE_AFTER_S:
                now = utc_now_iso()
                conn.execute(
                    "UPDATE jobs SET status = 'queued', heartbeat_at = ?, "
                    "updated_at = ?, data = json_set(json_set(data, '$.status', "
                    "'queued'), '$.heartbeat_at', ?) "
                    "WHERE id = ? AND status IN ('claimed', 'running')",
                    (now, now, now, job_id),
                )
                requeued += 1
    restored = 0
    for mission_id in list_mission_ids(db):
        mission = load_mission(mission_id, db)
        if mission is None:
            continue
        if mission.status not in (MissionStatus.BUILDING, MissionStatus.RUNNING,
                                  MissionStatus.AUDITING):
            continue
        if job_tracker.active_job(mission_id, JobKind.BUILD, db) is not None:
            continue
        _restore_approved(
            mission,
            "Build did not finish (the process restarted mid-build). "
            "No partial work was kept; safe to retry.",
            db,
        )
        restored += 1
    if requeued or restored:
        logger.warning("worker_recover requeued=%d restored=%d", requeued, restored)
    return {"requeued": requeued, "restored": restored}


def _parse_utc(value: str) -> float:
    from datetime import datetime, timezone

    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class Worker:
    """Single-threaded job runner (structural max-1 concurrency)."""

    def __init__(self, db_path=None):
        self._db_path = db_path
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="field-worker", daemon=True)
        self._thread.start()
        logger.info("worker_start mode=single-thread max_concurrent=1")

    def stop(self, timeout_s: float = 10.0) -> None:
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout_s)

    def run_once(self) -> bool:
        """Claim and run a single job. Returns False when idle."""
        job = claim_next_job(self._db_path)
        if job is None:
            return False
        logger.info("job_claim job=%s mission=%s", job.id, job.mission_id)
        heartbeat = _Heartbeat(job.id, self._db_path)
        heartbeat.start()
        try:
            run_build_job(job, self._db_path)
        finally:
            heartbeat.stop()
        return True

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self.run_once():
                    self._stop_event.wait(POLL_INTERVAL_S)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("worker_loop error=%r; continuing", exc)
                self._stop_event.wait(POLL_INTERVAL_S)


class _Heartbeat(threading.Thread):
    """Refresh a job's heartbeat while a long stage runs."""

    def __init__(self, job_id: str, db_path=None):
        super().__init__(name=f"field-heartbeat-{job_id}", daemon=True)
        self._job_id = job_id
        self._db_path = db_path
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5.0)

    def run(self) -> None:
        while not self._stop_event.wait(HEARTBEAT_INTERVAL_S):
            try:
                job = load_job(self._job_id, self._db_path)
                if job is None or job.status not in (
                        JobStatus.CLAIMED, JobStatus.RUNNING):
                    return
                job.heartbeat_at = utc_now_iso()
                save_job(job, self._db_path)
            except Exception:  # pragma: no cover - heartbeat must never kill work
                return
