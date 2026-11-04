"""Live interview/requirements test: real Muse Spark inference throughout.

Opt-in only: several model calls, a minute or two, a few cents.
Run from backend/:

    FIELD_LIVE_VERIFY=1 python -m pytest tests/test_interview_live.py -s

Skips by default. Drives the service layer directly (same code the API
calls) to avoid HTTP-client timeouts on model calls.
"""

import os

import pytest

from app.core.config import get_settings
from app.core.store import load_mission, save_mission
from app.models.mission import Mission, MissionStatus
from app.services.interview import answer_question, start_interview
from app.services.requirements import approve_requirements, generate_requirements

LIVE = os.environ.get("FIELD_LIVE_VERIFY") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="FIELD_LIVE_VERIFY=1 not set")

OWNER_CONTEXT = (
    "We run a small restaurant. Orders are written on paper slips at the "
    "counter and carried to the kitchen by hand. Slips get lost or are hard "
    "to read during rush hours, and customers wait without knowing their "
    "order status."
)

ANSWERS = [
    "Yes, kitchen staff should see new orders immediately on a shared screen, no paper handoff.",
    "Two roles: cashier takes orders at the counter, kitchen prepares them. No customer login needed.",
    "Dine-in and takeaway both matter. Rush hours are lunch and dinner; the list must stay readable then.",
]


def test_live_interview_to_approval(tmp_path) -> None:
    if not get_settings().meta_configured:
        pytest.skip("Meta Model API not configured")
    db = tmp_path / "live.db"
    save_mission(Mission(id="live-interview", name="Live interview"), db)

    mission = start_interview(
        "live-interview", owner_context=OWNER_CONTEXT, use_demo_evidence=True, db_path=db
    )
    assert mission.status == MissionStatus.INTERVIEWING
    assert mission.interview.status.value == "in_progress"
    assert mission.interview.pending_turn is not None

    given = 0
    while mission.interview.pending_turn is not None and given < 3:
        turn = mission.interview.pending_turn
        assert turn.question.strip() != ""
        mission = answer_question("live-interview", ANSWERS[given], db_path=db)
        given += 1
    assert mission.interview.is_complete is True
    questions = [t.question for t in mission.interview.turns]
    assert 1 <= len(questions) <= 3
    assert len(set(questions)) == len(questions)

    mission = generate_requirements("live-interview", db_path=db)
    assert mission.status == MissionStatus.REQUIREMENTS_READY
    spec = mission.spec
    assert spec is not None
    assert spec.problem_statement.strip() != ""
    assert len(spec.user_roles) >= 1
    assert len(spec.functional_requirements) >= 1
    assert len(spec.acceptance_criteria) >= 1
    assert spec.approval_blockers() == []

    mission = generate_requirements(
        "live-interview",
        change_request="Orders must also track whether they are dine-in or takeaway.",
        db_path=db,
    )
    assert mission.spec is not None and mission.spec.revision == 2

    mission = approve_requirements("live-interview", db_path=db)
    assert mission.status == MissionStatus.APPROVED
    assert mission.is_approved is True
    assert mission.approved_at != ""
    assert mission.requirements.strip() != ""
    assert len(mission.acceptance_criteria) >= 1

    persisted = load_mission("live-interview", db)
    assert persisted is not None
    assert persisted.status == MissionStatus.APPROVED
    assert persisted.spec is not None and persisted.spec.revision == 2

    print(f"\nLIVE INTERVIEW: {len(questions)} question(s), "
          f"{len(spec.functional_requirements)} FRs, {len(spec.acceptance_criteria)} criteria, "
          f"roles={[r.name for r in spec.user_roles]}")
    for turn in mission.interview.turns:
        print(f"  Q{turn.index}: {turn.question}")
    print(f"  problem: {spec.problem_statement[:200]}")
