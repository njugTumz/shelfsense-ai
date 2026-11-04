"""Keyless tests: deterministic architecture classification.

Run from backend/: python -m pytest tests/test_architecture.py
Classification is derived from spec signals plus NEEDS_BACKEND disclosures —
never from an unreliable model-generated schema.
"""

from app.models.generation import GenerationOutput
from app.models.interview import StructuredRequirements
from app.services.architecture import ArchitectureClass, classify_architecture


def _spec(text_fields=None, **overrides):
    base = {
        "business_context": "ctx",
        "problem_statement": "A business problem.",
        "functional_requirements": [{"id": "FR-1", "requirement": "Do the thing."}],
    }
    base.update(overrides)
    if text_fields:
        base.update(text_fields)
    return StructuredRequirements.model_validate(base)


def _output(unimplemented=None):
    return GenerationOutput.model_validate({
        "summary": "s",
        "files": [{"path": "src/App.tsx", "content": "ok"}],
        "feature_map": [],
        "unimplemented": unimplemented or [],
        "assumptions": [],
    })


def test_frontend_only_when_no_persistence_signals() -> None:
    result = classify_architecture(_spec(), _output())
    assert result.category == ArchitectureClass.FRONTEND_ONLY
    assert any("resets on reload" in limit for limit in result.limitations)


def test_local_persistence_from_reload_criteria() -> None:
    spec = _spec(acceptance_criteria=["After page reload all orders remain visible."])
    result = classify_architecture(spec, _output())
    assert result.category == ArchitectureClass.LOCAL_PERSISTENCE
    assert any("not shared" in limit for limit in result.limitations)
    assert any("not a production database" in limit for limit in result.limitations)


def test_needs_backend_from_disclosure() -> None:
    output = _output([{"requirement_id": "FR-1",
                       "reason": "NEEDS_BACKEND: shared multi-user order board"}])
    result = classify_architecture(_spec(), output)
    assert result.category == ArchitectureClass.NEEDS_BACKEND
    assert any("FR-1" in reason for reason in result.reasons)


def test_needs_backend_from_spec_language() -> None:
    spec = _spec(non_functional_requirements=["Support multiple cashiers with user accounts."])
    result = classify_architecture(spec, _output())
    assert result.category == ArchitectureClass.NEEDS_BACKEND


def test_needs_integration_from_external_signals() -> None:
    spec = _spec(functional_requirements=[
        {"id": "FR-9", "requirement": "Send SMS receipts via a third-party gateway."}])
    result = classify_architecture(spec, _output())
    assert result.category == ArchitectureClass.NEEDS_INTEGRATION
    assert any("not implemented" in limit for limit in result.limitations)


def test_backend_takes_precedence_over_integration() -> None:
    spec = _spec(
        functional_requirements=[
            {"id": "FR-9", "requirement": "Charge cards via the payment provider API."}],
        non_functional_requirements=["Shared order state across all waiter devices."],
    )
    result = classify_architecture(spec, _output())
    assert result.category == ArchitectureClass.NEEDS_BACKEND
    assert any("integration" in reason.lower() for reason in result.reasons)


def test_never_claims_production_persistence() -> None:
    for spec, output in [
        (_spec(), _output()),
        (_spec(acceptance_criteria=["Orders persist after reload."]), _output()),
    ]:
        result = classify_architecture(spec, _output())
        joined = " ".join(result.limitations).lower()
        assert "production database" not in joined or "not a production database" in joined
        assert "multi-user operation" not in joined or "does not" in joined or "no " in joined


def test_classification_surfaced_in_coverage_report() -> None:
    from app.models.verification import E2BVerification
    from app.services.coverage import build_coverage_report

    spec = _spec(acceptance_criteria=["Orders persist after reload."])
    report = build_coverage_report(spec, _output(), E2BVerification(available=True, passed=True))
    assert report.architecture is not None
    assert report.architecture.category == ArchitectureClass.LOCAL_PERSISTENCE
