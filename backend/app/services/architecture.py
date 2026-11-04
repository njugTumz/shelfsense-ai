"""Deterministic architecture classification for generated applications.

Derived from approved-spec signals plus the model's NEEDS_BACKEND-style
disclosures — never from an unreliable model-generated schema. Pure (no
I/O), so every category is keyless-testable.

Precedence (highest wins; lesser signals are kept as reasons):
NEEDS_BACKEND > NEEDS_INTEGRATION > LOCAL_PERSISTENCE > FRONTEND_ONLY.
A shared backend is the foundation: when an app needs both shared state
and external systems, NEEDS_BACKEND is primary and the integration need
is recorded alongside it.
"""

from __future__ import annotations

from app.models.generation import (
    ArchitectureClass,
    ArchitectureClassification,
    GenerationOutput,
)
from app.models.interview import StructuredRequirements

NEEDS_BACKEND_PREFIX = "NEEDS_BACKEND:"


_BACKEND_SIGNALS = (
    "multi-user", "multi user", "shared persistence", "shared state",
    "shared order", "shared board", "shared between", "shared across",
    "user accounts", "user account", "authentication", "authorization",
    "sign in", "sign-in", "login", "server-side", "server side",
    "real-time sync", "realtime sync", "cloud database", "postgres",
    "shared backend", "backend api",
)

_INTEGRATION_SIGNALS = (
    "third-party", "third party", "external system", "external api",
    "payment provider", "payment gateway", "stripe", "paypal",
    "sms gateway", "email service", "sendgrid", "twilio",
    "webhook", "maps api", "weather api",
)

_PERSISTENCE_SIGNALS = (
    "persist", "localstorage", "local storage", "reload", "offline",
    "save data", "stored locally", "survive restart",
)


def _spec_text(spec: StructuredRequirements) -> str:
    parts: list[str] = [
        spec.business_context,
        spec.problem_statement,
        *spec.objectives,
        *spec.non_functional_requirements,
        *spec.core_features,
        *spec.acceptance_criteria,
        *spec.assumptions,
    ]
    parts.extend(r.requirement for r in spec.functional_requirements)
    parts.extend(f"{r.name} {r.description}" for r in spec.user_roles)
    return "\n".join(parts).lower()


def _backend_disclosures(output: GenerationOutput) -> list[str]:
    return [
        f"{item.requirement_id}: {item.reason}"
        for item in output.unimplemented
        if item.reason.startswith(NEEDS_BACKEND_PREFIX)
    ]


_LIMITATIONS = {
    ArchitectureClass.FRONTEND_ONLY: [
        "Prototype runs fully in the browser. No data is stored: everything resets on reload.",
    ],
    ArchitectureClass.LOCAL_PERSISTENCE: [
        "Prototype stores data only in this browser (localStorage). It is not shared "
        "between devices or users, is not backed up, and is not a production database.",
    ],
    ArchitectureClass.NEEDS_BACKEND: [
        "Requires a server API with shared persistence for multi-user operation. "
        "The generated prototype does not include a backend.",
    ],
    ArchitectureClass.NEEDS_INTEGRATION: [
        "Requires external systems or third-party services. Those integrations are "
        "not implemented in the prototype.",
    ],
}


def classify_architecture(
    spec: StructuredRequirements, output: GenerationOutput
) -> ArchitectureClassification:
    """Classify the architecture a spec+output implies. Deterministic."""
    text = _spec_text(spec)
    disclosures = _backend_disclosures(output)
    backend_hits = sorted({s for s in _BACKEND_SIGNALS if s in text})
    integration_hits = sorted({s for s in _INTEGRATION_SIGNALS if s in text})
    persistence_hits = sorted({s for s in _PERSISTENCE_SIGNALS if s in text})

    if disclosures or backend_hits:
        reasons = [f"model disclosure: {d}" for d in disclosures]
        reasons += [f"spec requires: {hit}" for hit in backend_hits]
        reasons += [f"also needs integration: {hit}" for hit in integration_hits]
        category = ArchitectureClass.NEEDS_BACKEND
    elif integration_hits:
        reasons = [f"spec requires external system: {hit}" for hit in integration_hits]
        category = ArchitectureClass.NEEDS_INTEGRATION
    elif persistence_hits:
        reasons = [f"spec requires persistence: {hit}" for hit in persistence_hits]
        category = ArchitectureClass.LOCAL_PERSISTENCE
    else:
        reasons = ["no persistence, backend, or integration signals in the spec"]
        category = ArchitectureClass.FRONTEND_ONLY
    return ArchitectureClassification(
        category=category, reasons=reasons,
        limitations=list(_LIMITATIONS[category]),
    )
