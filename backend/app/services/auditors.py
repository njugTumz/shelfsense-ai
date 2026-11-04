"""Auditor providers for the VERIFY stage.

``Auditor`` is the smallest plug-in point that lets Muse Glimmer return
later without rewriting verification: one interface, one active provider
today (adversarial Muse Spark), one preserved-but-unavailable provider
(Muse Glimmer).

HARD RULES (do not weaken):
- The Glimmer provider MUST NOT call Modal. It reports unavailable with
  the honest reason. Glimmer is NOT VERIFIED.
- Spark findings MUST NEVER be labeled as Glimmer findings.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.models.verification import (
    AuditFinding,
    AuditResult,
    AuditVerdict,
)
from app.services.spark_client import SparkError, responses_structured

GLIMMER_NOT_VERIFIED = (
    "NOT VERIFIED — Modal GPU/account limit prevented genuine inference. "
    "The Glimmer/Modal spike code is preserved under scripts/smoke/ for a "
    "future retry; this provider makes no Modal calls."
)

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "PASS_WITH_WARNINGS", "FAIL"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "category": {
                        "type": "string",
                        "enum": ["requirements", "workflow", "implementation", "testing", "ux"],
                    },
                    "finding": {"type": "string"},
                    "evidence": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "file": {"type": "string"},
                },
                "required": ["severity", "category", "finding", "evidence", "recommendation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdict", "summary", "findings"],
    "additionalProperties": False,
}

AUDIT_SYSTEM = """\
You are an independent software auditor reviewing the generated application \
described by the requirements below. You did NOT build this application, you \
have no access to the builder's reasoning, and you must not assume the \
builder is correct. Review ONLY the evidence below and report what you can \
support.

Severity guide:
- critical: the app cannot fulfill a core requirement, or a workflow is broken.
- high: a requirement is partially implemented or an important interaction fails.
- medium: a real defect with a workaround, or untested-but-risky behavior.
- low: minor issues only. Judge the interface against the requested \
workflows: broken navigation, dead controls, or unreadable layouts that \
block the workflows are functional defects; pure polish preferences are \
low at most and never critical or high on aesthetics alone.

Rules:
- Every finding needs concrete evidence quoted from the sources or test results.
- Do not invent files, APIs, or behaviors not present in the evidence.
- Deterministic test evidence is authoritative: if it contradicts the \
implementation, say so, and never let a visual impression override an \
executed test result in either direction.
- Note areas the tests did not actually cover (testing-category findings).
- If the evidence genuinely supports a clean bill, return verdict PASS with \
an empty findings list rather than inventing issues.
"""

#: Read budget for the adversarial audit (medium effort over large evidence).
AUDIT_TIMEOUT_S = 300.0

SOURCE_FILE_BUDGET = 6000
SOURCE_TOTAL_BUDGET = 20000
PRIORITY_SOURCES = ("src/App.tsx", "src/lib/orderLogic.js", "package.json", "index.html")


class AuditEvidence(BaseModel):
    """Everything the auditor may inspect. No builder reasoning included."""

    requirements: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    file_list: list[str] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)
    e2b_summary: str = ""
    preview_available: bool = False


class Auditor(ABC):
    """One independent review provider."""

    name: str = ""

    @abstractmethod
    def available(self) -> bool:
        """True when a real audit call can be attempted right now."""

    @abstractmethod
    def audit(self, evidence: AuditEvidence) -> AuditResult:
        """Run one genuine audit and return the structured result."""


def _render_evidence(evidence: AuditEvidence) -> str:
    criteria = "\n".join(f"- {c}" for c in evidence.acceptance_criteria) or "(none stated)"
    files = "\n".join(f"- {path}" for path in evidence.file_list) or "(none)"
    ordered = sorted(
        evidence.sources.items(),
        key=lambda kv: (
            next(
                (i for i, p in enumerate(PRIORITY_SOURCES) if kv[0].endswith(p)),
                len(PRIORITY_SOURCES),
            ),
            kv[0],
        ),
    )
    chunks: list[str] = []
    total = 0
    for path, content in ordered:
        snippet = content[:SOURCE_FILE_BUDGET]
        if total + len(snippet) > SOURCE_TOTAL_BUDGET:
            chunks.append(f"--- {path} (omitted: evidence budget reached) ---")
            continue
        total += len(snippet)
        truncated = " [truncated]" if len(content) > SOURCE_FILE_BUDGET else ""
        chunks.append(f"--- {path}{truncated} ---\n{snippet}")
    sources = "\n\n".join(chunks) or "(no source provided)"
    return (
        f"APPROVED REQUIREMENTS\n{evidence.requirements or '(none stated)'}\n\n"
        f"ACCEPTANCE CRITERIA\n{criteria}\n\n"
        f"GENERATED FILE LIST\n{files}\n\n"
        f"GENERATED SOURCE\n{sources}\n\n"
        f"DETERMINISTIC E2B TEST RESULTS (these checks really executed)\n"
        f"{evidence.e2b_summary or '(no test evidence)'}\n\n"
        f"RUNNING PREVIEW: {'served HTTP 200 during verification' if evidence.preview_available else 'not available'}"
    )


def _validate_audit_payload(data: dict) -> tuple[AuditVerdict, str, list[AuditFinding]]:
    verdict = AuditVerdict(data["verdict"])
    summary = str(data.get("summary", ""))
    findings = [AuditFinding.model_validate(f) for f in data.get("findings", [])]
    return verdict, summary, findings


class SparkAdversarialAuditor(Auditor):
    """Independent review via Muse Spark (Responses API, structured output).

    Uses the verified request shape from docs/integration-notes.md:
    ``POST /responses`` with ``text.format`` JSON schema. Model ID comes
    from settings (never hardcoded, never guessed).
    """

    name = "spark_adversarial"

    def available(self) -> bool:
        return get_settings().meta_configured

    def audit(self, evidence: AuditEvidence) -> AuditResult:
        started = time.perf_counter()
        settings = get_settings()
        if not settings.meta_configured:
            return AuditResult(
                provider=self.name,
                available=False,
                completed=False,
                error="Spark audit unavailable: META_MODEL_API_KEY or MUSE_SPARK_MODEL_ID is not configured.",
                latency_s=round(time.perf_counter() - started, 1),
            )
        prompt = _render_evidence(evidence)
        base_input = f"{AUDIT_SYSTEM}\n\nEVIDENCE\n{prompt}"
        try:
            data, usage = responses_structured(
                schema_name="AuditReport",
                schema=AUDIT_SCHEMA,
                input_text=base_input,
                effort="medium",
                timeout_s=AUDIT_TIMEOUT_S,
            )
            try:
                verdict, summary, findings = _validate_audit_payload(data)
            except (KeyError, ValidationError) as exc:
                data, usage = responses_structured(
                    schema_name="AuditReport",
                    schema=AUDIT_SCHEMA,
                    input_text=(
                        f"{base_input}\n\nYour previous reply did not match the "
                        f"AuditReport shape ({exc}). Reply again with ONLY valid "
                        f"AuditReport JSON."
                    ),
                    effort="medium",
                timeout_s=AUDIT_TIMEOUT_S,
                )
                verdict, summary, findings = _validate_audit_payload(data)
        except (SparkError, KeyError, ValidationError) as exc:
            return AuditResult(
                provider=self.name,
                available=True,
                completed=False,
                model_id=settings.muse_spark_model_id,
                error=f"Spark audit failed: {exc}"[:2000],
                error_class=type(exc).__name__,
                latency_s=round(time.perf_counter() - started, 1),
            )
        return AuditResult(
            provider=self.name,
            available=True,
            completed=True,
            verdict=verdict,
            summary=summary,
            findings=findings,
            model_id=settings.muse_spark_model_id,
            usage=usage,
            latency_s=round(time.perf_counter() - started, 1),
        )


class GlimmerAuditor(Auditor):
    """Preserved Glimmer provider. Reports unavailable; never calls Modal.

    Glimmer remains the TARGET auditor. It becomes active only after a
    genuine inference is observed in a future retry of scripts/smoke/.
    """

    name = "glimmer"

    def available(self) -> bool:
        return False

    def audit(self, evidence: AuditEvidence) -> AuditResult:  # noqa: ARG002
        return AuditResult(
            provider=self.name,
            available=False,
            completed=False,
            error=GLIMMER_NOT_VERIFIED,
        )


ACTIVE_AUDITOR_NAME = SparkAdversarialAuditor.name
TARGET_AUDITOR_NAME = GlimmerAuditor.name


def get_auditor(name: str) -> Auditor:
    providers = {
        SparkAdversarialAuditor.name: SparkAdversarialAuditor(),
        GlimmerAuditor.name: GlimmerAuditor(),
    }
    if name not in providers:
        raise KeyError(f"Unknown auditor provider: {name}")
    return providers[name]


def active_auditor() -> Auditor:
    """The auditor used by the current verified runtime path."""
    return get_auditor(ACTIVE_AUDITOR_NAME)
