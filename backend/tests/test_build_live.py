"""Live build test: real Spark generation -> real E2B -> real audit.

Opt-in only: one large model call plus a full E2B run and audit,
several minutes and on the order of $0.10-0.25.
Run from backend/:

    FIELD_LIVE_VERIFY=1 python -m pytest tests/test_build_live.py -s

Skips by default. Seeds an approved restaurant spec (spec generation
itself is proven by tests/test_interview_live.py) and drives the
service layer directly to avoid HTTP-client timeouts.
"""

import os

import pytest

from app.core.config import get_settings
from app.core.store import load_mission, save_mission
from app.models.interview import StructuredRequirements
from app.models.mission import Mission, MissionStatus
from app.services.build import build_application

LIVE = os.environ.get("FIELD_LIVE_VERIFY") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="FIELD_LIVE_VERIFY=1 not set")


def _restaurant_spec() -> StructuredRequirements:
    return StructuredRequirements.model_validate({
        "business_context": "Small restaurant, counter plus kitchen plus pickup shelf.",
        "problem_statement": "Paper order slips written at the counter get lost or are "
        "unreadable during rush hours, and kitchen staff rely on verbal updates.",
        "objectives": ["No lost orders", "Visible order status for staff"],
        "user_roles": [
            {"name": "cashier", "description": "Takes orders at the counter."},
            {"name": "kitchen staff", "description": "Prepares orders."},
        ],
        "functional_requirements": [
            {"id": "FR-1", "requirement": "Create orders via a counter entry form."},
            {"id": "FR-2", "requirement": "Show a shared active order list."},
            {"id": "FR-3", "requirement": "Move orders through Received, Preparing, Ready, Picked Up."},
            {"id": "FR-4", "requirement": "Show Ready orders in a pickup shelf section."},
            {"id": "FR-5", "requirement": "Keep orders visible across page reloads (client-side)."},
        ],
        "non_functional_requirements": ["Usable on a phone at the counter."],
        "user_journeys": [
            {"name": "Take order", "steps": ["Enter order", "Submit", "See it in the list"]},
            {"name": "Prepare order", "steps": ["See new order", "Advance status", "Mark Ready"]},
        ],
        "core_features": ["Order entry", "Shared order board", "Status updates", "Pickup shelf"],
        "data_entities": [{"name": "Order", "fields": ["id", "items", "status"]}],
        "acceptance_criteria": [
            "The application builds successfully and the preview serves.",
            "Counter staff can create an order and it appears in the list.",
            "Staff can move an order forward and the list updates.",
            "The board remains visible after page reload.",
        ],
        "assumptions": ["Single device at the counter."],
        "unresolved_questions": [],
    })


def test_live_approved_to_verified(tmp_path) -> None:
    settings = get_settings()
    if not settings.e2b_configured:
        pytest.skip("E2B_API_KEY not configured")
    if not settings.meta_configured:
        pytest.skip("Meta Model API not configured")
    db = tmp_path / "live.db"
    spec = _restaurant_spec()
    save_mission(Mission(
        id="live-build", name="Live build", status=MissionStatus.APPROVED,
        spec=spec, approved_at="2026-01-01T00:00:00+00:00",
        requirements="seeded", acceptance_criteria=list(spec.acceptance_criteria),
    ), db)

    result = build_application("live-build", db_path=db)

    assert result.generation is not None
    assert result.generation.status.value == "generated", result.generation.error
    assert "src/App.tsx" in result.generation.manifest
    assert result.generation.model_id == settings.muse_spark_model_id
    assert result.files is not None
    assert any(p.endswith("package.json") for p in result.files)
    assert any(p.endswith("src/App.tsx") for p in result.files)

    assert result.verification is not None
    verification = result.verification
    assert verification.e2b.available is True
    assert verification.e2b.passed is True, verification.e2b.error
    assert verification.e2b.preview_url.startswith("https://")
    assert verification.audit.completed is True, verification.audit.error
    assert result.status in (
        MissionStatus.VERIFIED,
        MissionStatus.VERIFIED_WITH_WARNINGS,
        MissionStatus.FAILED,
    )
    assert verification.coverage is not None
    assert len(verification.coverage.requirements) == 5
    assert len(verification.coverage.acceptance) == 4
    supported = [m for m in verification.coverage.acceptance if m.status.value == "supported"]
    assert len(supported) == 4
    browser_checks = {c.name: c for c in verification.e2b.checks if c.name.startswith("browser_")}
    assert verification.e2b.browser is not None and verification.e2b.browser.available is True
    assert "firefox" in verification.e2b.browser.browser
    assert browser_checks["browser_loads_app"].status.value == "pass"
    assert browser_checks["browser_creates_order"].status.value == "pass"
    assert browser_checks["browser_advances_status"].status.value == "pass"
    assert browser_checks["browser_persists_after_reload"].status.value == "pass"
    # The seeded criteria never demand invalid-transition rejection: whatever a
    # generation reports here (fail for permissive selects, skipped for
    # buttons-only), it must stay non-gating (e2b.passed above already held).
    assert browser_checks["browser_rejects_invalid"].required is False
    assert browser_checks["browser_rejects_invalid"].status.value in ("fail", "skipped")
    assert "browser_console_clean" in browser_checks
    assert "browser_responsive_layout" in browser_checks
    shots = [s for s in verification.e2b.browser.screenshots if s.data_url]
    assert len(shots) >= 2

    persisted = load_mission("live-build", db)
    assert persisted is not None and persisted.status == result.status
    assert persisted.generation is not None and persisted.verification is not None

    print(f"\nLIVE BUILD: {result.status.value} files={result.generation.manifest} "
          f"audit={verification.audit.verdict} findings={len(verification.audit.findings)} "
          f"supported_criteria={len(supported)}/4 "
          f"browser={verification.e2b.browser.browser} "
          f"browser_s={verification.e2b.browser.latency_s} shots={len(shots)}")
    print(f"  summary: {result.generation.summary[:300]}")
