"""Keyless tests: resilience budgets — every model call site passes an
explicit read timeout, captured at runtime through faked providers.

Run from backend/: python -m pytest tests/test_resilience.py
No network use; all provider calls are simulated.
"""

import json

import pytest

from app.core.config import get_settings


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("META_MODEL_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("META_MODEL_BASE_URL", "https://models.test/v1")
    monkeypatch.setenv("MUSE_SPARK_MODEL_ID", "muse-spark-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _spec():
    from app.models.interview import StructuredRequirements

    return StructuredRequirements.model_validate({
        "business_context": "ctx",
        "problem_statement": "A business problem.",
        "functional_requirements": [{"id": "FR-1", "requirement": "Do the thing."}],
    })


def _developer_output():
    return {
        "summary": "s",
        "files": [{"path": "src/App.tsx",
                   "content": "export default function App() { return null; }"}],
        "feature_map": [],
        "unimplemented": [],
        "assumptions": [],
    }


def test_developer_uses_600s_read_budget(configured, monkeypatch) -> None:
    from app.services import developer as developer_module
    from app.services.developer import DEVELOPER_TIMEOUT_S, generate_application

    assert DEVELOPER_TIMEOUT_S == 600.0
    seen = []

    def fake(**kwargs):
        seen.append(kwargs)
        return _developer_output(), {"input_tokens": 5, "output_tokens": 9}

    monkeypatch.setattr(developer_module, "responses_structured", fake)
    generate_application(_spec())
    assert seen and all(k.get("timeout_s") == 600.0 for k in seen), seen


def test_repair_uses_600s_read_budget(configured, monkeypatch) -> None:
    from app.services import developer as developer_module
    from app.services.developer import repair_application_files

    seen = []

    def fake(**kwargs):
        seen.append(kwargs)
        return _developer_output(), {"input_tokens": 5, "output_tokens": 9}

    monkeypatch.setattr(developer_module, "responses_structured", fake)
    repair_application_files(_spec(), {"src/App.tsx": "old"}, "build failed")
    assert seen and all(k.get("timeout_s") == 600.0 for k in seen), seen


def test_requirements_uses_300s_read_budget(configured, monkeypatch) -> None:
    from app.services import requirements as requirements_module
    from app.services.requirements import REQUIREMENTS_TIMEOUT_S

    assert REQUIREMENTS_TIMEOUT_S == 300.0
    seen = []

    def fake(**kwargs):
        seen.append(kwargs)
        return {
            "business_context": "ctx",
            "problem_statement": "p",
            "functional_requirements": [{"id": "FR-1", "requirement": "r"}],
        }, {}

    monkeypatch.setattr(requirements_module, "responses_structured", fake)
    requirements_module._generate_validated("plan this")
    assert seen and all(k.get("timeout_s") == 300.0 for k in seen), seen


def test_audit_uses_300s_read_budget(configured, monkeypatch) -> None:
    from app.services import auditors as auditors_module
    from app.services.auditors import (
        AUDIT_TIMEOUT_S,
        AuditEvidence,
        SparkAdversarialAuditor,
    )

    assert AUDIT_TIMEOUT_S == 300.0
    seen = []

    def fake(**kwargs):
        seen.append(kwargs)
        return {"verdict": "PASS", "summary": "fine", "findings": []}, {}

    monkeypatch.setattr(auditors_module, "responses_structured", fake)
    result = SparkAdversarialAuditor().audit(AuditEvidence())
    assert result.completed is True
    assert seen and all(k.get("timeout_s") == 300.0 for k in seen), seen


def test_interview_uses_180s_read_budget(configured, monkeypatch) -> None:
    from app.models.mission import Mission, MissionStatus
    from app.services import interview as interview_module
    from app.services.interview import INTERVIEW_TIMEOUT_S

    assert INTERVIEW_TIMEOUT_S == 180.0
    seen = []

    def fake(**kwargs):
        seen.append(kwargs)
        return {"needs_more": False, "question": "", "rationale": ""}, {}

    monkeypatch.setattr(interview_module, "responses_structured", fake)
    mission = Mission(id="m", status=MissionStatus.ANALYZING)
    interview_module._ask_model(mission)
    assert seen and all(k.get("timeout_s") == 180.0 for k in seen), seen


def test_vision_uses_300s_read_budget(configured, monkeypatch, tmp_path) -> None:
    from app.models.media import FrameRecord, MediaRecord
    from app.services import media as media_module
    from app.services.media import VISION_TIMEOUT_S

    assert VISION_TIMEOUT_S == 300.0
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    frame_dir = tmp_path / "uploads" / "missions" / "m"
    frame_dir.mkdir(parents=True)
    (frame_dir / "frame_01.jpg").write_bytes(b"\xff\xd8fakejpeg")
    media = MediaRecord(
        mission_id="m",
        frames=[FrameRecord(id="f1", timestamp_s=1.0,
                            path="missions/m/frame_01.jpg")],
    )
    seen = []

    def fake(**kwargs):
        seen.append(kwargs)
        return {"observations": [], "hypotheses": []}, {}

    monkeypatch.setattr(media_module, "responses_structured_multimodal", fake)
    media_module.run_visual_analysis(media)
    assert seen and all(k.get("timeout_s") == 300.0 for k in seen), seen


def test_job_lifecycle_round_trip(tmp_path) -> None:
    import sqlite3

    from app.core.store import init_db, load_active_job, load_job
    from app.models.jobs import JobKind, JobStatus
    from app.services import jobs as jobs_module

    db = init_db(tmp_path / "jobs.db")
    job = jobs_module.create_job("m1", JobKind.BUILD, db_path=db)
    assert job.status == JobStatus.RUNNING
    assert load_active_job("m1", "build", path=db).id == job.id
    with pytest.raises(sqlite3.IntegrityError):
        jobs_module.create_job("m1", JobKind.BUILD, db_path=db)
    done = jobs_module.finish_job(
        job.id, JobStatus.FAILED, ms=1500,
        usage={"input_tokens": 10, "output_tokens": 5},
        error="boom", error_class="SparkTransientError", db_path=db,
    )
    assert done.status == JobStatus.FAILED
    assert done.error_class == "SparkTransientError"
    assert done.usage == {"input_tokens": 10, "output_tokens": 5}
    assert len(done.attempts) == 1 and done.attempts[0].ms == 1500
    assert load_active_job("m1", "build", path=db) is None
    assert load_job(job.id, path=db).status == JobStatus.FAILED
    # Terminal job releases the lock: a retry may create a new one.
    retry = jobs_module.create_job("m1", JobKind.BUILD, db_path=db)
    assert retry.id != job.id


def test_jobs_table_migrates_existing_db(tmp_path) -> None:
    import sqlite3

    from app.core.store import init_db

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE jobs (
                id TEXT PRIMARY KEY, mission_id TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL,
                updated_at TEXT NOT NULL, data TEXT NOT NULL
            )"""
        )
    init_db(db)
    with sqlite3.connect(db) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "idempotency_key" in cols
        indexes = [r[1] for r in conn.execute("PRAGMA index_list(jobs)").fetchall()]
        assert "idx_jobs_active" in indexes
        assert "idx_jobs_idempotency" in indexes
    # Second init is a no-op (duplicate column + indexes tolerated).
    init_db(db)


def test_job_attempts_accumulate_usage(tmp_path) -> None:
    from app.core.store import init_db, load_job
    from app.models.jobs import JobKind, JobStatus
    from app.services import jobs as jobs_module

    db = init_db(tmp_path / "jobs.db")
    job = jobs_module.create_job("m1", JobKind.BUILD, db_path=db)
    jobs_module.record_attempt(job.id, 100, {"input_tokens": 10}, db_path=db)
    jobs_module.finish_job(
        job.id, JobStatus.SUCCEEDED, ms=200,
        usage={"input_tokens": 20, "output_tokens": 5}, db_path=db,
    )
    done = load_job(job.id, path=db)
    assert done.attempt == 2
    assert len(done.attempts) == 2
    assert done.usage == {"input_tokens": 30, "output_tokens": 5}


def test_build_failure_records_job_with_error_class(tmp_path, monkeypatch) -> None:
    from app.core.store import load_job, save_mission
    from app.models.mission import Mission, MissionStatus
    from app.services import build as build_module
    from app.services import jobs as jobs_module
    from app.services.spark_client import SparkTransientError

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    get_settings.cache_clear()
    mission = Mission(id="b1", status=MissionStatus.APPROVED, spec=_spec())
    save_mission(mission)

    def boom(spec):
        raise SparkTransientError("TRANSPORT ERROR: ReadTimeout: slow")

    monkeypatch.setattr(build_module, "generate_application", boom)
    result = build_module.build_application("b1")
    assert result.status == MissionStatus.APPROVED
    assert result.generation.status.value == "failed"
    assert result.generation.error_class == "SparkTransientError"
    assert jobs_module.active_job("b1") is None
    get_settings.cache_clear()


def test_build_success_records_job_with_usage(tmp_path, monkeypatch) -> None:
    from app.core.store import load_job, save_mission
    from app.models.mission import Mission, MissionStatus
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
    from app.services import build as build_module
    from app.services import jobs as jobs_module

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    get_settings.cache_clear()
    mission = Mission(id="b2", status=MissionStatus.APPROVED, spec=_spec())
    save_mission(mission)

    def fake_ok(spec):
        from app.models.generation import GenerationOutput

        return (
            GenerationOutput.model_validate(_developer_output()),
            {"input_tokens": 5, "output_tokens": 9},
            "muse-spark-test",
        )

    def fake_verify(mission_id, db_path=None):
        from app.core.store import load_mission

        current = load_mission(mission_id)
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

    monkeypatch.setattr(build_module, "generate_application", fake_ok)
    monkeypatch.setattr(build_module, "run_mission_verification", fake_verify)
    result = build_module.build_application("b2")
    assert result.status == MissionStatus.BUILDING  # fake verify keeps status
    assert result.generation.status.value == "generated"
    assert jobs_module.active_job("b2") is None
    get_settings.cache_clear()


def _seed_approved(db_path, mission_id="k1"):
    from app.core.store import save_mission
    from app.models.mission import Mission, MissionStatus

    mission = Mission(id=mission_id, status=MissionStatus.APPROVED, spec=_spec())
    save_mission(mission, db_path)
    return mission


def _fake_ok():
    from app.models.generation import GenerationOutput

    return (
        GenerationOutput.model_validate(_developer_output()),
        {"input_tokens": 5, "output_tokens": 9},
        "muse-spark-test",
    )


def test_same_key_replays_without_new_work(tmp_path, monkeypatch) -> None:
    from app.core.store import init_db
    from app.services import build as build_module

    db = init_db(tmp_path / "test.db")
    _seed_approved(db)
    calls = []

    def fake_generate(spec):
        calls.append(1)
        return _fake_ok()

    def fake_verify(mission_id, db_path=None):
        from app.core.store import load_mission

        return load_mission(mission_id, db_path)

    monkeypatch.setattr(build_module, "generate_application", fake_generate)
    monkeypatch.setattr(build_module, "run_mission_verification", fake_verify)
    first = build_module.build_application("k1", db_path=db, idempotency_key="key-1")
    second = build_module.build_application("k1", db_path=db, idempotency_key="key-1")
    assert first.id == second.id == "k1"
    assert len(calls) == 1


def test_fresh_key_after_terminal_starts_new_work(tmp_path, monkeypatch) -> None:
    from app.core.store import init_db
    from app.services import build as build_module

    db = init_db(tmp_path / "test.db")
    _seed_approved(db)
    calls = []

    def fake_generate(spec):
        calls.append(1)
        return _fake_ok()

    def fake_verify(mission_id, db_path=None):
        from app.core.store import load_mission

        return load_mission(mission_id, db_path)

    monkeypatch.setattr(build_module, "generate_application", fake_generate)
    monkeypatch.setattr(build_module, "run_mission_verification", fake_verify)
    build_module.build_application("k1", db_path=db, idempotency_key="key-1")
    # Mission is left BUILDING by the fake verify; restore APPROVED like a
    # completed-then-reset flow would before the deliberate retry.
    from app.core.store import load_mission, save_mission
    from app.models.mission import MissionStatus

    mission = load_mission("k1", db)
    mission.status = MissionStatus.APPROVED
    save_mission(mission, db)
    build_module.build_application("k1", db_path=db, idempotency_key="key-2")
    assert len(calls) == 2


def test_concurrent_build_second_gets_conflict(tmp_path, monkeypatch) -> None:
    import threading

    from app.core.store import init_db
    from app.services import build as build_module
    from app.services.interview import LifecycleError

    db = init_db(tmp_path / "test.db")
    _seed_approved(db)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fake_generate(spec):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=10.0)
        return _fake_ok()

    def fake_verify(mission_id, db_path=None):
        from app.core.store import load_mission

        return load_mission(mission_id, db_path)

    monkeypatch.setattr(build_module, "generate_application", fake_generate)
    monkeypatch.setattr(build_module, "run_mission_verification", fake_verify)
    errors = []

    def second():
        try:
            build_module.build_application("k1", db_path=db)
        except LifecycleError as exc:
            errors.append(exc)

    first = threading.Thread(
        target=lambda: build_module.build_application("k1", db_path=db))
    first.start()
    assert entered.wait(timeout=10.0)
    worker = threading.Thread(target=second)
    worker.start()
    worker.join(timeout=10.0)
    release.set()
    first.join(timeout=10.0)
    assert len(errors) == 1
    assert len(calls) == 1


def test_unique_index_blocks_double_active_job(tmp_path) -> None:
    from app.core.store import init_db
    from app.models.jobs import JobKind
    from app.services import build as build_module
    from app.services import jobs as jobs_module
    from app.services.interview import LifecycleError

    db = init_db(tmp_path / "test.db")
    _seed_approved(db)
    jobs_module.create_job("k1", JobKind.BUILD, db_path=db)
    try:
        build_module.build_application("k1", db_path=db)
    except LifecycleError as exc:
        assert "already in progress" in str(exc)
    else:
        raise AssertionError("expected LifecycleError for double active job")


def test_build_api_honors_idempotency_key(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import build as build_module

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    get_settings.cache_clear()
    client = TestClient(app)
    mission_id = client.post("/api/missions", json={"name": "k"}).json()["id"]
    from app.core.store import load_mission, save_mission
    from app.models.mission import MissionStatus

    mission = load_mission(mission_id)
    mission.status = MissionStatus.APPROVED
    mission.spec = _spec()
    save_mission(mission)

    calls = []

    def fake_generate(spec):
        calls.append(1)
        return _fake_ok()

    def fake_verify(mid, db_path=None):
        from app.core.store import load_mission as _load

        return _load(mid, db_path)

    monkeypatch.setattr(build_module, "generate_application", fake_generate)
    monkeypatch.setattr(build_module, "run_mission_verification", fake_verify)
    headers = {"Idempotency-Key": "api-key-1"}
    first = client.post(f"/api/missions/{mission_id}/build", json={}, headers=headers)
    second = client.post(f"/api/missions/{mission_id}/build", json={}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1
    get_settings.cache_clear()


def test_transcription_splits_transport_timeout(configured, monkeypatch, tmp_path) -> None:
    from app.models.media import MediaRecord
    from app.services import media as media_module

    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    audio_dir = tmp_path / "uploads" / "missions" / "m"
    audio_dir.mkdir(parents=True)
    (audio_dir / "audio_24k_mono.wav").write_bytes(b"RIFFfak cannon")
    media = MediaRecord(mission_id="m")
    seen = {}

    class _Resp:
        status_code = 200
        text = json.dumps({"transcript": "hello", "turns": []})

        def json(self):
            return {"transcript": "hello", "turns": []}

    def fake_post(url, headers=None, json=None, files=None, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(media_module.httpx, "post", fake_post)
    media_module.run_transcription(media)
    transport = seen["timeout"]
    assert transport.connect == 10.0
    assert transport.read == 300.0
    assert transport.write == 120.0
