"""Live end-to-end verification test: real E2B run + real Spark audit.

Opt-in only: takes minutes and spends a few cents of provider budget.
Run from backend/:

    FIELD_LIVE_VERIFY=1 python -m pytest tests/test_verify_live.py -s

Skips by default so the normal suite stays keyless and fast. Asserts the
full persisted path: demo files -> E2B checks executed -> Spark audit
completed -> terminal mission status stored in SQLite.
"""

import os

import pytest

from app.core.config import get_settings
from app.core.store import load_mission, save_mission
from app.demo.restaurant_app import (
    DEMO_ACCEPTANCE_CRITERIA,
    DEMO_REQUIREMENTS,
    demo_files,
)
from app.models.mission import Mission, MissionStatus
from app.services.pipeline import run_mission_verification

LIVE = os.environ.get("FIELD_LIVE_VERIFY") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="FIELD_LIVE_VERIFY=1 not set")


def test_live_demo_verification(tmp_path) -> None:
    settings = get_settings()
    if not settings.e2b_configured:
        pytest.skip("E2B_API_KEY not configured")
    if not settings.meta_configured:
        pytest.skip("Meta Model API not configured")

    db = tmp_path / "live.db"
    mission = Mission(
        id="live-demo",
        name="Live demo",
        requirements=DEMO_REQUIREMENTS,
        acceptance_criteria=list(DEMO_ACCEPTANCE_CRITERIA),
        files=demo_files(),
    )
    save_mission(mission, db)

    result = run_mission_verification("live-demo", use_demo_app=True, db_path=db)

    assert result.verification is not None
    verification = result.verification
    assert verification.e2b.available is True
    assert verification.e2b.passed is True, verification.e2b.error
    assert verification.e2b.preview_url.startswith("https://")
    executed = [c for c in verification.e2b.checks if c.status.value == "pass"]
    assert len(executed) >= 7
    assert any(c.name == "status_transitions_execute" and c.status.value == "pass"
               for c in verification.e2b.checks)
    browser_checks = {c.name: c for c in verification.e2b.checks if c.name.startswith("browser_")}
    assert verification.e2b.browser is not None and verification.e2b.browser.available is True
    assert "firefox" in verification.e2b.browser.browser
    assert browser_checks["browser_loads_app"].status.value == "pass"
    assert browser_checks["browser_advances_status"].status.value == "pass"
    assert browser_checks["browser_rejects_invalid"].status.value == "pass"
    assert browser_checks["browser_creates_order"].status.value == "skipped"
    assert browser_checks["browser_persists_after_reload"].status.value == "skipped"
    shots = [s for s in verification.e2b.browser.screenshots if s.data_url]
    assert len(shots) >= 2
    assert verification.audit.provider == "spark_adversarial"
    assert verification.audit.completed is True, verification.audit.error
    assert verification.audit.model_id == settings.muse_spark_model_id
    assert verification.audit.verdict is not None
    assert result.status in (
        MissionStatus.VERIFIED,
        MissionStatus.VERIFIED_WITH_WARNINGS,
        MissionStatus.FAILED,
    )
    persisted = load_mission("live-demo", db)
    assert persisted is not None
    assert persisted.status == result.status
    assert persisted.verification is not None
    assert persisted.verification.audit.summary != ""
    print(f"\nLIVE RESULT: {result.status.value} audit={verification.audit.verdict} "
          f"findings={len(verification.audit.findings)} "
          f"e2b_s={verification.e2b.latency_s} audit_s={verification.audit.latency_s}")
