"""Mission API: create, retrieve, and verify missions. Never returns secrets."""

import uuid

from fastapi import APIRouter, File, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app.core.store import load_mission, save_mission, utc_now_iso
from app.demo.restaurant_app import (
    DEMO_ACCEPTANCE_CRITERIA,
    DEMO_REQUIREMENTS,
    demo_files,
)
from app.core.config import get_settings
from app.models.generation import GenerationRecord
from app.models.interview import (
    EvidenceItem,
    InterviewState,
    StructuredRequirements,
)
from app.models.media import MediaRecord
from app.models.jobs import JobRecord
from app.models.mission import Mission, MissionStatus
from app.services.build import build_application, cancel_build
from app.services.jobs import active_job
from app.services.repair import repair_application
from app.services.interview import (
    LifecycleError,
    answer_question,
    start_interview,
)
from app.services.media import (
    MediaUploadError,
    get_frame_bytes,
    process_media,
    save_upload,
)
from app.services.pipeline import run_mission_verification
from app.services.preview import PreviewError, launch_preview
from app.services.requirements import (
    approve_requirements,
    generate_requirements,
)
from app.services.spark_client import SparkError

router = APIRouter(prefix="/api/missions", tags=["missions"])


class CreateMissionRequest(BaseModel):
    name: str = ""


class VerifyRequest(BaseModel):
    """One of: explicit files, use_demo_app, or the mission's stored files."""

    files: dict[str, str] | None = None
    requirements: str | None = None
    acceptance_criteria: list[str] | None = None
    use_demo_app: bool = False


@router.post("", response_model=Mission, status_code=201)
def create_mission(body: CreateMissionRequest) -> Mission:
    now = utc_now_iso()
    mission = Mission(
        id=uuid.uuid4().hex[:12],
        name=body.name,
        status=MissionStatus.CREATED,
        created_at=now,
        updated_at=now,
    )
    return save_mission(mission)


@router.post("/demo", response_model=Mission, status_code=201)
def create_demo_mission() -> Mission:
    """Create a mission pre-loaded with the reference restaurant app.

    The reference app is a controlled starter for the verification demo;
    it is not claimed to be model-generated.
    """
    now = utc_now_iso()
    mission = Mission(
        id=uuid.uuid4().hex[:12],
        name="Restaurant demo",
        status=MissionStatus.CREATED,
        created_at=now,
        updated_at=now,
        requirements=DEMO_REQUIREMENTS,
        acceptance_criteria=list(DEMO_ACCEPTANCE_CRITERIA),
        files=demo_files(),
    )
    return save_mission(mission)


@router.get("/{mission_id}", response_model=Mission)
def get_mission(mission_id: str) -> Mission:
    mission = load_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Unknown mission.")
    return mission


@router.post("/{mission_id}/verify", response_model=Mission)
def verify_mission(mission_id: str, body: VerifyRequest) -> Mission:
    """Run deterministic E2B verification plus the independent AI audit.

    Synchronous by design for the MVP: E2B build/run takes a few minutes.
    """
    if load_mission(mission_id) is None:
        raise HTTPException(status_code=404, detail="Unknown mission.")
    try:
        return run_mission_verification(
            mission_id,
            files=body.files,
            requirements=body.requirements,
            acceptance_criteria=body.acceptance_criteria,
            use_demo_app=body.use_demo_app,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class StartInterviewRequest(BaseModel):
    owner_context: str = ""
    use_demo_evidence: bool = False


class AnswerRequest(BaseModel):
    answer: str


class ChangeRequest(BaseModel):
    feedback: str


class InterviewView(BaseModel):
    interview: InterviewState
    evidence: list[EvidenceItem]


def _mission_or_404(mission_id: str) -> Mission:
    mission = load_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Unknown mission.")
    return mission


@router.post("/{mission_id}/interview/start", response_model=Mission)
def api_start_interview(mission_id: str, body: StartInterviewRequest) -> Mission:
    _mission_or_404(mission_id)
    try:
        return start_interview(
            mission_id,
            owner_context=body.owner_context,
            use_demo_evidence=body.use_demo_evidence,
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SparkError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{mission_id}/interview", response_model=InterviewView)
def api_get_interview(mission_id: str) -> InterviewView:
    mission = _mission_or_404(mission_id)
    return InterviewView(interview=mission.interview, evidence=mission.evidence)


@router.post("/{mission_id}/interview/answer", response_model=Mission)
def api_answer_question(mission_id: str, body: AnswerRequest) -> Mission:
    _mission_or_404(mission_id)
    try:
        return answer_question(mission_id, body.answer)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SparkError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{mission_id}/requirements/generate", response_model=Mission)
def api_generate_requirements(mission_id: str) -> Mission:
    _mission_or_404(mission_id)
    try:
        return generate_requirements(mission_id)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SparkError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{mission_id}/requirements", response_model=StructuredRequirements)
def api_get_requirements(mission_id: str) -> StructuredRequirements:
    mission = _mission_or_404(mission_id)
    if mission.spec is None:
        raise HTTPException(status_code=404, detail="No requirements generated yet.")
    return mission.spec


@router.post("/{mission_id}/requirements/changes", response_model=Mission)
def api_request_changes(mission_id: str, body: ChangeRequest) -> Mission:
    _mission_or_404(mission_id)
    try:
        return generate_requirements(mission_id, change_request=body.feedback)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SparkError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{mission_id}/approve", response_model=Mission)
def api_approve_requirements(mission_id: str) -> Mission:
    _mission_or_404(mission_id)
    try:
        return approve_requirements(mission_id)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class BuildRequest(BaseModel):
    force: bool = False


@router.post("/{mission_id}/build", response_model=Mission)
def api_build_application(
    mission_id: str,
    body: BuildRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Mission:
    """Generate the app from the approved spec, run it in E2B, verify it.

    Synchronous by design for the MVP: generation plus E2B build/run
    takes several minutes. Generation failure returns the mission with
    its previous status and the real error (safe to retry). Pass a
    client-generated Idempotency-Key per user action: network retries
    and double submits with the same key return current state instead
    of starting duplicate work.
    """
    _mission_or_404(mission_id)
    try:
        return build_application(mission_id, force=body.force, idempotency_key=idempotency_key)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{mission_id}/build/job", response_model=JobRecord)
def api_build_job(mission_id: str) -> JobRecord:
    """The mission's active background build job (worker liveness).

    404 when no job is active (synchronous builds, finished runs, or
    missions that never built). The UI reads status/heartbeat_at to
    show worker activity; heartbeats older than the worker's stale
    threshold mean the build may be stuck.
    """
    _mission_or_404(mission_id)
    job = active_job(mission_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No active build job.")
    return job


@router.post("/{mission_id}/build/cancel", response_model=Mission)
def api_cancel_build(mission_id: str) -> Mission:
    """Cancel the mission's active background build.

    A queued build never starts; a running one stops at the next stage
    boundary and the mission returns to APPROVED. Synchronous in-flight
    builds cannot be cancelled (409).
    """
    _mission_or_404(mission_id)
    try:
        return cancel_build(mission_id)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{mission_id}/preview", response_model=Mission)
def api_launch_preview(mission_id: str) -> Mission:
    """Launch a temporary live preview of the mission's stored generated files.

    Synchronous by design for the MVP: a fresh E2B sandbox plus npm
    install/build takes about a minute. Runs no models: the stored files
    are served as-is. Reuses an active preview instead of launching a
    duplicate; an expired sandbox is cleaned up before replacement.
    """
    _mission_or_404(mission_id)
    try:
        return launch_preview(mission_id)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{mission_id}/generation", response_model=GenerationRecord)
def api_get_generation(mission_id: str) -> GenerationRecord:
    mission = _mission_or_404(mission_id)
    if mission.generation is None:
        raise HTTPException(status_code=404, detail="No generation record yet.")
    return mission.generation


@router.post("/{mission_id}/repair", response_model=Mission)
def api_repair_application(mission_id: str) -> Mission:
    """Run the single repair-and-retest cycle for a FAILED mission.

    Synchronous by design for the MVP: one developer repair call plus a
    full E2B re-verify takes several minutes. Refused with 409 when the
    mission is not FAILED, the attempt was used, or the failure is not
    model-fixable. A repair whose generation fails returns the mission
    still FAILED with the real error (safe to retry).
    """
    _mission_or_404(mission_id)
    try:
        return repair_application(mission_id)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{mission_id}/video", response_model=Mission)
def api_upload_video(mission_id: str, file: UploadFile = File(...)) -> Mission:
    """Upload a business video. Validated, size-capped, stored privately."""
    _mission_or_404(mission_id)
    limit = get_settings().max_upload_size_mb * 1_000_000
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = file.file.read(1_000_000)
        if not chunk:
            break
        received += len(chunk)
        if received > limit + 1:
            raise HTTPException(
                status_code=413,
                detail=f"Video exceeds the {get_settings().max_upload_size_mb} MB limit.",
            )
        chunks.append(chunk)
    try:
        return save_upload(
            mission_id,
            original_filename=file.filename or "",
            content_type=file.content_type or "",
            data=b"".join(chunks),
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MediaUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{mission_id}/analyze", response_model=Mission)
def api_analyze_video(mission_id: str) -> Mission:
    """Extract frames/audio, transcribe, analyze visuals, synthesize evidence.

    Synchronous by design for the MVP (minutes). Idempotent when ready;
    resumes from the first incomplete stage after failure.
    """
    _mission_or_404(mission_id)
    try:
        return process_media(mission_id)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{mission_id}/media", response_model=MediaRecord)
def api_get_media(mission_id: str) -> MediaRecord:
    mission = _mission_or_404(mission_id)
    if mission.media is None:
        raise HTTPException(status_code=404, detail="No video uploaded yet.")
    return mission.media


@router.get("/{mission_id}/media/frames/{frame_id}")
def api_get_frame(mission_id: str, frame_id: str) -> Response:
    """Serve one extracted frame thumbnail (mission-scoped, validated)."""
    try:
        data = get_frame_bytes(mission_id, frame_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg")
