"""Keyless tests: interview state, requirements validation, approval rules.

Run from backend/: python -m pytest tests/test_interview.py
Uses seeded mission fixtures; makes no provider calls. Live Spark paths
are tests/test_interview_live.py.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.store import load_mission, save_mission
from app.main import app
from app.models.interview import (
    EvidenceCategory,
    EvidenceItem,
    InterviewState,
    InterviewStatus,
    InterviewTurn,
    StructuredRequirements,
)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _seeded_mission(client, status="CREATED"):
    mission_id = client.post("/api/missions", json={"name": "t"}).json()["id"]
    if status == "CREATED":
        return mission_id
    raise AssertionError("seed via store for non-CREATED states")


def _spec(**overrides):
    data = {
        "business_context": "ctx",
        "problem_statement": "Orders get lost between counter and kitchen.",
        "objectives": ["Fewer lost orders"],
        "user_roles": [{"name": "cashier", "description": "takes orders"}],
        "functional_requirements": [{"id": "FR-1", "requirement": "List orders."}],
        "non_functional_requirements": ["Works on phones"],
        "user_journeys": [{"name": "Take order", "steps": ["a", "b"]}],
        "core_features": ["Order list"],
        "data_entities": [{"name": "Order", "fields": ["status"]}],
        "acceptance_criteria": ["Orders are displayed."],
        "assumptions": ["Single location"],
        "unresolved_questions": [],
    }
    data.update(overrides)
    return StructuredRequirements.model_validate(data)


def test_interview_state_helpers() -> None:
    state = InterviewState(status=InterviewStatus.IN_PROGRESS, turns=[
        InterviewTurn(index=1, question="q1?", answer="a1"),
        InterviewTurn(index=2, question="q2?"),
    ])
    assert state.answered_count == 1
    assert state.pending_turn is not None and state.pending_turn.index == 2
    assert state.is_complete is False
    assert InterviewState(status=InterviewStatus.COMPLETE).is_complete is True


def test_spec_validation_and_blockers() -> None:
    assert _spec().approval_blockers() == []
    assert _spec(user_roles=[]).approval_blockers() != []
    assert _spec(functional_requirements=[]).approval_blockers() != []
    assert _spec(acceptance_criteria=[]).approval_blockers() != []
    with pytest.raises(Exception):
        _spec(problem_statement="  ")


def test_start_rejects_empty_without_demo(db_path) -> None:
    client = TestClient(app)
    mission_id = _seeded_mission(client)
    response = client.post(f"/api/missions/{mission_id}/interview/start", json={})
    assert response.status_code == 400


def test_start_rejects_unknown_mission(db_path) -> None:
    client = TestClient(app)
    assert client.post("/api/missions/nope/interview/start", json={}).status_code == 404
    assert client.get("/api/missions/nope/interview").status_code == 404
    assert client.get("/api/missions/nope/requirements").status_code == 404
    assert client.post("/api/missions/nope/approve", json={}).status_code == 404


def test_answer_enforces_three_question_cap_without_spark(db_path) -> None:
    """Seed 2 answered + 1 pending: the 3rd answer completes with no model call."""
    from app.models.mission import Mission, MissionStatus

    mission = Mission(
        id="cap",
        status=MissionStatus.INTERVIEWING,
        evidence=[EvidenceItem(category=EvidenceCategory.STATED, text="ctx", source="owner context")],
        interview=InterviewState(status=InterviewStatus.IN_PROGRESS, turns=[
            InterviewTurn(index=1, question="q1?", rationale="r1", answer="a1"),
            InterviewTurn(index=2, question="q2?", rationale="r2", answer="a2"),
            InterviewTurn(index=3, question="q3?", rationale="r3"),
        ]),
    )
    save_mission(mission, db_path)
    client = TestClient(app)
    response = client.post("/api/missions/cap/interview/answer", json={"answer": "a3"})
    assert response.status_code == 200
    body = response.json()
    assert body["interview"]["status"] == "complete"
    assert len(body["interview"]["turns"]) == 3
    assert all(t["answer"] for t in body["interview"]["turns"])

    reloaded = load_mission("cap", db_path)
    assert reloaded is not None and reloaded.interview.is_complete is True


def test_answer_rejects_when_nothing_pending(db_path) -> None:
    client = TestClient(app)
    mission_id = _seeded_mission(client)
    assert client.post(f"/api/missions/{mission_id}/interview/answer", json={"answer": "x"}).status_code == 409


def test_generate_requires_complete_interview(db_path) -> None:
    client = TestClient(app)
    mission_id = _seeded_mission(client)
    assert client.post(f"/api/missions/{mission_id}/requirements/generate").status_code == 409
    assert client.post(f"/api/missions/{mission_id}/requirements/changes", json={"feedback": "x"}).status_code == 409


def test_approve_rules_and_flattening(db_path) -> None:
    from app.models.mission import Mission, MissionStatus

    client = TestClient(app)
    fresh_id = _seeded_mission(client)
    assert client.post(f"/api/missions/{fresh_id}/approve").status_code == 409

    ready = Mission(id="ready", status=MissionStatus.REQUIREMENTS_READY, spec=_spec())
    save_mission(ready, db_path)
    response = client.post("/api/missions/ready/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["approved_at"] != ""
    assert body["is_approved"] is True
    assert "Orders get lost" in body["requirements"]
    assert body["acceptance_criteria"] == ["Orders are displayed."]

    weak = Mission(
        id="weak",
        status=MissionStatus.REQUIREMENTS_READY,
        spec=_spec(acceptance_criteria=[]),
    )
    save_mission(weak, db_path)
    assert client.post("/api/missions/weak/approve").status_code == 409


def test_interview_views_and_missing_spec(db_path) -> None:
    client = TestClient(app)
    mission_id = _seeded_mission(client)
    view = client.get(f"/api/missions/{mission_id}/interview").json()
    assert view["interview"]["status"] == "not_started"
    assert view["evidence"] == []
    assert client.get(f"/api/missions/{mission_id}/requirements").status_code == 404


def test_spark_unavailable_is_503(db_path, monkeypatch) -> None:
    monkeypatch.setenv("META_MODEL_API_KEY", "")
    get_settings.cache_clear()
    client = TestClient(app)
    mission_id = client.post("/api/missions", json={}).json()["id"]
    response = client.post(
        f"/api/missions/{mission_id}/interview/start",
        json={"owner_context": "We lose paper order slips.", "use_demo_evidence": True},
    )
    assert response.status_code == 503
    assert "unavailable" in response.text.lower() or "not configured" in response.text.lower()
    assert load_mission(mission_id, db_path).status.value == "CREATED"


def test_old_mission_rows_load_with_defaults() -> None:
    from app.models.mission import Mission

    mission = Mission.model_validate({"id": "legacy"})
    assert mission.interview.status.value == "not_started"
    assert mission.evidence == []
    assert mission.spec is None
    assert mission.approved_at == ""
    assert mission.is_approved is False
