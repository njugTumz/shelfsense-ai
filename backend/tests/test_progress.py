"""Keyless tests: progress-supporting backend (sticky approval, check
timestamps, build-job endpoint)."""

import pytest

from app.models.jobs import JobRecord, JobStatus
from app.models.mission import Mission, MissionStatus


def test_is_approved_is_sticky_after_build_starts() -> None:
    assert Mission(id="m").is_approved is False
    approved = Mission(
        id="m", status=MissionStatus.APPROVED, approved_at="2026-11-05T00:00:00+00:00")
    assert approved.is_approved is True
    building = Mission(
        id="m", status=MissionStatus.BUILDING, approved_at="2026-11-05T00:00:00+00:00")
    assert building.is_approved is True
    verified = Mission(
        id="m", status=MissionStatus.VERIFIED, approved_at="2026-11-05T00:00:00+00:00")
    assert verified.is_approved is True


def test_check_records_completion_time() -> None:
    from app.models.verification import CheckCategory
    from app.services.e2b_verification import _check

    check = _check("production_build", CheckCategory.BUILD, True, "ok", 1.0)
    assert check.completed_at != ""
    assert "T" in check.completed_at


def _job(**overrides):
    record = JobRecord(id="j1", mission_id="m1")
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


def test_build_job_endpoint_returns_active_job(monkeypatch) -> None:
    import app.api.missions as missions_api

    job = _job(status=JobStatus.RUNNING, heartbeat_at="2026-11-05T00:00:00+00:00")
    monkeypatch.setattr(missions_api, "load_mission", lambda mission_id: Mission(id=mission_id))
    monkeypatch.setattr(missions_api, "active_job", lambda mission_id: job)
    assert missions_api.api_build_job("m1") is job


def test_build_job_endpoint_404_without_job_or_mission(monkeypatch) -> None:
    from fastapi import HTTPException

    import app.api.missions as missions_api

    monkeypatch.setattr(missions_api, "load_mission", lambda mission_id: Mission(id=mission_id))
    monkeypatch.setattr(missions_api, "active_job", lambda mission_id: None)
    with pytest.raises(HTTPException) as exc:
        missions_api.api_build_job("m1")
    assert exc.value.status_code == 404

    monkeypatch.setattr(missions_api, "load_mission", lambda mission_id: None)
    with pytest.raises(HTTPException) as exc:
        missions_api.api_build_job("nope")
    assert exc.value.status_code == 404
