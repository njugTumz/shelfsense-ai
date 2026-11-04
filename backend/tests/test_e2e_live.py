"""Live full-mission test: video -> evidence -> interview -> build -> verify.

Opt-in only: the complete FIELD slice on real services, roughly
10-15 minutes and on the order of $0.40-0.60 of provider spend.
Run from backend/:

    FIELD_LIVE_VERIFY=1 python -m pytest tests/test_e2e_live.py -s

Skips by default. Drives the service layer directly (same code the API
and UI call) using the committed sample restaurant video.
"""

import os
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.store import save_mission
from app.models.mission import Mission, MissionStatus
from app.services.build import build_application
from app.services.interview import answer_question, start_interview
from app.services.media import process_media, save_upload
from app.services.requirements import approve_requirements, generate_requirements

LIVE = os.environ.get("FIELD_LIVE_VERIFY") == "1"
SAMPLE_VIDEO = Path(__file__).resolve().parents[2] / "scripts" / "smoke" / "sample.mp4"

pytestmark = pytest.mark.skipif(not LIVE, reason="FIELD_LIVE_VERIFY=1 not set")

ANSWERS = [
    "Yes, the kitchen must see new orders instantly; the paper handoff is the core problem.",
    "Two roles: counter staff take orders, kitchen prepares them. No customer login needed.",
    "Both dine-in and takeaway matter. Rush hours are lunch and dinner.",
]


@pytest.fixture()
def live_env(tmp_path, monkeypatch):
    db = tmp_path / "e2e.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path / "media"))
    get_settings.cache_clear()
    yield db
    get_settings.cache_clear()


def test_live_full_mission(live_env) -> None:
    settings = get_settings()
    if not settings.transcribe_configured:
        pytest.skip("Transcription not configured")
    if not settings.meta_configured:
        pytest.skip("Meta Model API not configured")
    if not settings.e2b_configured:
        pytest.skip("E2B_API_KEY not configured")
    assert SAMPLE_VIDEO.exists(), "sample video missing"
    db = live_env
    save_mission(Mission(id="e2e", name="Full mission"), db)

    save_upload("e2e", "sample.mp4", "video/mp4", SAMPLE_VIDEO.read_bytes(), db_path=db)
    analyzed = process_media("e2e", db_path=db)
    assert analyzed.media is not None and analyzed.media.status.value == "ready", (
        analyzed.media.error if analyzed.media else "no media"
    )

    mission = start_interview("e2e", owner_context="Rush hours are the worst.", db_path=db)
    given = 0
    while mission.interview.pending_turn is not None and given < 3:
        mission = answer_question("e2e", ANSWERS[given], db_path=db)
        given += 1
    assert mission.interview.is_complete is True

    mission = generate_requirements("e2e", db_path=db)
    assert mission.status == MissionStatus.REQUIREMENTS_READY
    assert mission.spec is not None
    mission = approve_requirements("e2e", db_path=db)
    assert mission.status == MissionStatus.APPROVED

    result = build_application("e2e", db_path=db)
    assert result.generation is not None and result.generation.status.value == "generated", (
        result.generation.error if result.generation else "no generation"
    )
    assert result.verification is not None
    verification = result.verification
    assert verification.e2b.available is True
    assert verification.e2b.passed is True, verification.e2b.error
    assert verification.audit.completed is True, verification.audit.error
    assert verification.e2b.browser is not None and verification.e2b.browser.available is True
    browser_checks = {c.name: c.status.value for c in verification.e2b.checks
                      if c.name.startswith("browser_")}
    assert browser_checks.get("browser_loads_app") == "pass"
    assert result.status in (
        MissionStatus.VERIFIED,
        MissionStatus.VERIFIED_WITH_WARNINGS,
        MissionStatus.FAILED,
    )

    print(f"\nLIVE E2E: {result.status.value} "
          f"questions={len(mission.interview.turns)} "
          f"spec_rev={result.spec.revision} "
          f"files={result.generation.manifest} "
          f"audit={verification.audit.verdict} "
          f"browser={browser_checks}")
