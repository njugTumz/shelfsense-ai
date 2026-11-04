"""Keyless tests: background worker, checkpoints, cancel, crash recovery.

Run from backend/: python -m pytest tests/test_worker.py
All provider and E2B calls are simulated; the worker is exercised for
real (threads, atomic claims, heartbeats, stage boundaries).
"""

import threading
import time

import pytest

from app.core.config import get_settings


@pytest.fixture()
def async_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("BUILD_ASYNC", "true")
    get_settings.cache_clear()
    yield tmp_path / "test.db"
    get_settings.cache_clear()


def _spec():
    from app.models.interview import StructuredRequirements

    return StructuredRequirements.model_validate({
        "business_context": "ctx",
        "problem_statement": "A business problem.",
        "functional_requirements": [{"id": "FR-1", "requirement": "Do the thing."}],
    })


def _output_tuple():
    from app.models.generation import GenerationOutput

    return (
        GenerationOutput.model_validate({
            "summary": "s",
            "files": [{"path": "src/App.tsx",
                       "content": "export default function App() { return null; }"}],
            "feature_map": [],
            "unimplemented": [],
            "assumptions": [],
        }),
        {"input_tokens": 5, "output_tokens": 9},
        "muse-spark-test",
    )


def _seed_approved(db, mission_id="w1"):
    from app.core.store import save_mission
    from app.models.mission import Mission, MissionStatus

    mission = Mission(id=mission_id, status=MissionStatus.APPROVED, spec=_spec())
    save_mission(mission, db)
    return mission


def _fake_verify_ok(mission_id, db_path=None):
    from app.core.store import load_mission
    from app.models.verification import (
        AuditResult,
        AuditVerdict,
        CheckCategory,
        CheckStatus,
        E2BVerification,
        VerificationCheck,
        VerificationResult,
        VerificationStatus,
    )

    current = load_mission(mission_id, db_path)
    current.verification = VerificationResult(
        status=VerificationStatus.VERIFIED,
        reason="",
        e2b=E2BVerification(
            available=True, passed=True,
            checks=[VerificationCheck(
                name="c", category=CheckCategory.BUILD,
                status=CheckStatus.PASS, required=True,
            )],
        ),
        audit=AuditResult(
            provider="spark_adversarial", available=True, completed=True,
            verdict=AuditVerdict.PASS,
        ),
    )
    return current


def test_enqueue_returns_building_and_worker_completes(async_db, monkeypatch) -> None:
    from app.core.store import load_mission
    from app.models.jobs import JobStatus
    from app.models.mission import MissionStatus
    from app.services import build as build_module
    from app.services import worker as worker_module

    _seed_approved(async_db)
    calls = []

    def fake_generate(spec):
        calls.append(1)
        return _output_tuple()

    monkeypatch.setattr(worker_module, "generate_application", fake_generate)
    monkeypatch.setattr(worker_module, "run_mission_verification", _fake_verify_ok)
    enqueued = build_module.build_application("w1", db_path=async_db)
    assert enqueued.status == MissionStatus.BUILDING
    assert enqueued.progress_step == "queued"

    worker = worker_module.Worker(async_db)
    assert worker.run_once() is True
    assert worker.run_once() is False  # queue drained
    assert len(calls) == 1

    mission = load_mission("w1", async_db)
    assert mission.verification is not None
    assert mission.verification.coverage is not None
    assert mission.verification.coverage.architecture is not None
    from app.services import jobs as jobs_module

    assert jobs_module.active_job("w1", db_path=async_db) is None


def test_worker_resume_skips_completed_generate(async_db, monkeypatch) -> None:
    """Crash after generate: resume must not call the model again."""
    from app.core.store import init_db, load_job, load_mission, save_job, save_mission
    from app.demo.restaurant_app import APP_ROOT
    from app.models.generation import GenerationRecord, GenerationStatus
    from app.models.jobs import JobKind, JobStatus
    from app.services import jobs as jobs_module
    from app.services import worker as worker_module

    init_db(async_db)
    mission = _seed_approved(async_db)
    mission.files = {f"{APP_ROOT}/src/App.tsx": "export default function App() { return null; }"}
    mission.generation = GenerationRecord(
        status=GenerationStatus.GENERATED, summary="s",
        manifest=["src/App.tsx"], usage={"input_tokens": 5, "output_tokens": 9},
    )
    save_mission(mission, async_db)
    job = jobs_module.create_job("w1", JobKind.BUILD, db_path=async_db)
    job.stages_done = ["generated"]
    save_job(job, async_db)

    def boom_generate(spec):
        raise AssertionError("generate must not run on resume")

    verify_calls = []
    orig_verify = _fake_verify_ok

    def counting_verify(mission_id, db_path=None):
        verify_calls.append(1)
        return orig_verify(mission_id, db_path)

    monkeypatch.setattr(worker_module, "generate_application", boom_generate)
    monkeypatch.setattr(worker_module, "run_mission_verification", counting_verify)
    done = worker_module.run_build_job(job, async_db)
    assert done.status == JobStatus.SUCCEEDED
    assert len(verify_calls) == 1
    assert load_mission("w1", async_db).verification is not None


def test_crash_recovery_requeues_and_finishes_once(async_db, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    from app.core.store import init_db, load_job, save_job
    from app.demo.restaurant_app import APP_ROOT
    from app.models.generation import GenerationRecord, GenerationStatus
    from app.models.jobs import JobKind, JobStatus
    from app.models.mission import MissionStatus
    from app.services import jobs as jobs_module
    from app.services import worker as worker_module
    from app.core.store import load_job

    init_db(async_db)
    mission = _seed_approved(async_db)
    mission.status = MissionStatus.BUILDING
    mission.files = {f"{APP_ROOT}/src/App.tsx": "x"}
    mission.generation = GenerationRecord(status=GenerationStatus.GENERATED,
                                          manifest=["src/App.tsx"])
    from app.core.store import save_mission

    save_mission(mission, async_db)
    job = jobs_module.create_job("w1", JobKind.BUILD, db_path=async_db)
    job.stages_done = ["generated"]
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    job.heartbeat_at = stale
    save_job(job, async_db)

    recovered = worker_module.recover_stale_state(async_db)
    assert recovered == {"requeued": 1, "restored": 0}
    reloaded = load_job(job.id, async_db)
    assert reloaded.status == JobStatus.QUEUED
    import sqlite3 as _sqlite3

    with _sqlite3.connect(async_db) as conn:
        assert conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job.id,)).fetchone()[0] == "queued"

    def boom_generate(spec):
        raise AssertionError("crashed run already generated; must not repeat")

    monkeypatch.setattr(worker_module, "generate_application", boom_generate)
    monkeypatch.setattr(worker_module, "run_mission_verification", _fake_verify_ok)
    worker = worker_module.Worker(async_db)
    assert worker.run_once() is True
    done = load_job(job.id, async_db)
    assert done.status == JobStatus.SUCCEEDED
    assert len(done.attempts) == 1  # this run's attempt only


def test_recovery_restores_stranded_mission(async_db) -> None:
    from app.core.store import init_db, load_mission, save_mission
    from app.models.mission import MissionStatus
    from app.services import worker as worker_module

    init_db(async_db)
    stuck = _seed_approved(async_db, "stuck")
    stuck.status = MissionStatus.RUNNING
    save_mission(stuck, async_db)
    fine = _seed_approved(async_db, "fine")
    fine.status = MissionStatus.FAILED
    save_mission(fine, async_db)

    recovered = worker_module.recover_stale_state(async_db)
    assert recovered == {"requeued": 0, "restored": 1}
    assert load_mission("stuck", async_db).status == MissionStatus.APPROVED
    assert "safe to retry" in load_mission("stuck", async_db).error
    assert load_mission("fine", async_db).status == MissionStatus.FAILED


def test_cancel_queued_never_starts(async_db) -> None:
    from app.core.store import load_mission
    from app.models.jobs import JobStatus
    from app.models.mission import MissionStatus
    from app.services import build as build_module
    from app.services import worker as worker_module

    _seed_approved(async_db)
    build_module.build_application("w1", db_path=async_db)
    cancelled = build_module.cancel_build("w1", db_path=async_db)
    assert cancelled.status == MissionStatus.APPROVED
    assert load_mission("w1", async_db).generation.status.value == "failed"
    worker = worker_module.Worker(async_db)
    assert worker.run_once() is False


def test_cancel_running_stops_at_stage_boundary(async_db, monkeypatch) -> None:
    from app.core.store import load_mission
    from app.models.jobs import JobStatus
    from app.models.mission import MissionStatus
    from app.services import build as build_module
    from app.services import jobs as jobs_module
    from app.services import worker as worker_module

    _seed_approved(async_db)
    entered = threading.Event()
    release = threading.Event()

    def fast_generate(spec):
        return _output_tuple()

    def slow_verify(mission_id, db_path=None):
        entered.set()
        assert release.wait(timeout=10.0)
        return _fake_verify_ok(mission_id, db_path)

    monkeypatch.setattr(worker_module, "generate_application", fast_generate)
    monkeypatch.setattr(worker_module, "run_mission_verification", slow_verify)
    build_module.build_application("w1", db_path=async_db)

    worker = worker_module.Worker(async_db)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert entered.wait(timeout=10.0)
    build_module.cancel_build("w1", db_path=async_db)
    release.set()
    thread.join(timeout=15.0)
    assert not thread.is_alive()

    mission = load_mission("w1", async_db)
    assert mission.status == MissionStatus.APPROVED
    assert "cancelled" in mission.error.lower()
    assert jobs_module.active_job("w1", db_path=async_db) is None


def test_cancel_without_job_conflicts(async_db) -> None:
    from app.services import build as build_module
    from app.services.interview import LifecycleError

    _seed_approved(async_db)
    try:
        build_module.cancel_build("w1", db_path=async_db)
    except LifecycleError as exc:
        assert "No active build" in str(exc)
    else:
        raise AssertionError("expected LifecycleError")


def test_claim_is_atomic(async_db) -> None:
    from app.models.jobs import JobKind, JobStatus
    from app.services import jobs as jobs_module
    from app.services import worker as worker_module

    _seed_approved(async_db)
    jobs_module.create_job("w1", JobKind.BUILD, status=JobStatus.QUEUED,
                           db_path=async_db)
    winners = []
    barrier = threading.Barrier(4)

    def racer():
        barrier.wait(timeout=10.0)
        job = worker_module.claim_next_job(async_db)
        if job is not None:
            winners.append(job.id)

    threads = [threading.Thread(target=racer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)
    assert len(winners) == 1
    from app.core.store import load_job

    assert load_job(winners[0], async_db).status == JobStatus.CLAIMED


def test_worker_serializes_two_missions(async_db, monkeypatch) -> None:
    from app.models.jobs import JobStatus
    from app.services import build as build_module
    from app.services import worker as worker_module

    _seed_approved(async_db, "s1")
    _seed_approved(async_db, "s2")
    events = []

    def slow_generate(spec):
        events.append(("start", threading.get_ident()))
        time.sleep(0.3)
        events.append(("end", threading.get_ident()))
        return _output_tuple()

    monkeypatch.setattr(worker_module, "generate_application", slow_generate)
    monkeypatch.setattr(worker_module, "run_mission_verification", _fake_verify_ok)
    build_module.build_application("s1", db_path=async_db)
    build_module.build_application("s2", db_path=async_db)

    worker = worker_module.Worker(async_db)
    worker.start()
    try:
        deadline = time.monotonic() + 30.0
        from app.core.store import load_job
        from app.services import jobs as jobs_module

        while time.monotonic() < deadline:
            remaining = [
                m for m in ("s1", "s2")
                if jobs_module.active_job(m, db_path=async_db) is not None
            ]
            if not remaining:
                break
            time.sleep(0.2)
        assert remaining == []
    finally:
        worker.stop()
    kinds = [kind for kind, _ in events]
    assert kinds == ["start", "end", "start", "end"], events
    assert len({ident for _, ident in events}) == 1


def test_worker_generation_failure_mirrors_sync(async_db, monkeypatch) -> None:
    from app.core.store import load_mission
    from app.models.jobs import JobStatus
    from app.models.mission import MissionStatus
    from app.services import build as build_module
    from app.services import worker as worker_module
    from app.services.spark_client import SparkTransientError

    _seed_approved(async_db)

    def boom(spec):
        raise SparkTransientError("TRANSPORT ERROR: ReadTimeout: slow")

    monkeypatch.setattr(worker_module, "generate_application", boom)
    build_module.build_application("w1", db_path=async_db)
    worker = worker_module.Worker(async_db)
    assert worker.run_once() is True
    mission = load_mission("w1", async_db)
    assert mission.status == MissionStatus.APPROVED
    assert mission.generation.status.value == "failed"
    assert mission.generation.error_class == "SparkTransientError"

    from app.core.store import load_job
    from app.services import jobs as jobs_module

    assert jobs_module.active_job("w1", db_path=async_db) is None


def test_cancel_endpoint_round_trip(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import build as build_module

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("BUILD_ASYNC", "true")
    get_settings.cache_clear()
    try:
        client = TestClient(app)
        mission_id = client.post("/api/missions", json={"name": "c"}).json()["id"]
        from app.core.store import load_mission, save_mission
        from app.models.mission import MissionStatus

        mission = load_mission(mission_id)
        mission.status = MissionStatus.APPROVED
        mission.spec = _spec()
        save_mission(mission)
        queued = client.post(f"/api/missions/{mission_id}/build", json={})
        assert queued.status_code == 200
        assert queued.json()["status"] == "BUILDING"
        cancelled = client.post(f"/api/missions/{mission_id}/build/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "APPROVED"
        again = client.post(f"/api/missions/{mission_id}/build/cancel")
        assert again.status_code == 409
    finally:
        get_settings.cache_clear()
