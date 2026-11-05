"""Build orchestration: APPROVED -> generated app -> E2B -> verdict.

Steps: confirm approval -> generate via Spark -> validate manifest ->
merge FIELD scaffold -> run the existing verification pipeline ->
append the coverage report. Generation failure restores the previous
status (APPROVED, or the terminal state on forced rebuild) with the
real error, so retry is always safe and nothing is half-built.

Safe retry behavior:
- APPROVED with no/failed generation: build runs.
- BUILDING/RUNNING/AUDITING: 409, a build is already in progress.
- Terminal verification state: 409 unless force=true (explicit rebuild).
"""

from __future__ import annotations

import sqlite3
import time

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.store import load_mission, save_mission, utc_now_iso
from app.demo.restaurant_app import APP_ROOT
from app.demo.scaffold import scaffold_files
from app.models.generation import GenerationRecord, GenerationStatus
from app.models.jobs import JobKind, JobStatus
from app.models.mission import Mission, MissionStatus
from app.services import jobs as job_tracker
from app.services.coverage import build_coverage_report
from app.services.developer import GenerationValidationError, generate_application
from app.services.interview import LifecycleError
from app.services.pipeline import run_mission_verification
from app.services.spark_client import SparkError

TERMINAL_VERIFICATION_STATES = frozenset({
    MissionStatus.VERIFIED,
    MissionStatus.VERIFIED_WITH_WARNINGS,
    MissionStatus.FAILED,
    MissionStatus.AUDIT_LIMITED,
    MissionStatus.INCONCLUSIVE,
})
IN_PROGRESS_STATES = frozenset({
    MissionStatus.BUILDING,
    MissionStatus.RUNNING,
    MissionStatus.AUDITING,
})


def _enqueue_build(
    mission: Mission, idempotency_key: str | None, db_path=None
) -> Mission:
    """Async path: persist a QUEUED job and return immediately.

    The worker claims it (max 1 concurrent build) and drives the same
    stages; the UI polls the mission as usual. Guards and idempotency
    replay already ran in build_application.
    """
    try:
        job_tracker.create_job(
            mission.id, JobKind.BUILD, status=JobStatus.QUEUED,
            idempotency_key=idempotency_key, db_path=db_path,
        )
    except sqlite3.IntegrityError as exc:
        if idempotency_key:
            seen = job_tracker.job_by_key(mission.id, idempotency_key, db_path)
            if seen is not None:
                return load_mission(mission.id, db_path)
        raise LifecycleError(
            "A build is already in progress for this mission."
        ) from exc
    mission.status = MissionStatus.BUILDING
    mission.error = ""
    mission.progress_step = "queued"
    mission.progress_label = "Build queued — the background worker picks it up shortly…"
    mission.generation = GenerationRecord(
        status=GenerationStatus.GENERATING, started_at=utc_now_iso())
    return save_mission(mission, db_path)


def cancel_build(mission_id: str, db_path=None) -> Mission:
    """Cancel the mission's active build job. A queued job never starts
    (mission restored here); a worker-run job stops at the next stage
    boundary. Synchronous in-flight builds cannot be interrupted."""
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    job = job_tracker.active_job(mission_id, JobKind.BUILD, db_path)
    if job is None:
        raise LifecycleError("No active build to cancel for this mission.")
    if job.status == JobStatus.QUEUED:
        job_tracker.cancel_job(job.id, db_path)
        mission.status = MissionStatus.APPROVED
        mission.error = "Build cancelled before it started."
        mission.progress_step = ""
        mission.progress_label = ""
        if mission.generation is not None:
            mission.generation.status = GenerationStatus.FAILED
            mission.generation.completed_at = utc_now_iso()
            mission.generation.error = "Build cancelled before it started."
        return save_mission(mission, db_path)
    if not get_settings().build_async:
        raise LifecycleError(
            "This build is running synchronously; wait for it to finish "
            "(cancelling takes effect on background builds only)."
        )
    job_tracker.cancel_job(job.id, db_path)
    mission.progress_label = "Cancelling — the worker stops at the next step…"
    return save_mission(mission, db_path)


def build_application(
    mission_id: str,
    force: bool = False,
    db_path=None,
    idempotency_key: str | None = None,
) -> Mission:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if idempotency_key:
        seen = job_tracker.job_by_key(mission_id, idempotency_key, db_path)
        if seen is not None:
            # Same user action retried (network retry, double submit):
            # return current state without starting new work.
            return load_mission(mission_id, db_path)
    if mission.status in IN_PROGRESS_STATES:
        raise LifecycleError(f"A build is already in progress (mission is {mission.status.value}).")
    if mission.status in TERMINAL_VERIFICATION_STATES and not force:
        raise LifecycleError(
            f"Mission already has verification result {mission.status.value}; "
            "pass force=true to rebuild."
        )
    if mission.status != MissionStatus.APPROVED and mission.status not in TERMINAL_VERIFICATION_STATES:
        raise LifecycleError(
            f"Build needs an approved specification (mission is {mission.status.value})."
        )
    if mission.spec is None:
        raise LifecycleError("Build needs an approved specification first.")

    previous_status = mission.status
    spec = mission.spec
    if get_settings().build_async:
        return _enqueue_build(mission, idempotency_key, db_path)
    try:
        job = job_tracker.create_job(
            mission_id, JobKind.BUILD,
            idempotency_key=idempotency_key, db_path=db_path,
        )
    except sqlite3.IntegrityError as exc:
        if idempotency_key:
            seen = job_tracker.job_by_key(mission_id, idempotency_key, db_path)
            if seen is not None:
                # Lost the atomic race with our own duplicate: replay.
                return load_mission(mission_id, db_path)
        raise LifecycleError(
            "A build is already in progress for this mission."
        ) from exc
    mission.status = MissionStatus.BUILDING
    mission.error = ""
    mission.progress_step = "generating"
    mission.progress_label = "Generating application files with Muse Spark…"
    record = GenerationRecord(status=GenerationStatus.GENERATING, started_at=utc_now_iso())
    mission.generation = record
    save_mission(mission, db_path)

    started = time.monotonic()
    try:
        output, usage, model_id = generate_application(spec)
    except (SparkError, GenerationValidationError, ValidationError) as exc:
        record.status = GenerationStatus.FAILED
        record.completed_at = utc_now_iso()
        record.error = f"Generation failed: {exc}"[:2000]
        record.error_class = type(exc).__name__
        mission.generation = record
        mission.status = previous_status
        mission.error = record.error
        mission.progress_step = ""
        mission.progress_label = ""
        job_tracker.finish_job(
            job.id, JobStatus.FAILED,
            ms=job_tracker.monotonic_ms(started),
            error=record.error, error_class=record.error_class,
            db_path=db_path,
        )
        return save_mission(mission, db_path)

    merged = scaffold_files()
    for item in output.files:
        merged[f"{APP_ROOT}/{item.path}"] = item.content
    mission.files = merged
    record.status = GenerationStatus.GENERATED
    record.completed_at = utc_now_iso()
    record.model_id = model_id
    record.usage = dict(usage)
    record.summary = output.summary
    record.manifest = [item.path for item in output.files]
    record.feature_map = list(output.feature_map)
    record.unimplemented = list(output.unimplemented)
    record.assumptions = list(output.assumptions)
    mission.generation = record
    save_mission(mission, db_path)

    try:
        mission = run_mission_verification(mission_id, db_path=db_path)
    except Exception as exc:
        job_tracker.finish_job(
            job.id, JobStatus.FAILED,
            ms=job_tracker.monotonic_ms(started),
            usage=dict(usage),
            error=f"Verification crashed: {exc}"[:2000],
            error_class=type(exc).__name__,
            db_path=db_path,
        )
        raise
    if mission.verification is not None:
        mission.verification.coverage = build_coverage_report(
            spec, output, mission.verification.e2b
        )
        save_mission(mission, db_path)
    job_tracker.finish_job(
        job.id, JobStatus.SUCCEEDED,
        ms=job_tracker.monotonic_ms(started),
        usage=dict(usage),
        db_path=db_path,
    )
    return mission
