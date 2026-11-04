"""Live repair test: broken build -> FAILED -> one repair -> retested green.

Opt-in only: two full E2B runs, two audits, and one developer repair
call, roughly 8-12 minutes and on the order of $0.30-0.50.
Run from backend/:

    FIELD_LIVE_VERIFY=1 python -m pytest tests/test_repair_live.py -s

Skips by default. Seeds a mission whose generated App.tsx carries one
deliberate TypeScript type error (deterministic production_build
failure), verifies it to FAILED, then drives the real repair loop and
asserts the retest passes. Drives the service layer directly to avoid
HTTP-client timeouts.
"""

import os

import pytest

from app.core.config import get_settings
from app.core.store import load_mission, save_mission
from app.demo.restaurant_app import APP_ROOT
from app.demo.scaffold import scaffold_files
from app.models.interview import StructuredRequirements
from app.models.mission import Mission, MissionStatus
from app.services.interview import LifecycleError
from app.services.pipeline import run_mission_verification
from app.services.repair import repair_application

LIVE = os.environ.get("FIELD_LIVE_VERIFY") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="FIELD_LIVE_VERIFY=1 not set")

#: One deliberate defect: assigning a string to a number fails
#: `tsc --noEmit` deterministically. Everything else is valid.
BROKEN_APP_TSX = """\
import { useState } from "react";

const seedCount: number = "three";

export default function App() {
  const [orders] = useState<string[]>(["Order 1", "Order 2"]);
  return (
    <main>
      <h1>Orders ({seedCount})</h1>
      <ul>
        {orders.map((item) => (
          <li key={item}>{item} \\u2014 Received</li>
        ))}
      </ul>
    </main>
  );
}
"""


def _spec() -> StructuredRequirements:
    return StructuredRequirements.model_validate({
        "business_context": "Small restaurant counter.",
        "problem_statement": "Paper slips get lost.",
        "objectives": ["Visible orders"],
        "user_roles": [
            {"name": "cashier", "description": "Takes orders at the counter."},
        ],
        "functional_requirements": [
            {"id": "FR-1", "requirement": "Show the active order list."},
        ],
        "non_functional_requirements": ["Usable on a phone."],
        "user_journeys": [
            {"name": "See orders", "steps": ["Open app", "Read the list"]},
        ],
        "core_features": ["Order list"],
        "data_entities": [{"name": "Order", "fields": ["id", "items", "status"]}],
        "acceptance_criteria": [
            "The application builds successfully and the preview serves.",
        ],
        "assumptions": [],
        "unresolved_questions": [],
    })


def test_live_repair_fixes_broken_build(tmp_path) -> None:
    settings = get_settings()
    if not settings.e2b_configured:
        pytest.skip("E2B_API_KEY not configured")
    if not settings.meta_configured:
        pytest.skip("Meta Model API not configured")
    db = tmp_path / "repair-live.db"
    spec = _spec()
    files = scaffold_files()
    files[f"{APP_ROOT}/src/App.tsx"] = BROKEN_APP_TSX
    save_mission(Mission(
        id="live-repair", name="Live repair", status=MissionStatus.APPROVED,
        spec=spec, approved_at="2026-01-01T00:00:00+00:00",
        requirements="seeded", acceptance_criteria=list(spec.acceptance_criteria),
        files=files,
    ), db)

    failed = run_mission_verification("live-repair", db_path=db)
    assert failed.status == MissionStatus.FAILED, failed.status
    assert failed.verification is not None
    assert failed.verification.reason == "runtime", failed.verification.reason
    checks = {c.name: c for c in failed.verification.e2b.checks}
    assert checks["production_build"].status.value == "fail"
    assert "TS2322" in checks["production_build"].detail or \
        "Type 'string' is not assignable" in checks["production_build"].detail, \
        checks["production_build"].detail[:500]

    repaired = repair_application("live-repair", db_path=db)

    assert repaired.repair is not None
    assert repaired.repair.attempts == 1
    assert repaired.repair.status.value == "repaired"
    assert repaired.repair.trigger == "runtime:production_build"
    assert repaired.repair.previous_status == "FAILED"
    assert repaired.repair.previous_reason == "runtime"
    assert repaired.repair.new_status == repaired.status.value
    assert repaired.repair.model_id == settings.muse_spark_model_id
    assert "src/App.tsx" in repaired.repair.manifest

    assert repaired.verification is not None
    verification = repaired.verification
    assert verification.e2b.available is True
    assert verification.e2b.passed is True, verification.e2b.error
    assert verification.audit.completed is True, verification.audit.error
    assert verification.coverage is not None
    assert len(verification.coverage.requirements) == 1
    assert repaired.status in (
        MissionStatus.VERIFIED,
        MissionStatus.VERIFIED_WITH_WARNINGS,
        MissionStatus.FAILED,
    )
    if repaired.status == MissionStatus.FAILED:
        assert verification.reason == "audit", verification.reason

    with pytest.raises(LifecycleError):
        repair_application("live-repair", db_path=db)

    persisted = load_mission("live-repair", db)
    assert persisted is not None and persisted.repair is not None
    assert persisted.repair.attempts == 1

    print(f"\nLIVE REPAIR: FAILED/runtime -> {repaired.status.value}/"
          f"{verification.reason} model={repaired.repair.model_id} "
          f"summary={repaired.repair.summary[:200]}")
    print(f"  audit verdict={verification.audit.verdict} "
          f"summary={verification.audit.summary[:300]}")
    for finding in verification.audit.findings:
        print(f"  finding [{finding.severity.value}/{finding.category.value}] "
              f"{finding.finding[:250]}")
