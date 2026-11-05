"""Verification data contracts and verdict logic.

These models are the machine-readable record of the VERIFY stage:
deterministic E2B runtime evidence plus one independent AI audit.
``compute_verdict`` is pure (no I/O) so the full decision matrix is
keyless-testable.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.generation import CoverageReport


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


class CheckCategory(str, Enum):
    BUILD = "build"
    RUNTIME = "runtime"
    FUNCTIONAL = "functional"
    QUALITY = "quality"


class VerificationCheck(BaseModel):
    """One executed deterministic check. A check is PASS only if it ran."""

    name: str
    category: CheckCategory
    status: CheckStatus
    required: bool = True
    detail: str = ""
    latency_s: float = 0.0
    #: Structured evidence extras. Lifecycle checks record the traversed
    #: status chain here (e.g. ["Received", "Preparing", "Ready"]) so
    #: coverage reads data, never parses the detail string.
    chain: list[str] = Field(default_factory=list)
    #: Per-hop durations in ms, in hop order (walker diagnostics).
    hop_durations_ms: list[int] = Field(default_factory=list)
    #: Wall-clock completion of this check (ISO-8601); "" when unknown
    #: (e.g. mapped browser checks, whose steps carry durations only).
    completed_at: str = ""


class BrowserStepStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    BLOCKED = "blocked"


class BrowserStep(BaseModel):
    name: str
    status: BrowserStepStatus
    detail: str = ""
    duration_ms: int = 0
    chain: list[str] = Field(default_factory=list)
    #: Per-hop durations in ms, in hop order (walker diagnostics).
    hop_durations_ms: list[int] = Field(default_factory=list)


class BrowserScreenshot(BaseModel):
    name: str
    data_url: str = ""


class BrowserTestResult(BaseModel):
    """Real browser execution evidence from inside the E2B sandbox."""

    available: bool
    browser: str = ""
    steps: list[BrowserStep] = Field(default_factory=list)
    screenshots: list[BrowserScreenshot] = Field(default_factory=list)
    error: str = ""
    latency_s: float = 0.0
    #: The run was cut short by sandbox transport (ConnectError, stream
    #: errors), not by an executed assertion. Distinct from available:
    #: a script that ran and found nothing is measured, not inconclusive.
    transport_failed: bool = False
    #: Automatic transport retries consumed by this run (0 or 1).
    transport_retries: int = 0


class E2BVerification(BaseModel):
    """Deterministic execution evidence from an E2B sandbox run."""

    available: bool
    passed: bool
    checks: list[VerificationCheck] = Field(default_factory=list)
    preview_url: str = ""
    sandbox_id: str = ""
    error: str = ""
    latency_s: float = 0.0
    browser: BrowserTestResult | None = None
    #: Sandbox transport (not the app) ended the run early. Drives the
    #: INCONCLUSIVE verdict; never set by executed assertion failures.
    transport_failed: bool = False
    #: Automatic transport retries consumed across this run.
    transport_retries: int = 0


class AuditVerdict(str, Enum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingCategory(str, Enum):
    REQUIREMENTS = "requirements"
    WORKFLOW = "workflow"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    UX = "ux"


class AuditFinding(BaseModel):
    severity: FindingSeverity
    category: FindingCategory
    finding: str
    evidence: str = ""
    recommendation: str = ""
    file: str = ""


class AuditResult(BaseModel):
    """Structured output of one auditor provider run."""

    provider: str
    available: bool
    completed: bool
    verdict: AuditVerdict | None = None
    summary: str = ""
    findings: list[AuditFinding] = Field(default_factory=list)
    model_id: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    error_class: str = ""
    latency_s: float = 0.0


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    VERIFIED_WITH_WARNINGS = "VERIFIED_WITH_WARNINGS"
    FAILED = "FAILED"
    AUDIT_LIMITED = "AUDIT_LIMITED"
    INCONCLUSIVE = "INCONCLUSIVE"


VerdictReason = Literal[
    "clean", "warnings", "runtime", "audit", "audit_unavailable", "e2b_unavailable",
    "transport",
]

BLOCKING_SEVERITIES = frozenset({FindingSeverity.CRITICAL, FindingSeverity.HIGH})


def summarize_e2b(checks: list[VerificationCheck]) -> bool:
    """True only when every required check ran and passed."""
    required = [c for c in checks if c.required]
    if not required:
        return False
    return all(c.status == CheckStatus.PASS for c in required)


def compute_verdict(
    e2b: E2BVerification,
    audit: AuditResult,
    has_coverage_gaps: bool = False,
) -> tuple[VerificationStatus, VerdictReason]:
    """Combine the two independent signals plus coverage into one status.

    - FAILED: a required check failed, a blocking (critical/high) audit
      finding exists, or E2B was unavailable. Lack of coverage evidence
      alone never yields FAILED.
    - INCONCLUSIVE: sandbox transport (not the app) ended the run early
      after the automatic retry. Executed required failures still win:
      observed app breakage is FAILED even when transport also failed.
    - AUDIT_LIMITED: the app works and deterministic checks pass, but the
      independent AI audit could not complete. Never used for Glimmer.
    - VERIFIED_WITH_WARNINGS: core checks pass with non-blocking
      findings, or any acceptance criterion is PARTIAL/UNVERIFIED (the
      criteria are named in coverage.acceptance).
    - VERIFIED: core checks pass, the audit is clean, and every
      acceptance criterion is SUPPORTED.
    """
    if not e2b.available:
        return VerificationStatus.FAILED, "e2b_unavailable"
    if any(c.required and c.status == CheckStatus.FAIL for c in e2b.checks):
        return VerificationStatus.FAILED, "runtime"
    if e2b.transport_failed:
        return VerificationStatus.INCONCLUSIVE, "transport"
    if not e2b.passed:
        return VerificationStatus.FAILED, "runtime"
    if not audit.available or not audit.completed:
        return VerificationStatus.AUDIT_LIMITED, "audit_unavailable"
    blocking = [f for f in audit.findings if f.severity in BLOCKING_SEVERITIES]
    if audit.verdict == AuditVerdict.FAIL or blocking:
        return VerificationStatus.FAILED, "audit"
    if audit.verdict == AuditVerdict.PASS_WITH_WARNINGS or audit.findings or has_coverage_gaps:
        return VerificationStatus.VERIFIED_WITH_WARNINGS, "warnings"
    return VerificationStatus.VERIFIED, "clean"


class VerificationResult(BaseModel):
    """Persisted VERIFY-stage outcome for one mission."""

    status: VerificationStatus
    reason: str = ""
    e2b: E2BVerification
    audit: AuditResult
    coverage: CoverageReport | None = None
    audit_provider_active: str = "spark_adversarial"
    audit_provider_target: str = "glimmer"
    started_at: str = ""
    completed_at: str = ""
