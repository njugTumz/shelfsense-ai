"""Build orchestration: APPROVED -> generated app -> E2B -> verdict.

Steps: confirm approval -> generate via Spark -> validate manifest ->
merge FIELD scaffold -> run the existing verification pipeline ->
append the coverage report. Generation failure restores the previous
status (APPROVED, or the terminal state on forced rebuild) with the
real error, so retry is always safe and nothing is half-built.

Safe retry behavior:
- APPROVED with no/failed generation: build runs.
- BUILDING/RUNNING/AUDITING: 409, a build is already in progress.
- Terminal verification state: 409 unless force=true (explicit rebuild).
"""

from __future__ import annotations

from pydantic import ValidationError

from app.core.store import load_mission, save_mission, utc_now_iso
from app.demo.restaurant_app import APP_ROOT
from app.demo.scaffold import scaffold_files
from app.models.generation import GenerationRecord, GenerationStatus
from app.models.mission import Mission, MissionStatus
from app.services.coverage import build_coverage_report
from app.services.developer import GenerationValidationError, generate_application
from app.services.interview import LifecycleError
from app.services.pipeline import run_mission_verification
from app.services.spark_client import SparkError

TERMINAL_VERIFICATION_STATES = frozenset({
    MissionStatus.VERIFIED,
    MissionStatus.VERIFIED_WITH_WARNINGS,
    MissionStatus.FAILED,
    MissionStatus.AUDIT_LIMITED,
})
IN_PROGRESS_STATES = frozenset({
    MissionStatus.BUILDING,
    MissionStatus.RUNNING,
    MissionStatus.AUDITING,
})


def build_application(mission_id: str, force: bool = False, db_path=None) -> Mission:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if mission.status in IN_PROGRESS_STATES:
        raise LifecycleError(f"A build is already in progress (mission is {mission.status.value}).")
    if mission.status in TERMINAL_VERIFICATION_STATES and not force:
        raise LifecycleError(
            f"Mission already has verification result {mission.status.value}; "
            "pass force=true to rebuild."
        )
    if mission.status != MissionStatus.APPROVED and mission.status not in TERMINAL_VERIFICATION_STATES:
        raise LifecycleError(
            f"Build needs an approved specification (mission is {mission.status.value})."
        )
    if mission.spec is None:
        raise LifecycleError("Build needs an approved specification first.")

    previous_status = mission.status
    spec = mission.spec
    mission.status = MissionStatus.BUILDING
    mission.error = ""
    record = GenerationRecord(status=GenerationStatus.GENERATING, started_at=utc_now_iso())
    mission.generation = record
    save_mission(mission, db_path)

    try:
        output, usage, model_id = generate_application(spec)
    except (SparkError, GenerationValidationError, ValidationError) as exc:
        record.status = GenerationStatus.FAILED
        record.completed_at = utc_now_iso()
        record.error = f"Generation failed: {exc}"[:2000]
        mission.generation = record
        mission.status = previous_status
        mission.error = record.error
        return save_mission(mission, db_path)

    merged = scaffold_files()
    for item in output.files:
        merged[f"{APP_ROOT}/{item.path}"] = item.content
    mission.files = merged
    record.status = GenerationStatus.GENERATED
    record.completed_at = utc_now_iso()
    record.model_id = model_id
    record.usage = dict(usage)
    record.summary = output.summary
    record.manifest = [item.path for item in output.files]
    record.feature_map = list(output.feature_map)
    record.unimplemented = list(output.unimplemented)
    record.assumptions = list(output.assumptions)
    mission.generation = record
    save_mission(mission, db_path)

    mission = run_mission_verification(mission_id, db_path=db_path)
    if mission.verification is not None:
        mission.verification.coverage = build_coverage_report(
            spec, output, mission.verification.e2b
        )
        save_mission(mission, db_path)
    return mission
