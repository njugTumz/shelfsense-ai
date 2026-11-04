"""One bounded repair-and-retest cycle (plan.md sections 10-11).

A FAILED mission whose failure the model can plausibly fix gets exactly
one automated repair: the developer agent receives the current sources
plus the observed failure evidence, returns a complete fixed file set
under the same manifest rules as a fresh build, and the existing
verification pipeline retests it. ``repair_eligibility`` is pure (no
I/O) so the whole decision matrix is keyless-testable.
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from app.core.store import load_mission, save_mission, utc_now_iso
from app.demo.restaurant_app import APP_ROOT
from app.demo.scaffold import scaffold_files
from app.models.generation import BLOCKED_FILES, RepairRecord, RepairStatus
from app.models.mission import Mission, MissionStatus
from app.models.verification import (
    BLOCKING_SEVERITIES,
    AuditVerdict,
    CheckStatus,
    VerificationCheck,
)
from app.services.build import IN_PROGRESS_STATES
from app.services.coverage import build_coverage_report
from app.services.developer import (
    GenerationValidationError,
    repair_application_files,
)
from app.services.interview import LifecycleError
from app.services.pipeline import run_mission_verification
from app.services.spark_client import SparkError

#: The MVP allows exactly one completed repair+retest cycle per mission.
MAX_REPAIR_ATTEMPTS = 1

#: Failed checks the model cannot fix (FIELD/infra owned).
INFRASTRUCTURE_CHECKS = frozenset({
    "sandbox_create",
    "node_runtime",
    "files_uploaded",
    "npm_install",
    "sandbox_exception",
})

#: Failed checks that point at generated app source (plus browser_*).
REPAIRABLE_RUNTIME_CHECKS = frozenset({
    "production_build",
    "preview_serves",
    "assets_resolve",
    "order_workflow_present",
    "status_transitions_execute",
})

GENERATED_PREFIX = APP_ROOT + "/src/"
FAILURE_DETAIL_BUDGET = 600


class RepairEligibility(BaseModel):
    """Pure eligibility decision for one mission."""

    eligible: bool
    reason: str = ""
    trigger: str = ""
    failures: str = ""


def _failed_required(checks: list[VerificationCheck]) -> list[VerificationCheck]:
    return [c for c in checks if c.required and c.status != CheckStatus.PASS]


def _is_repairable_check(name: str) -> bool:
    return name in REPAIRABLE_RUNTIME_CHECKS or name.startswith("browser_")


def repair_eligibility(mission: Mission) -> RepairEligibility:
    """Decide whether one repair+retest cycle may run (pure, no I/O)."""
    if mission.status != MissionStatus.FAILED:
        return RepairEligibility(
            eligible=False,
            reason="not_failed",
            failures=f"Repair needs a FAILED mission (mission is {mission.status.value}).",
        )
    if mission.verification is None:
        return RepairEligibility(
            eligible=False, reason="no_verification",
            failures="Repair needs a recorded verification result.",
        )
    attempts = mission.repair.attempts if mission.repair else 0
    if attempts >= MAX_REPAIR_ATTEMPTS:
        return RepairEligibility(
            eligible=False, reason="already_repaired",
            failures="The single repair-and-retest cycle was already used.",
        )
    verification = mission.verification
    if verification.reason == "runtime":
        failed = _failed_required(verification.e2b.checks)
        infra = sorted(c.name for c in failed if c.name in INFRASTRUCTURE_CHECKS)
        if infra:
            return RepairEligibility(
                eligible=False, reason="infrastructure",
                failures=(
                    "Failed checks are FIELD/infra owned, not model-fixable: "
                    + ", ".join(infra)
                ),
            )
        fixable = [c for c in failed if _is_repairable_check(c.name)]
        if not fixable:
            return RepairEligibility(
                eligible=False, reason="no_repairable_cause",
                failures="No failed check points at generated app source.",
            )
        lines = [
            f"- [{c.category.value}] {c.name}: {(c.detail or '(no detail)')[:FAILURE_DETAIL_BUDGET]}"
            for c in fixable
        ]
        trigger = "runtime:" + ",".join(sorted(c.name for c in fixable))
        return RepairEligibility(
            eligible=True, reason="runtime", trigger=trigger,
            failures="FAILED REQUIRED CHECKS\n" + "\n".join(lines),
        )
    if verification.reason == "audit":
        blocking = [
            f for f in verification.audit.findings if f.severity in BLOCKING_SEVERITIES
        ]
        manifest = set(mission.generation.manifest) if mission.generation else set()
        lines = []
        for finding in blocking:
            grounded = "grounded" if _finding_grounded(finding.file, manifest) else "ungrounded"
            lines.append(
                f"- [{finding.severity.value}/{finding.category.value}][{grounded}] "
                f"{finding.finding[:FAILURE_DETAIL_BUDGET]}"
                + (f" (file: {finding.file})" if finding.file else "")
                + (f" Recommendation: {finding.recommendation[:FAILURE_DETAIL_BUDGET]}"
                   if finding.recommendation else "")
            )
        if verification.audit.verdict == AuditVerdict.FAIL and not lines:
            lines.append(f"- audit verdict FAIL: {verification.audit.summary[:FAILURE_DETAIL_BUDGET]}")
        if not lines:
            return RepairEligibility(
                eligible=False, reason="no_repairable_cause",
                failures="Audit failure has no blocking findings to address.",
            )
        trigger = f"audit:{len(blocking)} blocking finding(s)"
        return RepairEligibility(
            eligible=True, reason="audit", trigger=trigger,
            failures="BLOCKING AUDIT FINDINGS\n" + "\n".join(lines),
        )
    return RepairEligibility(
        eligible=False, reason="unavailable_provider",
        failures=(
            f"Verification ended with '{verification.reason}': the failure is a "
            "missing/unavailable provider, not model-fixable app source."
        ),
    )


def _finding_grounded(reported_file: str, manifest: set[str]) -> bool:
    if not reported_file:
        return True
    short = reported_file
    if short.startswith(APP_ROOT + "/"):
        short = short[len(APP_ROOT) + 1 :]
    return short in manifest or any(
        path.endswith(short) or short.endswith(path) for path in manifest
    )


def _generated_sources(files: dict[str, str]) -> dict[str, str]:
    return {
        path[len(APP_ROOT) + 1 :]: content
        for path, content in files.items()
        if path.startswith(GENERATED_PREFIX)
        and (path[len(APP_ROOT) + 1 :]) not in BLOCKED_FILES
    }


def repair_application(mission_id: str, db_path=None) -> Mission:
    """Run one repair+retest cycle and return the mission with its new state.

    Raises KeyError for an unknown mission and LifecycleError when repair
    is not available (not FAILED, already used, infra failure). A repair
    whose generation fails before any retest returns the mission still
    FAILED with the real error and does not consume the single attempt.
    """
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if mission.status in IN_PROGRESS_STATES:
        raise LifecycleError(f"A build is already in progress (mission is {mission.status.value}).")
    decision = repair_eligibility(mission)
    if not decision.eligible:
        raise LifecycleError(f"Repair not available: {decision.failures}")
    if mission.spec is None:
        raise LifecycleError("Repair needs the approved specification.")
    current = _generated_sources(mission.files or {})
    if "src/App.tsx" not in current:
        raise LifecycleError("Repair needs generated app source to fix.")

    record = mission.repair or RepairRecord(status=RepairStatus.REPAIRING)
    record.status = RepairStatus.REPAIRING
    record.trigger = decision.trigger
    record.previous_status = mission.status.value
    record.previous_reason = mission.verification.reason if mission.verification else ""
    record.started_at = utc_now_iso()
    record.error = ""
    mission.repair = record
    mission.error = ""
    save_mission(mission, db_path)

    try:
        output, usage, model_id = repair_application_files(
            mission.spec, current, decision.failures
        )
    except (SparkError, GenerationValidationError, ValidationError) as exc:
        record.status = RepairStatus.FAILED
        record.completed_at = utc_now_iso()
        record.error = f"Repair generation failed: {exc}"[:2000]
        mission.repair = record
        mission.error = record.error
        return save_mission(mission, db_path)

    merged = scaffold_files()
    for item in output.files:
        merged[f"{APP_ROOT}/{item.path}"] = item.content
    mission.files = merged
    record.summary = output.summary
    record.manifest = [item.path for item in output.files]
    record.model_id = model_id
    record.usage = dict(usage)
    mission.repair = record
    save_mission(mission, db_path)

    mission = run_mission_verification(mission_id, db_path=db_path)
    record = mission.repair
    assert record is not None
    record.attempts += 1
    record.status = RepairStatus.REPAIRED
    record.completed_at = utc_now_iso()
    record.new_status = mission.status.value
    record.new_reason = mission.verification.reason if mission.verification else ""
    if mission.verification is not None and mission.spec is not None:
        mission.verification.coverage = build_coverage_report(
            mission.spec, output, mission.verification.e2b
        )
    if mission.status == MissionStatus.FAILED and mission.error:
        mission.error = (
            f"Repair attempted ({record.trigger}); retest still failed: {mission.error}"
        )[:2000]
    mission.repair = record
    return save_mission(mission, db_path)
