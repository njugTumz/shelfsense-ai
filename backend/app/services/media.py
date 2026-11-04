"""Video upload and multimodal evidence pipeline (plan.md section 7).

UPLOAD -> EXTRACT (ffmpeg) -> TRANSCRIBE (Voice Transcribe) ->
ANALYZE (Spark vision) -> SYNTHESIZE (categorized evidence). Each stage
persists its status so the UI shows real progress and failed runs resume
from the first incomplete stage. Nothing here fabricates evidence:
unresolvable frame references are dropped and noted, and provider
failures land in ``media.error`` with status FAILED.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.store import load_mission, save_mission, utc_now_iso
from app.models.interview import EvidenceCategory, EvidenceItem
from app.models.media import (
    FrameRecord,
    MediaRecord,
    MediaStatus,
    TranscriptRecord,
    TranscriptTurn,
    VisualHypothesis,
    VisualObservation,
)
from app.models.mission import Mission, MissionStatus
from app.services.interview import LifecycleError
from app.services.spark_client import (
    SparkError,
    SparkUnavailableError,
    http_post_with_retry,
    responses_structured_multimodal,
)

ALLOWED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"})
N_FRAMES = 6
TRANSCRIBE_RATE = 24000
MAX_TRANSCRIBE_BYTES = 32_000_000
MAX_TRANSCRIBE_SECONDS = 600
TRANSCRIBE_MODE = "ENDPOINTING"

#: Read budget for 6-frame visual analysis (low effort, but large
#: multimodal payload and variable vision latency).
VISION_TIMEOUT_S = 300.0

#: Transcription transport: short connect, generous read for long audio,
#: long write for multi-MB WAV uploads.
TRANSCRIBE_CONNECT_S = 10.0
TRANSCRIBE_READ_S = 300.0
TRANSCRIBE_WRITE_S = 120.0

VISION_SYSTEM = """\
You are FIELD's visual analyst. Describe ONLY what is directly visible in \
the supplied video frames. Each frame label gives its id (f1, f2, ...) and \
timestamp in the source video.

Rules:
- Every observation must be visibly present in the frames it references. \
Reference frames by their ids.
- Never describe spoken content, off-screen events, exact quantities, \
prices, or identities you cannot see.
- Put interpretations and possible problems in hypotheses, never in \
observations, and ground each hypothesis in the frames it comes from.
- Confidence is your own certainty (low/medium/high).
- Reply with ONLY valid FrameAnalysis JSON.
"""

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "observation": {"type": "string"},
                    "frame_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["observation", "frame_ids", "confidence"],
                "additionalProperties": False,
            },
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "basis_frame_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["hypothesis", "basis_frame_ids", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["observations", "hypotheses"],
    "additionalProperties": False,
}


class MediaUploadError(ValueError):
    """Upload rejected. Carries the HTTP status the API should return."""

    status_code = 400


class UnsupportedVideoError(MediaUploadError):
    status_code = 415


class VideoTooLargeError(MediaUploadError):
    status_code = 413


class MediaProcessingError(Exception):
    """A pipeline stage failed; recorded on the media record, retryable."""


def media_root() -> Path:
    root = get_settings().media_storage_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def mission_media_dir(mission_id: str) -> Path:
    directory = media_root() / "missions" / mission_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_media_path(relative_path: str) -> Path:
    """Resolve a stored relative path, refusing traversal outside the root."""
    root = media_root().resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise MediaUploadError(f"Refusing path outside media storage: {relative_path}")
    return candidate


def save_upload(
    mission_id: str,
    original_filename: str,
    content_type: str,
    data: bytes,
    db_path=None,
) -> Mission:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if mission.status != MissionStatus.CREATED:
        raise LifecycleError(f"Upload needs a CREATED mission (is {mission.status.value}).")
    if mission.media is not None:
        raise LifecycleError("This mission already has an uploaded video.")
    ext = Path(original_filename or "").suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise UnsupportedVideoError(
            f"Unsupported video type {ext or '(none)'}; allowed: {sorted(ALLOWED_VIDEO_EXTENSIONS)}."
        )
    limit = get_settings().max_upload_size_mb * 1_000_000
    if len(data) > limit:
        raise VideoTooLargeError(
            f"Video is {len(data)} bytes (limit {get_settings().max_upload_size_mb} MB)."
        )
    if not data:
        raise MediaUploadError("Empty upload.")

    directory = mission_media_dir(mission_id)
    stored_name = f"video_{uuid.uuid4().hex[:8]}{ext}"
    video_path = directory / stored_name
    video_path.write_bytes(data)
    try:
        probe = _run_ffprobe(video_path)
    except MediaProcessingError:
        video_path.unlink(missing_ok=True)
        raise MediaUploadError("Upload is not a readable video file.") from None
    now = utc_now_iso()
    mission.media = MediaRecord(
        mission_id=mission_id,
        original_filename=Path(original_filename).name[:200],
        video_path=str(video_path.relative_to(media_root())),
        content_type=content_type or "",
        size_bytes=len(data),
        duration_s=probe["duration_s"],
        width=probe["width"],
        height=probe["height"],
        status=MediaStatus.UPLOADED,
        created_at=now,
        updated_at=now,
    )
    mission.status = MissionStatus.VIDEO_UPLOADED
    mission.error = ""
    return save_mission(mission, db_path)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MediaProcessingError(f"Required tool not installed: {cmd[0]}") from exc


def _run_ffprobe(video_path: Path) -> dict[str, Any]:
    probe = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size",
        "-show_entries", "stream=width,height,codec_type",
        "-of", "json", str(video_path),
    ])
    if probe.returncode != 0:
        raise MediaProcessingError(f"ffprobe failed: {probe.stderr.strip()[:500]}")
    try:
        info = json.loads(probe.stdout)
        duration = float(info["format"]["duration"])
        width = height = 0
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
                break
    except (ValueError, KeyError, TypeError) as exc:
        raise MediaProcessingError(f"Unreadable probe output: {exc}") from exc
    if duration <= 0:
        raise MediaProcessingError("Video has no measurable duration.")
    return {"duration_s": duration, "width": width, "height": height}


def run_extraction(media: MediaRecord) -> MediaRecord:
    """Extract timestamped frames + transcription-ready audio (ffmpeg)."""
    video = resolve_media_path(media.video_path)
    if not video.exists():
        raise MediaProcessingError("Source video file is missing from storage.")
    directory = mission_media_dir(media.mission_id)
    stamps = [(i + 0.5) * media.duration_s / N_FRAMES for i in range(N_FRAMES)]
    frames: list[FrameRecord] = []
    for i, stamp in enumerate(stamps, start=1):
        out = directory / f"frame_{i:02d}.jpg"
        conv = _run([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{stamp:.2f}",
            "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out),
        ])
        if conv.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise MediaProcessingError(f"Frame {i} extraction failed @{stamp:.1f}s: {conv.stderr.strip()[:300]}")
        frames.append(FrameRecord(
            id=f"f{i}",
            timestamp_s=round(stamp, 2),
            path=str(out.relative_to(media_root())),
            width=media.width,
            height=media.height,
        ))
    audio_path = directory / "audio_24k_mono.wav"
    conv = _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn",
        "-ac", "1", "-ar", str(TRANSCRIBE_RATE), "-c:a", "pcm_s16le",
        "-map_metadata", "-1", str(audio_path),
    ])
    if conv.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        raise MediaProcessingError(f"Audio extraction failed: {conv.stderr.strip()[:300]}")
    verify = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels",
        "-show_entries", "format=duration", "-of", "json", str(audio_path),
    ])
    if verify.returncode != 0:
        raise MediaProcessingError(f"Audio verification failed: {verify.stderr.strip()[:300]}")
    stream = json.loads(verify.stdout)["streams"][0]
    if not (stream["codec_name"] == "pcm_s16le" and int(stream["channels"]) == 1
            and int(stream["sample_rate"]) in (16000, 24000)):
        raise MediaProcessingError(f"WAV spec mismatch: {verify.stdout.strip()[:300]}")
    if audio_path.stat().st_size > MAX_TRANSCRIBE_BYTES:
        raise MediaProcessingError("Extracted audio exceeds the 32 MB transcription cap.")
    if float(json.loads(verify.stdout)["format"]["duration"]) > MAX_TRANSCRIBE_SECONDS:
        raise MediaProcessingError("Audio exceeds the 10-minute transcription cap.")
    media.frames = frames
    return media


def run_transcription(media: MediaRecord) -> MediaRecord:
    """Transcribe the extracted WAV via Muse Voice Transcribe."""
    settings = get_settings()
    if not settings.transcribe_configured:
        raise SparkUnavailableError(
            "Transcription unavailable: META_MODEL_API_KEY or "
            "MUSE_TRANSCRIBE_MODEL_ID is not configured."
        )
    audio_path = mission_media_dir(media.mission_id) / "audio_24k_mono.wav"
    if not audio_path.exists():
        raise MediaProcessingError("Extracted audio is missing; re-run extraction.")
    request_part = json.dumps({
        "model": settings.muse_transcribe_model_id,
        "audioEncoding": "WAV",
        "mode": TRANSCRIBE_MODE,
    })
    try:
        response = http_post_with_retry(
            f"{settings.meta_model_base_url}/asr/transcribe",
            "Transcribe",
            headers={"Authorization": f"Bearer {settings.meta_model_api_key}",
                     "Accept": "application/json"},
            files={
                "request": (None, request_part, "application/json"),
                "audio": (audio_path.name, audio_path.read_bytes(), "audio/wav"),
            },
            timeout=httpx.Timeout(
                connect=TRANSCRIBE_CONNECT_S, read=TRANSCRIBE_READ_S,
                write=TRANSCRIBE_WRITE_S, pool=TRANSCRIBE_CONNECT_S,
            ),
        )
    except SparkError as exc:
        raise MediaProcessingError(f"Transcription failed: {exc}") from exc
    payload = response.json()
    if "transcript" not in payload:
        raise MediaProcessingError(f"Transcription response has no transcript: {response.text[:800]}")
    turns = [
        TranscriptTurn(
            index=i,
            start_ms=int(turn.get("startMs", 0) or 0),
            end_ms=int(turn.get("endMs", 0) or 0),
            transcript=str(turn.get("transcript", "")),
            speaker=str(turn.get("speaker", "") or ""),
        )
        for i, turn in enumerate(payload.get("turns", []) or [], start=1)
    ]
    media.transcript = TranscriptRecord(
        text=str(payload.get("transcript", "")),
        turns=turns,
        audio_duration_ms=int(payload.get("audioDurationMs", 0) or 0),
        model_id=settings.muse_transcribe_model_id,
    )
    return media


def run_visual_analysis(media: MediaRecord) -> MediaRecord:
    """Describe frames with Muse Spark; drop unresolvable frame references."""
    settings = get_settings()
    images: list[tuple[str, str, bytes]] = []
    for frame in media.frames:
        path = resolve_media_path(frame.path)
        if not path.exists():
            raise MediaProcessingError(f"Extracted frame {frame.id} is missing; re-run extraction.")
        images.append((f"Frame {frame.id} at {frame.timestamp_s:.1f}s in the source video.",
                       "image/jpeg", path.read_bytes()))
    if not images:
        raise MediaProcessingError("No frames available for visual analysis.")
    data, usage = responses_structured_multimodal(
        schema_name="FrameAnalysis",
        schema=VISION_SCHEMA,
        instruction=VISION_SYSTEM,
        images=images,
        effort="low",
        timeout_s=VISION_TIMEOUT_S,
    )
    try:
        observations = [VisualObservation.model_validate(o) for o in data.get("observations", [])]
        hypotheses = [VisualHypothesis.model_validate(h) for h in data.get("hypotheses", [])]
    except ValidationError as exc:
        raise MediaProcessingError(f"Visual analysis output failed validation: {exc}") from exc
    known = {frame.id for frame in media.frames}
    kept_obs, dropped_obs = [], 0
    for obs in observations:
        refs = [fid for fid in obs.frame_ids if fid in known]
        if refs:
            obs.frame_ids = refs
            kept_obs.append(obs)
        else:
            dropped_obs += 1
    kept_hyp, dropped_hyp = [], 0
    for hyp in hypotheses:
        refs = [fid for fid in hyp.basis_frame_ids if fid in known]
        if refs:
            hyp.basis_frame_ids = refs
            kept_hyp.append(hyp)
        else:
            dropped_hyp += 1
    if dropped_obs or dropped_hyp:
        media.notes.append(
            f"Dropped {dropped_obs} observation(s) and {dropped_hyp} hypothese(s) "
            "with unresolvable frame references."
        )
    media.observations = kept_obs
    media.hypotheses = kept_hyp
    media.vision_model_id = settings.muse_spark_model_id
    media.vision_usage = dict(usage)
    return media


def synthesize_evidence(mission: Mission) -> Mission:
    """Convert the media record into categorized mission evidence.

    OBSERVED from frames, STATED from transcript turns, INFERRED from
    model hypotheses — every item carries its timestamped source.
    """
    media = mission.media
    assert media is not None
    by_id = {frame.id: frame for frame in media.frames}

    def frame_ref(fid: str) -> str:
        frame = by_id.get(fid)
        return f"frame {fid} @ {frame.timestamp_s:.1f}s" if frame else f"frame {fid}"

    evidence: list[EvidenceItem] = []
    for obs in media.observations:
        evidence.append(EvidenceItem(
            category=EvidenceCategory.OBSERVED,
            text=obs.observation,
            source="; ".join(frame_ref(fid) for fid in obs.frame_ids),
        ))
    if media.transcript is not None:
        for turn in media.transcript.turns:
            if not turn.transcript.strip():
                continue
            evidence.append(EvidenceItem(
                category=EvidenceCategory.STATED,
                text=turn.transcript.strip(),
                source=f"transcript turn {turn.index} @ {turn.start_ms / 1000:.0f}-{turn.end_ms / 1000:.0f}s",
            ))
        if not media.transcript.turns and media.transcript.text.strip():
            evidence.append(EvidenceItem(
                category=EvidenceCategory.STATED,
                text=media.transcript.text.strip(),
                source="full transcript (no turn segments)",
            ))
    for hyp in media.hypotheses:
        evidence.append(EvidenceItem(
            category=EvidenceCategory.INFERRED,
            text=hyp.hypothesis,
            source=f"hypothesis from {'; '.join(frame_ref(fid) for fid in hyp.basis_frame_ids)}",
        ))
    mission.evidence = evidence
    media.status = MediaStatus.READY
    media.error = ""
    return mission


def _extraction_complete(media: MediaRecord) -> bool:
    if len(media.frames) != N_FRAMES:
        return False
    try:
        paths = [resolve_media_path(f.path) for f in media.frames]
    except MediaUploadError:
        return False
    audio = mission_media_dir(media.mission_id) / "audio_24k_mono.wav"
    return all(p.exists() for p in paths) and audio.exists()


def process_media(mission_id: str, db_path=None) -> Mission:
    """Run the analysis pipeline to READY, resuming incomplete stages.

    Idempotent when already READY. Stage failures persist FAILED + the
    real error and return the mission (safe to retry via re-analysis).
    """
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if mission.media is None:
        raise LifecycleError("No video uploaded for this mission.")
    if mission.media.is_ready:
        return mission
    if mission.status not in (MissionStatus.VIDEO_UPLOADED, MissionStatus.ANALYZING):
        raise LifecycleError(
            f"Analysis needs an uploaded, unanalyzed video (mission is {mission.status.value})."
        )
    mission.status = MissionStatus.ANALYZING
    media = mission.media
    try:
        if not _extraction_complete(media):
            media.status = MediaStatus.EXTRACTING
            mission.updated_at = media.updated_at = utc_now_iso()
            save_mission(mission, db_path)
            media = run_extraction(media)
        if media.transcript is None:
            media.status = MediaStatus.TRANSCRIBING
            mission.updated_at = media.updated_at = utc_now_iso()
            save_mission(mission, db_path)
            media = run_transcription(media)
        if not media.vision_model_id:
            media.status = MediaStatus.ANALYZING
            mission.updated_at = media.updated_at = utc_now_iso()
            save_mission(mission, db_path)
            media = run_visual_analysis(media)
        mission.media = media
        mission = synthesize_evidence(mission)
        mission.error = ""
    except (MediaProcessingError, SparkError, ValidationError, OSError) as exc:
        media.status = MediaStatus.FAILED
        media.error = f"{type(exc).__name__}: {exc}"[:1000]
        mission.media = media
        mission.error = f"Video analysis failed: {exc}"[:1000]
    mission.updated_at = media.updated_at = utc_now_iso()
    return save_mission(mission, db_path)


def get_frame_bytes(mission_id: str, frame_id: str, db_path=None) -> bytes:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if mission.media is None:
        raise KeyError(f"No media for mission: {mission_id}")
    for frame in mission.media.frames:
        if frame.id == frame_id:
            path = resolve_media_path(frame.path)
            if path.exists():
                return path.read_bytes()
            raise KeyError(f"Frame file missing: {frame_id}")
    raise KeyError(f"Unknown frame: {frame_id}")
