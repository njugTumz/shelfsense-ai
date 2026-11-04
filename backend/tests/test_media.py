"""Keyless tests: upload validation, staged analysis, evidence synthesis.

Run from backend/: python -m pytest tests/test_media.py
ffmpeg runs locally (no network); provider stages are stubbed here and
proven live by tests/test_media_live.py.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models.interview import EvidenceCategory
from app.models.media import (
    FrameRecord,
    MediaRecord,
    MediaStatus,
    TranscriptRecord,
    TranscriptTurn,
    VisualHypothesis,
    VisualObservation,
)


@pytest.fixture()
def env_paths(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    media = tmp_path / "media"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(media))
    get_settings.cache_clear()
    yield db, media
    get_settings.cache_clear()


def make_video(path: Path) -> Path:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-pix_fmt", "yuv420p", "-shortest", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()[:300]
    return path


def _create(client) -> str:
    response = client.post("/api/missions", json={"name": "t"})
    assert response.status_code == 201
    return response.json()["id"]


def test_upload_rejects_bad_type_size_and_empty(env_paths) -> None:
    client = TestClient(app)
    mission_id = _create(client)
    bad = client.post(
        f"/api/missions/{mission_id}/video",
        files={"file": ("evil.txt", b"nope", "text/plain")},
    )
    assert bad.status_code == 415
    big = client.post(
        f"/api/missions/{mission_id}/video",
        files={"file": ("big.mp4", b"x" * 1024, "video/mp4")},
    )
    # 1KB is fine on size but unreadable as video
    assert big.status_code == 400
    empty = client.post(
        f"/api/missions/{mission_id}/video",
        files={"file": ("empty.mp4", b"", "video/mp4")},
    )
    assert empty.status_code == 400
    assert client.post("/api/missions/nope/video", files={"file": ("a.mp4", b"x")}).status_code == 404


def test_upload_enforces_size_cap(env_paths, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
    get_settings.cache_clear()
    client = TestClient(app)
    mission_id = _create(client)
    response = client.post(
        f"/api/missions/{mission_id}/video",
        files={"file": ("big.mp4", b"x" * 1_100_000, "video/mp4")},
    )
    assert response.status_code == 413


def test_upload_stores_safely_and_probes(env_paths, tmp_path) -> None:
    from app.core.store import load_mission

    client = TestClient(app)
    mission_id = _create(client)
    video = make_video(tmp_path / "src.mp4")
    response = client.post(
        f"/api/missions/{mission_id}/video",
        files={"file": ("../../traversal.mp4", video.read_bytes(), "video/mp4")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "VIDEO_UPLOADED"
    media = body["media"]
    assert media["original_filename"] == "traversal.mp4"
    assert ".." not in media["video_path"]
    assert 1.5 < media["duration_s"] < 2.5
    assert media["width"] == 320 and media["height"] == 240
    stored = env_paths[1] / media["video_path"]
    assert stored.exists() and stored.parent.name == mission_id

    again = client.post(
        f"/api/missions/{mission_id}/video",
        files={"file": ("second.mp4", video.read_bytes(), "video/mp4")},
    )
    assert again.status_code == 409
    assert load_mission(mission_id, env_paths[0]) is not None


def test_analyze_requires_upload(env_paths) -> None:
    client = TestClient(app)
    mission_id = _create(client)
    assert client.post(f"/api/missions/{mission_id}/analyze").status_code == 409
    assert client.get(f"/api/missions/{mission_id}/media").status_code == 404
    assert client.get(f"/api/missions/{mission_id}/media/frames/f1").status_code == 404
    assert client.get("/api/missions/nope/media/frames/f1").status_code == 404


def _seed_uploaded(db, mission_id="m1"):
    from app.core.store import save_mission
    from app.models.mission import Mission, MissionStatus

    mission = Mission(id=mission_id, status=MissionStatus.VIDEO_UPLOADED)
    mission.media = MediaRecord(mission_id=mission_id, duration_s=2.0, width=320, height=240)
    return save_mission(mission, db)


def test_process_resumes_from_first_incomplete_stage(env_paths, monkeypatch) -> None:
    from app.services import media as media_module

    db, _ = env_paths
    mission = _seed_uploaded(db)
    calls: list[str] = []

    def fake_extract(media):
        calls.append("extract")
        media.frames = [FrameRecord(id=f"f{i}", timestamp_s=float(i)) for i in range(1, 7)]
        return media

    def fake_transcribe(media):
        calls.append("transcribe")
        media.transcript = TranscriptRecord(text="hello", turns=[])
        return media

    def fake_vision(media):
        calls.append("vision")
        media.vision_model_id = "stub"
        return media

    monkeypatch.setattr(media_module, "run_extraction", fake_extract)
    monkeypatch.setattr(media_module, "run_transcription", fake_transcribe)
    monkeypatch.setattr(media_module, "run_visual_analysis", fake_vision)

    # Transcript already present: extraction + vision run, transcription skipped.
    mission.media.transcript = TranscriptRecord(text="seeded", turns=[])
    from app.core.store import save_mission

    save_mission(mission, db)
    result = media_module.process_media("m1", db_path=db)
    assert calls == ["extract", "vision"]
    assert result.media.status.value == "ready"
    assert any(e.category == EvidenceCategory.STATED for e in result.evidence)

    # Ready is idempotent: nothing re-runs.
    calls.clear()
    again = media_module.process_media("m1", db_path=db)
    assert calls == []
    assert again.media.status.value == "ready"


def test_stage_failure_and_retry(env_paths, monkeypatch) -> None:
    from app.services import media as media_module

    db, _ = env_paths
    _seed_uploaded(db)
    attempts: list[str] = []

    def flaky(media):
        attempts.append("extract")
        if len(attempts) == 1:
            raise media_module.MediaProcessingError("ffmpeg exploded")
        media.frames = [FrameRecord(id=f"f{i}", timestamp_s=float(i)) for i in range(1, 7)]
        return media

    def ok_transcribe(media):
        media.transcript = TranscriptRecord(text="t", turns=[])
        return media

    def ok_vision(media):
        media.vision_model_id = "stub"
        return media

    monkeypatch.setattr(media_module, "run_extraction", flaky)
    monkeypatch.setattr(media_module, "run_transcription", ok_transcribe)
    monkeypatch.setattr(media_module, "run_visual_analysis", ok_vision)

    failed = media_module.process_media("m1", db_path=db)
    assert failed.media.status.value == "failed"
    assert "ffmpeg exploded" in failed.media.error
    assert failed.status.value == "ANALYZING"

    retried = media_module.process_media("m1", db_path=db)
    assert retried.media.status.value == "ready"
    assert retried.media.error == ""


def test_vision_validation_and_dropped_refs(env_paths, monkeypatch, tmp_path) -> None:
    from app.services import media as media_module

    db, media_dir = env_paths
    mission = _seed_uploaded(db)
    mission.media.frames = [FrameRecord(id="f1", timestamp_s=1.0, path="x")]
    (media_dir / "missions" / "m1").mkdir(parents=True, exist_ok=True)
    (media_dir / "missions" / "m1" / "frame.jpg").write_bytes(b"fakejpg")
    mission.media.frames[0].path = "missions/m1/frame.jpg"
    from app.core.store import save_mission

    save_mission(mission, db)

    def bad_shape(**kwargs):
        return ({"observations": "not-a-list", "hypotheses": []}, {})

    monkeypatch.setattr(media_module, "responses_structured_multimodal", bad_shape)
    with pytest.raises(media_module.MediaProcessingError):
        media_module.run_visual_analysis(mission.media)

    def bad_refs(**kwargs):
        return ({
            "observations": [
                {"observation": "ghost", "frame_ids": ["f99"], "confidence": "high"},
                {"observation": "real", "frame_ids": ["f1"], "confidence": "low"},
            ],
            "hypotheses": [
                {"hypothesis": "lost", "basis_frame_ids": ["f99"], "confidence": "low"},
            ],
        }, {"input_tokens": 1})

    monkeypatch.setattr(media_module, "responses_structured_multimodal", bad_refs)
    result = media_module.run_visual_analysis(mission.media)
    assert [o.observation for o in result.observations] == ["real"]
    assert result.hypotheses == []
    assert any("Dropped 1 observation" in note for note in result.notes)


def test_synthesize_categorizes_with_sources(env_paths) -> None:
    from app.core.store import save_mission
    from app.services import media as media_module

    db, _ = env_paths
    mission = _seed_uploaded(db)
    mission.media.frames = [FrameRecord(id="f1", timestamp_s=12.4), FrameRecord(id="f2", timestamp_s=20.0)]
    mission.media.transcript = TranscriptRecord(text="full", turns=[
        TranscriptTurn(index=1, start_ms=34000, end_ms=41000, transcript="Slips get lost."),
        TranscriptTurn(index=2, start_ms=41000, end_ms=45000, transcript="  "),
    ])
    mission.media.observations = [VisualObservation(observation="Counter visible.", frame_ids=["f1"])]
    mission.media.hypotheses = [VisualHypothesis(hypothesis="Rush chaos.", basis_frame_ids=["f1", "f2"])]
    save_mission(mission, db)

    result = media_module.synthesize_evidence(mission)
    by_cat: dict[str, list] = {}
    for item in result.evidence:
        by_cat.setdefault(item.category.value, []).append(item)
    assert len(by_cat["OBSERVED"]) == 1
    assert "frame f1 @ 12.4s" in by_cat["OBSERVED"][0].source
    assert len(by_cat["STATED"]) == 1
    assert "turn 1 @ 34-41s" in by_cat["STATED"][0].source
    assert len(by_cat["INFERRED"]) == 1
    assert "f1" in by_cat["INFERRED"][0].source and "f2" in by_cat["INFERRED"][0].source
    assert result.media.status.value == "ready"


def test_interview_guards_for_media_missions(env_paths, monkeypatch) -> None:
    from app.models.mission import MissionStatus

    from app.core.store import save_mission
    from app.services import interview as interview_module

    monkeypatch.setattr(interview_module, "_ask_model", lambda mission: (True, "Q?", "R"))
    client = TestClient(app)

    ready = _seed_uploaded(env_paths[0], "ready")
    ready.status = MissionStatus.ANALYZING
    ready.media.status = MediaStatus.READY
    ready.evidence = []
    save_mission(ready, env_paths[0])
    refused = client.post(
        f"/api/missions/{ready.id}/interview/start",
        json={"owner_context": "ctx", "use_demo_evidence": True},
    )
    assert refused.status_code == 409
    started = client.post(
        f"/api/missions/{ready.id}/interview/start",
        json={"owner_context": "Paper slips vanish.", "use_demo_evidence": False},
    )
    assert started.status_code == 200
    assert started.json()["status"] == "INTERVIEWING"
    assert any(e["category"] == "STATED" for e in started.json()["evidence"])

    pending = _seed_uploaded(env_paths[0], "pending")
    pending.status = MissionStatus.ANALYZING
    save_mission(pending, env_paths[0])
    blocked = client.post(
        f"/api/missions/{pending.id}/interview/start",
        json={"owner_context": "ctx", "use_demo_evidence": False},
    )
    assert blocked.status_code == 409


def test_frame_serving_and_path_safety(env_paths) -> None:
    from app.models.mission import MissionStatus

    from app.core.store import save_mission
    from app.services import media as media_module

    db, media_dir = env_paths
    mission = _seed_uploaded(db)
    mission.media.frames = [FrameRecord(id="f1", timestamp_s=1.0, path="missions/m1/frame_01.jpg")]
    frame_path = media_dir / "missions" / "m1" / "frame_01.jpg"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"\xff\xd8fakejpg")
    save_mission(mission, db)

    client = TestClient(app)
    ok = client.get(f"/api/missions/{mission.id}/media/frames/f1")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/missions/{mission.id}/media/frames/f9").status_code == 404
    assert client.get(f"/api/missions/{mission.id}/media/frames/..%2Fvideo").status_code == 404
    with pytest.raises(Exception):
        media_module.resolve_media_path("../escape.mp4")
    with pytest.raises(Exception):
        media_module.resolve_media_path("/abs/path.mp4")


def test_media_persistence_roundtrip(env_paths) -> None:
    from app.core.store import load_mission

    db, _ = env_paths
    mission = _seed_uploaded(db)
    mission.media.frames = [FrameRecord(id="f1", timestamp_s=1.5)]
    mission.media.transcript = TranscriptRecord(text="t", turns=[])
    from app.core.store import save_mission

    save_mission(mission, db)
    reloaded = load_mission(mission.id, db)
    assert reloaded is not None and reloaded.media is not None
    assert reloaded.media.frames[0].timestamp_s == 1.5
    assert reloaded.media.transcript is not None and reloaded.media.transcript.text == "t"


def test_real_extraction_with_local_ffmpeg(env_paths, tmp_path) -> None:
    from app.services import media as media_module

    db, _ = env_paths
    mission = _seed_uploaded(db)
    video = make_video(tmp_path / "tiny.mp4")
    stored = env_paths[1] / "missions" / "m1"
    stored.mkdir(parents=True, exist_ok=True)
    target = stored / "video.mp4"
    target.write_bytes(video.read_bytes())
    mission.media.video_path = "missions/m1/video.mp4"
    mission.media.duration_s = 2.0
    mission.media.width = 320
    mission.media.height = 240

    result = media_module.run_extraction(mission.media)
    assert len(result.frames) == 6
    stamps = [f.timestamp_s for f in result.frames]
    assert stamps == sorted(stamps)
    assert all(0 < s < 2.0 for s in stamps)
    assert all((env_paths[1] / f.path).stat().st_size > 0 for f in result.frames)
    assert (stored / "audio_24k_mono.wav").stat().st_size > 0
