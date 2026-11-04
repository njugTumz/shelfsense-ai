"""Keyless tests: repair eligibility matrix and one-cycle orchestration.

Run from backend/: python -m pytest tests/test_repair.py
Provider outcomes are represented by fixed fixtures/stubs; real Spark
repair plus real E2B retest is proven by tests/test_repair_live.py.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.demo.restaurant_app import APP_ROOT
from app.main import app
from app.models.generation import GenerationOutput, RepairRecord, RepairStatus
from app.models.mission import Mission, MissionStatus
from app.models.verification import (
    AuditFinding,
    AuditResult,
    CheckCategory,
    CheckStatus,
    E2BVerification,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _spec():
    from app.models.interview import StructuredRequirements

    return StructuredRequirements.model_validate({
        "business_context": "ctx",
        "problem_statement": "Slips get lost.",
        "objectives": ["Fewer lost orders"],
        "user_roles": [{"name": "cashier", "description": "takes orders"}],
        "functional_requirements": [
            {"id": "FR-1", "requirement": "Create orders via a counter form."},
        ],
        "non_functional_requirements": ["Works on phones"],
        "user_journeys": [{"name": "Take order", "steps": ["a", "b"]}],
        "core_features": ["Order list"],
        "data_entities": [{"name": "Order", "fields": ["status"]}],
        "acceptance_criteria": ["The application builds successfully."],
        "assumptions": [],
        "unresolved_questions": [],
    })


def _check(name, status=CheckStatus.FAIL, category=CheckCategory.BUILD,
           required=True, detail="boom"):
    return VerificationCheck(
        name=name, category=category, status=status,
        required=required, detail=detail,
    )


def _e2b(checks, passed=False):
    return E2BVerification(available=True, passed=passed, checks=checks)


def _audit(findings=None, verdict="PASS", completed=True):
    return AuditResult(
        provider="spark_adversarial", available=True, completed=completed,
        verdict=verdict if completed else None, summary="stub",
        findings=findings or [],
    )


def _failed_mission(reason, e2b, audit, mission_id="r1", repair=None):
    from app.core.store import save_mission
    from app.models.generation import GenerationRecord, GenerationStatus

    mission = Mission(
        id=mission_id, status=MissionStatus.FAILED, spec=_spec(),
        approved_at="t", requirements="seeded",
        acceptance_criteria=["The application builds successfully."],
        files={f"{APP_ROOT}/src/App.tsx": "broken content"},
        generation=GenerationRecord(
            status=GenerationStatus.GENERATED, manifest=["src/App.tsx"]),
        verification=VerificationResult(
            status=VerificationStatus.FAILED, reason=reason,
            e2b=e2b, audit=audit,
        ),
        error="seeded failure",
        repair=repair,
    )
    return mission


def _save(mission, db_path):
    from app.core.store import save_mission

    return save_mission(mission, db_path)


def test_eligibility_requires_failed_with_verification(db_path) -> None:
    from app.services.repair import repair_eligibility

    approved = Mission(id="a", status=MissionStatus.APPROVED)
    decision = repair_eligibility(approved)
    assert decision.eligible is False
    assert decision.reason == "not_failed"

    verified = Mission(id="v", status=MissionStatus.VERIFIED)
    assert repair_eligibility(verified).reason == "not_failed"

    failed_bare = Mission(id="f", status=MissionStatus.FAILED)
    assert repair_eligibility(failed_bare).reason == "no_verification"


def test_eligibility_refuses_second_cycle(db_path) -> None:
    from app.services.repair import repair_eligibility

    mission = _failed_mission(
        "runtime", _e2b([_check("production_build")]), _audit(),
        repair=RepairRecord(status=RepairStatus.REPAIRED, attempts=1),
    )
    decision = repair_eligibility(mission)
    assert decision.eligible is False
    assert decision.reason == "already_repaired"


def test_eligibility_runtime_build_failure_is_repairable() -> None:
    from app.services.repair import repair_eligibility

    mission = _failed_mission(
        "runtime",
        _e2b([
            _check("production_build", detail="error TS2322: boom"),
            _check("npm_install", status=CheckStatus.PASS, detail="ok"),
        ]),
        _audit(),
    )
    decision = repair_eligibility(mission)
    assert decision.eligible is True
    assert decision.reason == "runtime"
    assert decision.trigger == "runtime:production_build"
    assert "TS2322" in decision.failures


def test_eligibility_runtime_browser_failure_is_repairable() -> None:
    from app.services.repair import repair_eligibility

    mission = _failed_mission(
        "runtime",
        _e2b([_check("browser_creates_order", category=CheckCategory.FUNCTIONAL,
                      detail="timeout waiting for marker")]),
        _audit(),
    )
    decision = repair_eligibility(mission)
    assert decision.eligible is True
    assert decision.trigger == "runtime:browser_creates_order"


def test_eligibility_infra_failures_are_not_repairable() -> None:
    from app.services.repair import repair_eligibility

    for name in ("npm_install", "node_runtime", "sandbox_create",
                 "files_uploaded", "sandbox_exception"):
        mission = _failed_mission("runtime", _e2b([_check(name)]), _audit())
        decision = repair_eligibility(mission)
        assert decision.eligible is False, name
        assert decision.reason == "infrastructure"

    mixed = _failed_mission(
        "runtime",
        _e2b([_check("npm_install"), _check("production_build")]),
        _audit(),
    )
    decision = repair_eligibility(mixed)
    assert decision.eligible is False
    assert decision.reason == "infrastructure"


def test_eligibility_unavailable_providers_are_not_repairable() -> None:
    from app.services.repair import repair_eligibility

    down = E2BVerification(available=False, passed=False, error="no key")
    mission = _failed_mission("e2b_unavailable", down, _audit(completed=False))
    decision = repair_eligibility(mission)
    assert decision.eligible is False
    assert decision.reason == "unavailable_provider"

    mission = _failed_mission(
        "audit_unavailable", _e2b([], passed=True), _audit(completed=False))
    assert repair_eligibility(mission).reason == "unavailable_provider"


def test_eligibility_audit_blocking_findings_are_repairable() -> None:
    from app.services.repair import repair_eligibility

    findings = [
        AuditFinding(
            severity="critical", category="workflow",
            finding="Status button does nothing.",
            evidence="clicked, no change", recommendation="Wire the handler.",
            file="src/App.tsx",
        ),
        AuditFinding(
            severity="high", category="implementation",
            finding="Phantom module breaks the build.",
            evidence="import fails", recommendation="Remove it.",
            file="src/components/Phantom.tsx",
        ),
        AuditFinding(
            severity="low", category="ux",
            finding="Minor spacing nit.", evidence="", recommendation="",
        ),
    ]
    mission = _failed_mission(
        "audit", _e2b([_check("production_build", status=CheckStatus.PASS)]),
        _audit(findings=findings, verdict="FAIL"),
    )
    decision = repair_eligibility(mission)
    assert decision.eligible is True
    assert decision.reason == "audit"
    assert decision.trigger == "audit:2 blocking finding(s)"
    assert "[grounded]" in decision.failures
    assert "[ungrounded]" in decision.failures
    assert "Minor spacing nit" not in decision.failures


def test_eligibility_audit_fail_verdict_without_findings() -> None:
    from app.services.repair import repair_eligibility

    mission = _failed_mission(
        "audit", _e2b([_check("production_build", status=CheckStatus.PASS)]),
        AuditResult(provider="spark_adversarial", available=True,
                    completed=True, verdict="FAIL", summary="holistic fail"),
    )
    decision = repair_eligibility(mission)
    assert decision.eligible is True
    assert "holistic fail" in decision.failures


def _repaired_output():
    return GenerationOutput.model_validate({
        "summary": "fixed the type error",
        "files": [{"path": "src/App.tsx",
                   "content": "export default function App() { return null; }"}],
        "feature_map": [],
        "unimplemented": [],
        "assumptions": [],
    })


def _stub_audit_pass():
    return AuditResult(provider="spark_adversarial", available=True,
                       completed=True, verdict="PASS", summary="stub")


def test_repair_success_flips_failed_to_verified(db_path, monkeypatch) -> None:
    from app.services import pipeline as pipeline_module
    from app.services import repair as repair_module

    _save(_failed_mission(
        "runtime", _e2b([_check("production_build")]), _audit()), db_path)
    seen = {}

    def fake_repair(spec, current, failures):
        seen["current"] = dict(current)
        seen["failures"] = failures
        return _repaired_output(), {"input_tokens": 1}, "stub-model"

    monkeypatch.setattr(repair_module, "repair_application_files", fake_repair)
    monkeypatch.setattr(
        pipeline_module, "verify_in_e2b",
        lambda files, acceptance_criteria=None, progress=None: _e2b(
            [_check("production_build", status=CheckStatus.PASS, detail="ok")],
            passed=True,
        ),
    )
    monkeypatch.setattr(
        pipeline_module, "active_auditor",
        lambda: type("A", (), {"audit": staticmethod(lambda ev: _stub_audit_pass())})(),
    )

    result = repair_module.repair_application("r1", db_path=db_path)
    assert result.status == MissionStatus.VERIFIED
    assert result.repair is not None
    assert result.repair.status == RepairStatus.REPAIRED
    assert result.repair.attempts == 1
    assert result.repair.trigger == "runtime:production_build"
    assert result.repair.previous_status == "FAILED"
    assert result.repair.previous_reason == "runtime"
    assert result.repair.new_status == "VERIFIED"
    assert result.repair.new_reason == "clean"
    assert result.repair.manifest == ["src/App.tsx"]
    assert result.repair.model_id == "stub-model"
    assert "src/App.tsx" in seen["current"]
    assert "production_build" in seen["failures"]
    assert result.files is not None
    assert result.files[f"{APP_ROOT}/src/App.tsx"].startswith("export default")
    assert any(p.endswith("package.json") for p in result.files)
    assert result.verification is not None
    assert result.verification.coverage is not None
    assert len(result.verification.coverage.requirements) == 1


def test_repair_sources_exclude_scaffold_owned_files(db_path, monkeypatch) -> None:
    from app.core.store import save_mission
    from app.services import pipeline as pipeline_module
    from app.services import repair as repair_module

    mission = _failed_mission(
        "runtime", _e2b([_check("production_build")]), _audit())
    mission.files = {
        f"{APP_ROOT}/src/App.tsx": "broken content",
        f"{APP_ROOT}/src/main.tsx": "scaffold entry",
        f"{APP_ROOT}/src/vite-env.d.ts": "scaffold types",
        f"{APP_ROOT}/package.json": "{}",
    }
    save_mission(mission, db_path)
    seen = {}

    def fake_repair(spec, current, failures):
        seen["current"] = dict(current)
        return _repaired_output(), {}, "stub-model"

    monkeypatch.setattr(repair_module, "repair_application_files", fake_repair)
    monkeypatch.setattr(
        pipeline_module, "verify_in_e2b",
        lambda files, acceptance_criteria=None, progress=None: _e2b(
            [_check("production_build", status=CheckStatus.PASS, detail="ok")],
            passed=True,
        ),
    )
    monkeypatch.setattr(
        pipeline_module, "active_auditor",
        lambda: type("A", (), {"audit": staticmethod(lambda ev: _stub_audit_pass())})(),
    )

    repair_module.repair_application("r1", db_path=db_path)
    assert set(seen["current"]) == {"src/App.tsx"}


def test_repair_retest_still_failing_records_attempt(db_path, monkeypatch) -> None:
    from app.services import pipeline as pipeline_module
    from app.services import repair as repair_module

    _save(_failed_mission(
        "runtime", _e2b([_check("production_build")]), _audit()), db_path)
    monkeypatch.setattr(
        repair_module, "repair_application_files",
        lambda spec, current, failures: (_repaired_output(), {}, "stub-model"),
    )
    monkeypatch.setattr(
        pipeline_module, "verify_in_e2b",
        lambda files, acceptance_criteria=None, progress=None: _e2b([_check("production_build")]),
    )
    monkeypatch.setattr(
        pipeline_module, "active_auditor",
        lambda: type("A", (), {"audit": staticmethod(lambda ev: _stub_audit_pass())})(),
    )

    result = repair_module.repair_application("r1", db_path=db_path)
    assert result.status == MissionStatus.FAILED
    assert result.repair is not None
    assert result.repair.attempts == 1
    assert result.repair.new_status == "FAILED"
    assert "Repair attempted" in result.error
    assert "runtime:production_build" in result.error


def test_repair_generation_failure_keeps_attempt(db_path, monkeypatch) -> None:
    from app.services import repair as repair_module
    from app.services.spark_client import SparkError

    _save(_failed_mission(
        "runtime", _e2b([_check("production_build")]), _audit()), db_path)

    def boom(spec, current, failures):
        raise SparkError("TRANSPORT ERROR: boom")

    monkeypatch.setattr(repair_module, "repair_application_files", boom)
    result = repair_module.repair_application("r1", db_path=db_path)
    assert result.status == MissionStatus.FAILED
    assert result.repair is not None
    assert result.repair.status == RepairStatus.FAILED
    assert result.repair.attempts == 0
    assert "boom" in result.error
    assert result.files is not None
    assert result.files[f"{APP_ROOT}/src/App.tsx"] == "broken content"

    from app.services.repair import repair_eligibility

    assert repair_eligibility(result).eligible is True


def test_second_repair_refused_after_completed_cycle(db_path, monkeypatch) -> None:
    from app.services import repair as repair_module
    from app.services.interview import LifecycleError

    _save(_failed_mission(
        "runtime", _e2b([_check("production_build")]), _audit(),
        repair=RepairRecord(status=RepairStatus.REPAIRED, attempts=1)), db_path)
    with pytest.raises(LifecycleError):
        repair_module.repair_application("r1", db_path=db_path)


def test_repair_guards(db_path) -> None:
    from app.core.store import save_mission
    from app.services import repair as repair_module
    from app.services.interview import LifecycleError

    client = TestClient(app)
    assert client.post("/api/missions/nope/repair").status_code == 404
    fresh = client.post("/api/missions", json={}).json()["id"]
    assert client.post(f"/api/missions/{fresh}/repair").status_code == 409

    save_mission(Mission(id="run", status=MissionStatus.RUNNING), db_path)
    with pytest.raises(LifecycleError):
        repair_module.repair_application("run", db_path=db_path)

    save_mission(Mission(id="ok", status=MissionStatus.APPROVED), db_path)
    with pytest.raises(LifecycleError):
        repair_module.repair_application("ok", db_path=db_path)
