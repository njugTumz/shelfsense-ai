"""Keyless tests: verdict matrix, Glimmer stub, mission API wiring.

Run from backend/: python -m pytest tests/test_verification.py
No provider calls are made here; the live path is tests/test_verify_live.py.
"""

import sys

import pytest
from fastapi.testclient import TestClient

from app.core.config import REPO_ROOT, get_settings
from app.main import app
from app.models.verification import (
    AuditFinding,
    AuditResult,
    AuditVerdict,
    CheckCategory,
    CheckStatus,
    E2BVerification,
    VerificationCheck,
    VerificationStatus,
    compute_verdict,
    summarize_e2b,
)
from app.services.auditors import AuditEvidence, GlimmerAuditor


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _check(name, status, required=True):
    return VerificationCheck(
        name=name,
        category=CheckCategory.BUILD,
        status=status,
        required=required,
    )


def _e2b(passed: bool, available: bool = True) -> E2BVerification:
    status = CheckStatus.PASS if passed else CheckStatus.FAIL
    return E2BVerification(available=available, passed=passed, checks=[_check("c", status)])


def _audit(verdict=None, findings=None, available=True, completed=True) -> AuditResult:
    return AuditResult(
        provider="spark_adversarial",
        available=available,
        completed=completed,
        verdict=verdict,
        findings=findings or [],
    )


def _finding(severity):
    return AuditFinding(
        severity=severity,
        category="requirements",
        finding="f",
        evidence="e",
        recommendation="r",
    )


def test_repo_root_points_at_repository() -> None:
    assert (REPO_ROOT / "plan.md").is_file()
    assert (REPO_ROOT / ".env.example").is_file()


def test_summarize_e2b_requires_all_required_pass() -> None:
    assert summarize_e2b([_check("a", CheckStatus.PASS)]) is True
    assert summarize_e2b([
        _check("a", CheckStatus.PASS),
        _check("opt", CheckStatus.FAIL, required=False),
    ]) is True
    assert summarize_e2b([_check("a", CheckStatus.FAIL)]) is False
    assert summarize_e2b([_check("a", CheckStatus.SKIPPED)]) is False
    assert summarize_e2b([]) is False


def test_verdict_matrix() -> None:
    clean = _audit(verdict=AuditVerdict.PASS)
    assert compute_verdict(_e2b(False), clean) == (VerificationStatus.FAILED, "runtime")
    assert compute_verdict(_e2b(True, available=False), clean) == (
        VerificationStatus.FAILED,
        "e2b_unavailable",
    )
    assert compute_verdict(_e2b(True), _audit(available=False)) == (
        VerificationStatus.AUDIT_LIMITED,
        "audit_unavailable",
    )
    assert compute_verdict(_e2b(True), _audit(completed=False)) == (
        VerificationStatus.AUDIT_LIMITED,
        "audit_unavailable",
    )
    assert compute_verdict(_e2b(True), _audit(verdict=AuditVerdict.FAIL)) == (
        VerificationStatus.FAILED,
        "audit",
    )
    assert compute_verdict(
        _e2b(True), _audit(verdict=AuditVerdict.PASS, findings=[_finding("critical")])
    ) == (VerificationStatus.FAILED, "audit")
    assert compute_verdict(
        _e2b(True), _audit(verdict=AuditVerdict.PASS, findings=[_finding("high")])
    ) == (VerificationStatus.FAILED, "audit")
    assert compute_verdict(
        _e2b(True), _audit(verdict=AuditVerdict.PASS, findings=[_finding("medium")])
    ) == (VerificationStatus.VERIFIED_WITH_WARNINGS, "warnings")
    assert compute_verdict(
        _e2b(True), _audit(verdict=AuditVerdict.PASS_WITH_WARNINGS)
    ) == (VerificationStatus.VERIFIED_WITH_WARNINGS, "warnings")
    assert compute_verdict(_e2b(True), clean) == (VerificationStatus.VERIFIED, "clean")


def test_glimmer_provider_never_calls_modal() -> None:
    auditor = GlimmerAuditor()
    assert auditor.available() is False
    result = auditor.audit(AuditEvidence())
    assert result.provider == "glimmer"
    assert result.available is False
    assert result.completed is False
    assert "NOT VERIFIED" in result.error
    assert "modal" not in {m.split(".")[0] for m in sys.modules}


def test_mission_api_create_get_demo() -> None:
    client = TestClient(app)
    created = client.post("/api/missions", json={"name": "t"})
    assert created.status_code == 201
    mission_id = created.json()["id"]
    assert created.json()["status"] == "CREATED"

    fetched = client.get(f"/api/missions/{mission_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == mission_id

    demo = client.post("/api/missions/demo")
    assert demo.status_code == 201
    body = demo.json()
    assert body["requirements"] != ""
    assert len(body["acceptance_criteria"]) > 0
    assert any(p.endswith("src/App.tsx") for p in body["files"])

    assert client.get("/api/missions/does-not-exist").status_code == 404


def test_verify_rejects_without_files() -> None:
    client = TestClient(app)
    mission_id = client.post("/api/missions", json={}).json()["id"]
    response = client.post(f"/api/missions/{mission_id}/verify", json={})
    assert response.status_code == 400
    assert client.post("/api/missions/does-not-exist/verify", json={}).status_code == 404


def test_no_secrets_in_mission_responses() -> None:
    client = TestClient(app)
    raw = client.post("/api/missions/demo").text
    for forbidden in ("api_key", "apikey", "secret", "bearer", "token="):
        assert forbidden not in raw.lower()
    for value in (get_settings().e2b_api_key, get_settings().meta_model_api_key):
        if value:
            assert value not in raw
