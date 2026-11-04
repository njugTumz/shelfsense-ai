"""Live video->evidence test: real ffmpeg, transcription, Spark vision.

Opt-in only: minutes and provider spend (transcription + vision).
Run from backend/:

    FIELD_LIVE_VERIFY=1 python -m pytest tests/test_media_live.py -s

Skips by default. Uses the committed sample restaurant video and drives
the service layer directly.
"""

import os
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.store import load_mission, save_mission
from app.models.interview import EvidenceCategory
from app.models.mission import Mission, MissionStatus
from app.services.interview import start_interview
from app.services.media import process_media, save_upload

LIVE = os.environ.get("FIELD_LIVE_VERIFY") == "1"
SAMPLE_VIDEO = Path(__file__).resolve().parents[2] / "scripts" / "smoke" / "sample.mp4"

pytestmark = pytest.mark.skipif(not LIVE, reason="FIELD_LIVE_VERIFY=1 not set")


@pytest.fixture()
def live_env(tmp_path, monkeypatch):
    db = tmp_path / "live.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path / "media"))
    get_settings.cache_clear()
    yield db
    get_settings.cache_clear()


def test_live_video_to_evidence(live_env) -> None:
    settings = get_settings()
    if not settings.transcribe_configured:
        pytest.skip("Transcription not configured")
    if not settings.meta_configured:
        pytest.skip("Meta Model API not configured")
    assert SAMPLE_VIDEO.exists(), "sample video missing"
    db = live_env
    save_mission(Mission(id="live-media", name="Live media"), db)

    uploaded = save_upload(
        "live-media",
        original_filename="sample.mp4",
        content_type="video/mp4",
        data=SAMPLE_VIDEO.read_bytes(),
        db_path=db,
    )
    assert uploaded.status == MissionStatus.VIDEO_UPLOADED
    assert uploaded.media is not None and uploaded.media.duration_s > 0

    result = process_media("live-media", db_path=db)
    media = result.media
    assert media is not None
    assert media.status.value == "ready", media.error
    assert len(media.frames) == 6
    stamps = [f.timestamp_s for f in media.frames]
    assert stamps == sorted(stamps) and all(0 < s < media.duration_s for s in stamps)
    assert media.transcript is not None and media.transcript.text.strip() != ""
    assert media.transcript.model_id == settings.muse_transcribe_model_id
    assert len(media.observations) >= 1
    known = {f.id for f in media.frames}
    assert all(set(o.frame_ids) <= known for o in media.observations)
    assert media.vision_model_id == settings.muse_spark_model_id
    assert media.vision_usage.get("output_tokens", 0) > 0

    cats = [e.category for e in result.evidence]
    assert EvidenceCategory.OBSERVED in cats
    assert EvidenceCategory.STATED in cats
    assert all("frame f" in e.source for e in result.evidence
               if e.category == EvidenceCategory.OBSERVED)

    interviewed = start_interview(
        "live-media", owner_context="Rush hours are the worst.", db_path=db
    )
    assert interviewed.status == MissionStatus.INTERVIEWING
    assert interviewed.interview.pending_turn is not None

    persisted = load_mission("live-media", db)
    assert persisted is not None and persisted.media is not None
    assert persisted.media.status.value == "ready"

    print(f"\nLIVE MEDIA: {len(media.frames)} frames, "
          f"transcript {len(media.transcript.text)} chars/{len(media.transcript.turns)} turns, "
          f"{len(media.observations)} obs/{len(media.hypotheses)} hyp, "
          f"evidence {len(result.evidence)} items")
    print(f"  transcript head: {media.transcript.text[:200]}")
    print(f"  obs0: {media.observations[0].observation[:200]}")
    print(f"  Q1: {interviewed.interview.pending_turn.question[:300]}")
