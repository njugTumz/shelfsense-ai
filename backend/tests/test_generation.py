"""Keyless tests: manifest validator, coverage mapping, build lifecycle.

Run from backend/: python -m pytest tests/test_generation.py
Provider outcomes are represented by fixed fixtures/stubs; real Spark
and E2B behavior is proven by tests/test_build_live.py.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models.generation import (
    AcceptanceSupport,
    FeatureMapping,
    GeneratedFile,
    GenerationOutput,
    RequirementCoverageStatus,
    UnimplementedItem,
    validate_manifest,
)
from app.models.interview import FunctionalRequirement, StructuredRequirements
from app.models.verification import (
    AuditResult,
    CheckCategory,
    CheckStatus,
    E2BVerification,
    VerificationCheck,
)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _file(path="src/App.tsx", content="export default function App() { return null; }"):
    return GeneratedFile(path=path, content=content)


def _spec():
    return StructuredRequirements.model_validate({
        "business_context": "ctx",
        "problem_statement": "Slips get lost.",
        "objectives": ["Fewer lost orders"],
        "user_roles": [{"name": "cashier", "description": "takes orders"}],
        "functional_requirements": [
            {"id": "FR-1", "requirement": "Create orders via a counter form."},
            {"id": "FR-2", "requirement": "Show a shared active order board."},
        ],
        "non_functional_requirements": ["Works on phones"],
        "user_journeys": [{"name": "Take order", "steps": ["a", "b"]}],
        "core_features": ["Order list"],
        "data_entities": [{"name": "Order", "fields": ["status"]}],
        "acceptance_criteria": [
            "The application builds successfully.",
            "Staff can move an order from Received to Preparing.",
        ],
        "assumptions": [],
        "unresolved_questions": [],
    })


def _output(**overrides):
    data = {
        "summary": "s",
        "files": [_file()],
        "feature_map": [],
        "unimplemented": [],
        "assumptions": [],
    }
    data.update(overrides)
    return GenerationOutput.model_validate(data)


def test_manifest_accepts_minimal_valid() -> None:
    assert validate_manifest([_file()]) == []
    assert validate_manifest([
        _file(),
        _file("src/app.css", ".x { color: red; }"),
        _file("src/lib/rules.js", "export const X = 1;"),
        _file("src/components/Row.tsx", "export default function Row() { return null; }"),
    ]) == []


def test_manifest_rejects_unsafe_paths() -> None:
    assert validate_manifest([_file("/etc/passwd")])
    assert validate_manifest([_file("../escape.tsx")])
    assert validate_manifest([_file("src/../escape.tsx")])
    assert validate_manifest([_file("src\\win.tsx")])
    assert validate_manifest([_file("")])
    assert validate_manifest([_file("package.json", "{}")])
    assert validate_manifest([_file("README.md", "x"), _file()])
    assert validate_manifest([_file("src/a/b/c/d.tsx")])
    assert validate_manifest([_file("src/notes.txt", "x"), _file()])
    assert validate_manifest([_file("src/noext", "x"), _file()])
    assert validate_manifest([_file("src/main.tsx", "x"), _file()])
    assert validate_manifest([_file("src/vite-env.d.ts", "x"), _file()])


def test_manifest_rejects_duplicates_missing_and_empty() -> None:
    assert validate_manifest([_file(), _file()])
    assert validate_manifest([_file("src/other.tsx", "x")])
    assert validate_manifest([_file("src/App.tsx", "   ")])
    assert validate_manifest([])


def test_manifest_rejects_excessive_output() -> None:
    many = [_file(f"src/f{i}.tsx", "x") for i in range(25)] + [_file()]
    assert len(many) == 26
    assert validate_manifest(many)
    assert validate_manifest([_file(content="x" * 20_001)])
    big = [_file(f"src/f{i}.tsx", "x" * 18_000) for i in range(7)] + [_file()]
    assert validate_manifest(big)


def test_manifest_rejects_forbidden_content() -> None:
    for snippet in (
        "const k = process.env.X;",
        "import.meta.env.MODE",
        "META_MODEL_API_KEY",
        "E2B_API_KEY",
        "MODAL_API_KEY",
        "AZURE_SPEECH_REGION",
        "require('child_process')",
    ):
        assert validate_manifest([_file(content=f"const a = 1; // {snippet}")]), snippet


def test_generation_output_schema_validation() -> None:
    assert _output().summary == "s"
    with pytest.raises(Exception):
        GenerationOutput.model_validate({"summary": "s"})
    with pytest.raises(Exception):
        GenerationOutput.model_validate({
            "summary": "s", "files": [{"path": "x"}], "feature_map": [],
            "unimplemented": [], "assumptions": [],
        })


def test_feature_coverage_states() -> None:
    from app.services.coverage import evaluate_feature_coverage

    output = _output(
        files=[_file(content="function CounterForm() { return <form/>; }")],
        feature_map=[FeatureMapping(feature="Counter order form", requirement_ids=["FR-1"])],
        unimplemented=[UnimplementedItem(requirement_id="FR-2", reason="out of scope")],
    )
    coverages = {c.requirement_id: c for c in evaluate_feature_coverage(_spec(), output)}
    assert coverages["FR-1"].status == RequirementCoverageStatus.IMPLEMENTED
    assert "static" in coverages["FR-1"].detail
    assert coverages["FR-2"].status == RequirementCoverageStatus.UNIMPLEMENTED

    unclaimed = _output(feature_map=[])
    coverages = {c.requirement_id: c for c in evaluate_feature_coverage(_spec(), unclaimed)}
    assert coverages["FR-1"].status == RequirementCoverageStatus.MISSING_EVIDENCE

    notrace = _output(
        files=[_file(content="const totally = unrelated;")],
        feature_map=[FeatureMapping(feature="Quantum scheduler", requirement_ids=["FR-1"])],
    )
    coverages = {c.requirement_id: c for c in evaluate_feature_coverage(_spec(), notrace)}
    assert coverages["FR-1"].status == RequirementCoverageStatus.MISSING_EVIDENCE


def _e2b(passed_names):
    checks = [
        VerificationCheck(name=name, category=CheckCategory.BUILD, status=CheckStatus.PASS)
        for name in passed_names
    ]
    return E2BVerification(available=True, passed=True, checks=checks)


def test_acceptance_mapping_supported_vs_unverified() -> None:
    from app.services.coverage import evaluate_acceptance_criteria

    mappings = evaluate_acceptance_criteria(_spec(), _e2b(["production_build"]))
    by_criterion = {m.criterion: m for m in mappings}
    assert by_criterion["The application builds successfully."].status == AcceptanceSupport.SUPPORTED
    assert by_criterion["The application builds successfully."].by_checks == ["production_build"]
    assert by_criterion["Staff can move an order from Received to Preparing."].status == (
        AcceptanceSupport.UNVERIFIED
    )

    mappings = evaluate_acceptance_criteria(_spec(), _e2b([]))
    assert all(m.status == AcceptanceSupport.UNVERIFIED for m in mappings)
    assert "no mapped check passed" in mappings[0].detail

    down = E2BVerification(available=False, passed=False, error="no key")
    assert all(m.status == AcceptanceSupport.UNVERIFIED for m in evaluate_acceptance_criteria(_spec(), down))


def _approved_mission(db_path, mission_id="b1"):
    from app.models.mission import Mission, MissionStatus

    from app.core.store import save_mission

    mission = Mission(id=mission_id, status=MissionStatus.APPROVED, spec=_spec(), approved_at="t")
    return save_mission(mission, db_path)


def test_build_guards(db_path) -> None:
    from app.models.mission import Mission, MissionStatus

    from app.core.store import save_mission
    from app.services.build import build_application
    from app.services.interview import LifecycleError

    client = TestClient(app)
    fresh = client.post("/api/missions", json={}).json()["id"]
    assert client.post(f"/api/missions/{fresh}/build", json={}).status_code == 409
    assert client.post("/api/missions/nope/build", json={}).status_code == 404
    assert client.get(f"/api/missions/{fresh}/generation").status_code == 404

    save_mission(Mission(id="run", status=MissionStatus.RUNNING), db_path)
    with pytest.raises(LifecycleError):
        build_application("run", db_path=db_path)

    _approved_mission(db_path, "ok")
    save_mission(Mission(id="term", status=MissionStatus.VERIFIED), db_path)
    with pytest.raises(LifecycleError):
        build_application("term", db_path=db_path)


def test_generation_failure_rolls_back_and_allows_retry(db_path, monkeypatch) -> None:
    from app.services import build as build_module
    from app.services.spark_client import SparkError

    _approved_mission(db_path)

    def boom(spec):
        raise SparkError("TRANSPORT ERROR: boom")

    monkeypatch.setattr(build_module, "generate_application", boom)
    first = build_module.build_application("b1", db_path=db_path)
    assert first.status.value == "APPROVED"
    assert first.generation is not None and first.generation.status.value == "failed"
    assert "boom" in first.error
    assert first.files is None

    second = build_module.build_application("b1", db_path=db_path)
    assert second.status.value == "APPROVED"
    assert second.generation is not None and second.generation.status.value == "failed"


def test_failed_terminal_rebuild_restores_status(db_path, monkeypatch) -> None:
    from app.models.mission import Mission, MissionStatus

    from app.core.store import save_mission
    from app.services import build as build_module
    from app.services.developer import GenerationValidationError

    mission = Mission(id="term", status=MissionStatus.VERIFIED, spec=_spec(), approved_at="t")
    save_mission(mission, db_path)

    def bad_manifest(spec):
        raise GenerationValidationError(["missing required file: src/App.tsx"])

    monkeypatch.setattr(build_module, "generate_application", bad_manifest)
    result = build_module.build_application("term", force=True, db_path=db_path)
    assert result.status == MissionStatus.VERIFIED
    assert result.generation is not None and result.generation.status.value == "failed"
    assert "src/App.tsx" in result.error


def _stub_e2b(passed: bool):
    status = CheckStatus.PASS if passed else CheckStatus.FAIL
    check = VerificationCheck(name="production_build", category=CheckCategory.BUILD, status=status)
    return E2BVerification(available=True, passed=passed, checks=[check], preview_url="https://x")


def _stub_audit(completed: bool):
    return AuditResult(
        provider="spark_adversarial", available=completed, completed=completed,
        verdict="PASS" if completed else None, summary="stub",
    )


def test_build_e2b_failure_yields_failed(db_path, monkeypatch) -> None:
    from app.models.mission import MissionStatus

    from app.services import build as build_module
    from app.services import pipeline as pipeline_module

    _approved_mission(db_path)
    monkeypatch.setattr(build_module, "generate_application",
                        lambda spec: (_output(), {"input_tokens": 1}, "stub-model"))
    monkeypatch.setattr(pipeline_module, "verify_in_e2b", lambda files, acceptance_criteria=None: _stub_e2b(False))
    monkeypatch.setattr(pipeline_module, "active_auditor",
                        lambda: type("A", (), {"audit": staticmethod(lambda ev: _stub_audit(True))})())

    result = build_module.build_application("b1", db_path=db_path)
    assert result.status == MissionStatus.FAILED
    assert result.verification is not None and result.verification.reason == "runtime"
    assert result.generation is not None and result.generation.status.value == "generated"
    assert result.files is not None and any(p.endswith("src/App.tsx") for p in result.files)
    assert result.verification.coverage is not None
    assert len(result.verification.coverage.requirements) == 2


def test_build_audit_unavailable_yields_audit_limited(db_path, monkeypatch) -> None:
    from app.models.mission import MissionStatus

    from app.services import build as build_module
    from app.services import pipeline as pipeline_module

    _approved_mission(db_path)
    monkeypatch.setattr(build_module, "generate_application",
                        lambda spec: (_output(), {"input_tokens": 1}, "stub-model"))
    monkeypatch.setattr(pipeline_module, "verify_in_e2b", lambda files, acceptance_criteria=None: _stub_e2b(True))
    monkeypatch.setattr(pipeline_module, "active_auditor",
                        lambda: type("A", (), {"audit": staticmethod(lambda ev: _stub_audit(False))})())

    result = build_module.build_application("b1", db_path=db_path)
    assert result.status == MissionStatus.AUDIT_LIMITED
    assert result.verification is not None
    assert result.verification.coverage is not None
    assert len(result.verification.coverage.acceptance) == 2
