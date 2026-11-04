"""VERIFY-stage pipeline: E2B deterministic checks, then one AI audit.

RUNNING (E2B) -> AUDITING (active auditor) -> terminal verification
status. Every transition is persisted so a failed stage can be retried
without repeating earlier work.
"""

from __future__ import annotations

from app.core.store import load_mission, save_mission, utc_now_iso
from app.demo.restaurant_app import (
    APP_ROOT,
    DEMO_ACCEPTANCE_CRITERIA,
    DEMO_REQUIREMENTS,
    demo_files,
)
from app.models.generation import GeneratedFile, GenerationOutput
from app.models.mission import Mission, MissionStatus
from app.models.verification import VerificationResult, compute_verdict
from app.services.auditors import (
    ACTIVE_AUDITOR_NAME,
    TARGET_AUDITOR_NAME,
    AuditEvidence,
    active_auditor,
)
from app.services.coverage import build_coverage_report
from app.services.e2b_verification import verify_in_e2b


def _short_path(path: str) -> str:
    return path[len(APP_ROOT) + 1 :] if path.startswith(APP_ROOT + "/") else path


def reconstruct_output(mission: Mission) -> GenerationOutput | None:
    """Rebuild the developer output from stored generation + files.

    Lets re-verification (which rebuilds VerificationResult from scratch)
    keep the coverage report — including the architecture classification —
    instead of dropping it. Returns None when the mission predates stored
    manifests (demo/legacy missions keep coverage=None).
    """
    record = mission.generation
    manifest = list(record.manifest) if record is not None else []
    if not manifest or not mission.files:
        return None
    try:
        files = [
            GeneratedFile(path=path, content=mission.files[f"{APP_ROOT}/{path}"])
            for path in manifest
        ]
    except KeyError:
        return None
    return GenerationOutput(
        summary=record.summary,
        files=files,
        feature_map=list(record.feature_map),
        unimplemented=list(record.unimplemented),
        assumptions=list(record.assumptions),
    )


def run_mission_verification(
    mission_id: str,
    files: dict[str, str] | None = None,
    requirements: str | None = None,
    acceptance_criteria: list[str] | None = None,
    use_demo_app: bool = False,
    db_path=None,
) -> Mission:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")

    resolved = dict(files) if files else None
    if resolved is None and use_demo_app:
        resolved = demo_files()
    if resolved is None:
        resolved = dict(mission.files) if mission.files else None
    if not resolved:
        raise ValueError(
            "No application files to verify: pass files, use_demo_app=true, "
            "or verify a mission that already has files."
        )

    mission.files = resolved
    if requirements is not None:
        mission.requirements = requirements
    elif use_demo_app and not mission.requirements:
        mission.requirements = DEMO_REQUIREMENTS
    if acceptance_criteria is not None:
        mission.acceptance_criteria = acceptance_criteria
    elif use_demo_app and not mission.acceptance_criteria:
        mission.acceptance_criteria = list(DEMO_ACCEPTANCE_CRITERIA)
    mission.error = ""

    run_started = utc_now_iso()
    mission.status = MissionStatus.RUNNING
    save_mission(mission, db_path)

    def report_progress(step: str, label: str) -> None:
        current = load_mission(mission_id, db_path)
        if current is None:
            return
        current.progress_step = step
        current.progress_label = label
        save_mission(current, db_path)

    e2b = verify_in_e2b(
        resolved, acceptance_criteria=mission.acceptance_criteria, progress=report_progress
    )

    mission.status = MissionStatus.AUDITING
    mission.progress_step = "auditing"
    mission.progress_label = "Running the independent Muse Spark audit…"
    save_mission(mission, db_path)

    check_lines = [
        f"- [{c.status.value}] {c.category.value}/{c.name}"
        + (f" (optional)" if not c.required else "")
        + (f": {c.detail}" if c.detail else "")
        for c in e2b.checks
    ]
    evidence = AuditEvidence(
        requirements=mission.requirements,
        acceptance_criteria=mission.acceptance_criteria,
        file_list=sorted(_short_path(p) for p in resolved),
        sources={_short_path(p): content for p, content in resolved.items()},
        e2b_summary="\n".join(check_lines) if check_lines else "(no checks ran)",
        preview_available=bool(e2b.preview_url)
        and any(c.name == "preview_serves" and c.status.value == "pass" for c in e2b.checks),
    )
    audit = active_auditor().audit(evidence)

    status, reason = compute_verdict(e2b, audit)
    mission.status = MissionStatus(status.value)
    mission.verification = VerificationResult(
        status=status,
        reason=reason,
        e2b=e2b,
        audit=audit,
        audit_provider_active=ACTIVE_AUDITOR_NAME,
        audit_provider_target=TARGET_AUDITOR_NAME,
        started_at=run_started,
        completed_at=utc_now_iso(),
    )
    if mission.spec is not None:
        output = reconstruct_output(mission)
        if output is not None:
            mission.verification.coverage = build_coverage_report(
                mission.spec, output, e2b
            )
    if status == MissionStatus.FAILED:
        if reason == "e2b_unavailable":
            mission.error = e2b.error
        elif reason == "runtime":
            failed = [c.name for c in e2b.checks if c.required and c.status.value != "pass"]
            mission.error = f"Runtime verification failed: {', '.join(failed)}. {e2b.error}".strip()
        else:
            blocking = [f for f in audit.findings if f.severity.value in ("critical", "high")]
            mission.error = (
                f"Audit blocked verification: {len(blocking)} critical/high finding(s). "
                f"{audit.summary}"
            ).strip()
    elif status == MissionStatus.AUDIT_LIMITED:
        mission.error = f"Independent AI audit unavailable: {audit.error}".strip()
    mission.progress_step = ""
    mission.progress_label = ""
    save_mission(mission, db_path)
    return mission
